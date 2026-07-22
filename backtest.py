"""Backtesting engine for mechanical scalping strategies on Binance data.

Replays parameterized rule-based strategies (StochRSI + volume + trend
filters, TP/SL brackets) over historical 5m candles, with the same futures
fee model as the paper-trading evaluator. Train/test split to detect
overfitting.

Usage:
    python backtest.py --symbol BNBUSDT --days 90
"""
from __future__ import annotations

import argparse
import itertools
import time
from pathlib import Path
from dataclasses import dataclass, field

import pandas as pd
import pandas_ta as ta
import requests

FUTURES_FEE_RT_PCT = 0.10   # round trip, % of notional (0.05 per side)
MAX_HOLD_BARS_5M = 96       # 8h — same cap as the live evaluator


def fetch_5m_history(symbol: str, days: int) -> pd.DataFrame:
    """Full 5m history for the last N days (paginated)."""
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    rows: list = []
    cursor = start
    while cursor < end:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": "5m",
                    "startTime": cursor, "limit": 1000},
            timeout=20,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        cursor = batch[-1][0] + 300_000
        if len(batch) < 1000:
            break
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["time", "open", "high", "low", "close", "volume"]
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df.reset_index(drop=True)


def add_indicators(df5: pd.DataFrame) -> pd.DataFrame:
    """StochRSI on 5m/15m/30m (resampled), volume ratio, 30m trend SMA."""
    out = df5.copy().set_index("time")

    def stoch_k_d(close: pd.Series) -> pd.DataFrame:
        s = ta.stochrsi(close, length=14, rsi_length=14, k=3, d=3)
        if s is None:
            return pd.DataFrame(index=close.index, columns=["k", "d"])
        s.columns = ["k", "d"]
        return s

    k5 = stoch_k_d(out["close"])
    out["k5"], out["d5"] = k5["k"], k5["d"]

    for tf, minutes in (("15", 15), ("30", 30)):
        res = out["close"].resample(f"{minutes}min").last().dropna()
        kd = stoch_k_d(res)
        out[f"k{tf}"] = kd["k"].reindex(out.index, method="ffill")
        out[f"d{tf}"] = kd["d"].reindex(out.index, method="ffill")

    c30 = out["close"].resample("30min").last().dropna()
    sma20_30 = c30.rolling(20).mean()
    out["trend30"] = (c30 > sma20_30).reindex(out.index, method="ffill")

    out["vol_ratio"] = out["volume"] / out["volume"].rolling(20).mean()
    out["ema20"] = ta.ema(out["close"], length=20)

    # --- familias lentas: señales sobre velas 1h, tendencia 4h ---
    h1 = out.resample("1h").agg({"open": "first", "high": "max",
                                 "low": "min", "close": "last"}).dropna()
    out["rsi1h"] = ta.rsi(h1["close"], length=14).reindex(out.index, method="ffill")
    for lb in (24, 48):
        out[f"h1hh{lb}"] = h1["high"].rolling(lb).max().shift(1).reindex(
            out.index, method="ffill")
        out[f"h1ll{lb}"] = h1["low"].rolling(lb).min().shift(1).reindex(
            out.index, method="ffill")
    c4 = out["close"].resample("4h").last().dropna()
    out["trend4h"] = (c4 > c4.rolling(20).mean()).reindex(out.index, method="ffill")
    # último bar 5m de cada hora (cierre de la vela 1h)
    out["h1_close"] = out.index.minute == 55
    # Rolling range extremes for breakout signals (exclude current bar).
    for lb in (48, 96):
        out[f"hh{lb}"] = out["high"].rolling(lb).max().shift(1)
        out[f"ll{lb}"] = out["low"].rolling(lb).min().shift(1)
    return out.reset_index()


@dataclass
class Params:
    strategy: str = "meanrev"  # meanrev | breakout
    oversold: float = 15      # 15m StochRSI extreme threshold (meanrev)
    lookback: int = 48        # range bars for breakout (5m bars)
    vol_min: float = 0.8      # minimum 5m volume ratio at signal
    tp_pct: float = 0.8       # take profit distance (% of price)
    sl_pct: float = 0.4       # stop distance (% of price)
    regime: str = "none"      # none | with_trend | counter_trend
    direction: str = "both"   # long | short | both
    rsi_th: float = 30        # umbral RSI 1h (slowmr)
    max_hold: int = MAX_HOLD_BARS_5M  # barras 5m máximas en posición


@dataclass
class Result:
    params: Params
    trades: int = 0
    wins: int = 0
    net_r: float = 0.0
    net_pct_sum: float = 0.0
    equity_curve: list[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    @property
    def max_drawdown_r(self) -> float:
        peak, mdd, run = 0.0, 0.0, 0.0
        for r in self.equity_curve:
            run += r
            peak = max(peak, run)
            mdd = min(mdd, run - peak)
        return mdd


def run_backtest(df: pd.DataFrame, p: Params) -> Result:
    """Sequential replay: one position at a time, signal at candle close."""
    res = Result(params=p)
    fee_r = FUTURES_FEE_RT_PCT / p.sl_pct  # fees expressed in R units

    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    k5 = df["k5"].to_numpy()
    d5 = df["d5"].to_numpy()
    k15 = df["k15"].to_numpy()
    vol = df["vol_ratio"].to_numpy()
    trend = df["trend30"].to_numpy()
    ema = df["ema20"].to_numpy()
    hh = df[f"hh{p.lookback}"].to_numpy() if f"hh{p.lookback}" in df else None
    ll = df[f"ll{p.lookback}"].to_numpy() if f"ll{p.lookback}" in df else None
    slow = p.strategy in ("slowtrend", "slowmr")
    if slow:
        h1c = df["h1_close"].to_numpy()
        rsi1h = df["rsi1h"].to_numpy()
        lbh = p.lookback if p.lookback in (24, 48) else 24
        h1hh = df[f"h1hh{lbh}"].to_numpy()
        h1ll = df[f"h1ll{lbh}"].to_numpy()
        trend = df["trend4h"].to_numpy()  # las lentas filtran con 4h

    import math
    i, n = 30, len(df)
    while i < n - 1:
        if math.isnan(k15[i]) or math.isnan(k5[i]) or math.isnan(vol[i]):
            i += 1
            continue

        if p.strategy == "breakout":
            if hh is None or math.isnan(hh[i]) or math.isnan(ll[i]):
                i += 1
                continue
            long_sig = close[i] > hh[i] and vol[i] >= p.vol_min
            short_sig = close[i] < ll[i] and vol[i] >= p.vol_min
        elif p.strategy == "pullback":
            if math.isnan(ema[i]):
                i += 1
                continue
            long_sig = (bool(trend[i]) and low[i] <= ema[i] and close[i] > ema[i]
                        and k5[i] > d5[i] and vol[i] >= p.vol_min)
            short_sig = (not bool(trend[i]) and high[i] >= ema[i] and close[i] < ema[i]
                         and k5[i] < d5[i] and vol[i] >= p.vol_min)
        elif p.strategy == "slowtrend":
            if not h1c[i] or math.isnan(h1hh[i]) or math.isnan(h1ll[i]):
                i += 1
                continue
            long_sig = close[i] > h1hh[i]
            short_sig = close[i] < h1ll[i]
        elif p.strategy == "slowmr":
            if not h1c[i] or math.isnan(rsi1h[i]):
                i += 1
                continue
            long_sig = rsi1h[i] < p.rsi_th
            short_sig = rsi1h[i] > 100 - p.rsi_th
        else:  # meanrev
            long_sig = (k15[i] < p.oversold and k5[i] > d5[i]
                        and k5[i - 1] <= d5[i - 1] and vol[i] >= p.vol_min)
            short_sig = (k15[i] > 100 - p.oversold and k5[i] < d5[i]
                         and k5[i - 1] >= d5[i - 1] and vol[i] >= p.vol_min)

        if p.regime == "with_trend":
            long_sig = long_sig and bool(trend[i])
            short_sig = short_sig and not bool(trend[i])
        elif p.regime == "counter_trend":
            long_sig = long_sig and not bool(trend[i])
            short_sig = short_sig and bool(trend[i])
        if p.direction == "long":
            short_sig = False
        elif p.direction == "short":
            long_sig = False

        if not (long_sig or short_sig):
            i += 1
            continue

        is_long = long_sig
        entry = close[i]
        tp = entry * (1 + p.tp_pct / 100) if is_long else entry * (1 - p.tp_pct / 100)
        sl = entry * (1 - p.sl_pct / 100) if is_long else entry * (1 + p.sl_pct / 100)

        gross_r = None
        j_end = min(i + 1 + p.max_hold, n)
        j = j_end - 1
        for j in range(i + 1, j_end):
            hit_sl = low[j] <= sl if is_long else high[j] >= sl
            hit_tp = high[j] >= tp if is_long else low[j] <= tp
            if hit_sl:          # conservative: SL first if both touch
                gross_r = -1.0
                break
            if hit_tp:
                gross_r = p.tp_pct / p.sl_pct
                break
        if gross_r is None:     # time exit at market
            move = (close[j] - entry) if is_long else (entry - close[j])
            gross_r = (move / entry * 100) / p.sl_pct

        net = gross_r - fee_r
        res.trades += 1
        res.wins += 1 if net > 0 else 0
        res.net_r += net
        res.net_pct_sum += net * p.sl_pct
        res.equity_curve.append(net)
        i = j + 1  # no overlapping positions

    return res


def sweep(df_train: pd.DataFrame, df_test: pd.DataFrame,
          grid: dict) -> list[tuple[Result, Result]]:
    """Backtest every combination on train, re-run survivors on test."""
    keys = list(grid)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*grid.values())]
    results = []
    for combo in combos:
        p = Params(**combo)
        train = run_backtest(df_train, p)
        if train.trades >= 10:  # need a minimum sample
            test = run_backtest(df_test, p)
            results.append((train, test))
    results.sort(key=lambda pair: pair[0].net_r, reverse=True)
    return results


def fmt(r: Result) -> str:
    return (f"{r.trades:>4} trades  wr {r.win_rate:>5.0%}  "
            f"netR {r.net_r:>+7.1f}  mdd {r.max_drawdown_r:>+6.1f}R")


GRIDS = {
    "meanrev": {
        "strategy": ["meanrev"],
        "oversold": [10, 15, 20],
        "vol_min": [0.5, 1.0],
        "tp_pct": [0.5, 0.8, 1.2],
        "sl_pct": [0.3, 0.5],
        "regime": ["none", "with_trend", "counter_trend"],
    },
    "breakout": {
        "strategy": ["breakout"],
        "lookback": [48, 96],
        "vol_min": [1.0, 1.5, 2.0],
        "tp_pct": [1.0, 1.5, 2.5],
        "sl_pct": [0.5, 0.75],
        "regime": ["none", "with_trend"],
    },
    "slowtrend": {
        "strategy": ["slowtrend"],
        "lookback": [24, 48],
        "tp_pct": [2.0, 3.0, 4.0],
        "sl_pct": [1.0, 1.5],
        "regime": ["none", "with_trend"],
        "max_hold": [288, 576],
    },
    "slowmr": {
        "strategy": ["slowmr"],
        "rsi_th": [25, 30],
        "tp_pct": [1.5, 2.5],
        "sl_pct": [0.8, 1.2],
        "regime": ["none", "with_trend", "counter_trend"],
        "max_hold": [288],
    },
    "pullback": {
        "strategy": ["pullback"],
        "vol_min": [0.5, 1.0],
        "tp_pct": [0.8, 1.2, 2.0],
        "sl_pct": [0.4, 0.6],
        "regime": ["none"],
    },
}


def load_symbol(symbol: str, days: int, cache_dir: Path) -> pd.DataFrame:
    """Fetch + indicator-annotate one symbol, with a 12h disk cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{symbol}_{days}d.parquet"
    if cache.exists() and time.time() - cache.stat().st_mtime < 12 * 3600:
        return pd.read_parquet(cache)
    df = add_indicators(fetch_5m_history(symbol, days))
    df.to_parquet(cache)
    return df


def param_key(p: Params) -> tuple:
    return (p.strategy, p.oversold, p.lookback, p.vol_min, p.tp_pct,
            p.sl_pct, p.regime, p.direction, p.rsi_th, p.max_hold)


def describe(p: Params) -> str:
    core = f"vol≥{p.vol_min} tp={p.tp_pct} sl={p.sl_pct} {p.regime}"
    if p.strategy == "breakout":
        return f"brk lb={p.lookback} {core}"
    if p.strategy == "pullback":
        return f"pb {core}"
    if p.strategy == "slowtrend":
        return f"st lb={p.lookback}h tp={p.tp_pct} sl={p.sl_pct} {p.regime} mh={p.max_hold//12}h"
    if p.strategy == "slowmr":
        return f"smr rsi={p.rsi_th:g} tp={p.tp_pct} sl={p.sl_pct} {p.regime} mh={p.max_hold//12}h"
    return f"mr os={p.oversold:g} {core}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BNBUSDT",
                    help="lista separada por comas, o 'watchlist'")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--families", default="meanrev,breakout,pullback")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    if args.symbols == "watchlist":
        from config import load_config
        cfg = load_config("config.yaml")
        symbols = [i.symbol for i in cfg.watchlist if i.type == "crypto"]
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]

    cache_dir = Path("data/bt_cache")
    frames: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    for sym in symbols:
        print(f"cargando {sym}…", flush=True)
        df = load_symbol(sym, args.days, cache_dir)
        split = int(len(df) * 2 / 3)
        frames[sym] = (df.iloc[:split],
                       df.iloc[split:].reset_index(drop=True))

    for family in args.families.split(","):
        grid = GRIDS[family.strip()]
        keys = list(grid)
        combos = [dict(zip(keys, v)) for v in itertools.product(*grid.values())]
        agg: dict[tuple, dict] = {}
        for combo in combos:
            p = Params(**combo)
            k = param_key(p)
            a = agg.setdefault(k, {"p": p, "train_r": 0.0, "test_r": 0.0,
                                   "trades": 0, "test_trades": 0, "pos_syms": 0,
                                   "syms": 0})
            for sym, (dtr, dte) in frames.items():
                tr = run_backtest(dtr, p)
                if tr.trades < 5:
                    continue
                te = run_backtest(dte, p)
                a["train_r"] += tr.net_r
                a["test_r"] += te.net_r
                a["trades"] += tr.trades
                a["test_trades"] += te.trades
                a["syms"] += 1
                if te.net_r > 0:
                    a["pos_syms"] += 1

        ranked = sorted(agg.values(), key=lambda a: a["train_r"], reverse=True)
        print(f"\n=== familia {family} — {len(symbols)} símbolos, "
              f"selección por TRAIN, juicio por TEST ===")
        print(f"{'params':<44} | {'trainR':>8} {'trades':>6} | "
              f"{'testR':>8} {'trades':>6} {'sym+':>5}")
        for a in ranked[:args.top]:
            if a["syms"] == 0:
                continue
            print(f"{describe(a['p']):<44} | {a['train_r']:>+8.1f} {a['trades']:>6} | "
                  f"{a['test_r']:>+8.1f} {a['test_trades']:>6} "
                  f"{a['pos_syms']:>3}/{a['syms']}")


if __name__ == "__main__":
    main()
