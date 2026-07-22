"""A5 (SMA 10/40 daily L/S) backtesteada sobre los stocks/ETFs de la
watchlist con datos diarios de IBKR.

Corre con dos modelos de comisiones para mostrar la sensibilidad:
- IBKR Pro con posiciones de $100: ~2% round trip (el mínimo de $1 domina)
- broker/tamaño donde las fees son 0.1% RT (posiciones grandes o comisión 0)

Nota: los shorts de stocks requieren margen y disponibilidad de préstamo;
los resultados L/S son teóricos en cash account. Se reporta también L-only.

Uso: python stocks_daily_trend.py   (requiere IB Gateway conectado)
"""
from __future__ import annotations

import pandas as pd

import daily_trend
from config import load_config
from fetchers.ibkr import _get_ib, disconnect

FOLDS = [(0.5, 0.667), (0.667, 0.833), (0.833, 1.0)]


def fetch_daily_stooq(symbol: str) -> pd.DataFrame:
    """Fallback gratuito: velas diarias de Yahoo Finance (chart API)."""
    import requests

    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"range": "2y", "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({
        "time": pd.to_datetime(res["timestamp"], unit="s", utc=True),
        "open": q["open"], "high": q["high"],
        "low": q["low"], "close": q["close"], "volume": q["volume"],
    }).dropna()
    return df.reset_index(drop=True)


def fetch_daily_ibkr(symbol: str, ibkr_cfg) -> pd.DataFrame:
    from ib_insync import Stock

    ib = _get_ib(ibkr_cfg)
    contract = ib.qualifyContracts(Stock(symbol, "SMART", "USD"))[0]
    bars = ib.reqHistoricalData(
        contract, endDateTime="", durationStr="2 Y", barSizeSetting="1 day",
        whatToShow="TRADES", useRTH=True, formatDate=2,
    )
    df = pd.DataFrame(
        [{"time": b.date, "open": b.open, "high": b.high,
          "low": b.low, "close": b.close, "volume": b.volume} for b in bars]
    )
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def table(data: dict, fee_rt: float, allow_short: bool) -> None:
    daily_trend.FEE_RT_PCT = fee_rt
    label = "L/S" if allow_short else "L-only"
    print(f"\n--- sma 10/40 {label} | fee RT {fee_rt}% ---")
    print(f"{'variante':<20} | fold1 (tr|te) | fold2 (tr|te) | fold3 (tr|te) | full")
    cells, full_net, full_tr = [], 0.0, 0
    for a, b in FOLDS:
        tr_net = te_net = 0.0
        for s, df in data.items():
            n = len(df)
            r1 = daily_trend.run(df.iloc[: int(n * a)], "sma", 10, 40, allow_short)
            r2 = daily_trend.run(
                df.iloc[int(n * a): int(n * b)].reset_index(drop=True),
                "sma", 10, 40, allow_short)
            tr_net += r1[0]
            te_net += r2[0]
        cells.append(f"{tr_net:+7.0f}%|{te_net:+6.0f}%")
    for s, df in data.items():
        r = daily_trend.run(df, "sma", 10, 40, allow_short)
        full_net += r[0]
        full_tr += r[1]
    print(f"{'sma 10/40':<20} | " + " | ".join(cells) +
          f" | {full_net:+7.0f}% ({full_tr}t)")

    # benchmark
    cells, full_bh = [], 0.0
    for a, b in FOLDS:
        tr_bh = te_bh = 0.0
        for s, df in data.items():
            n = len(df)
            c = df["close"]
            tr_bh += (c.iloc[int(n * a) - 1] / c.iloc[0] - 1) * 100
            te_bh += (c.iloc[int(n * b) - 1] / c.iloc[int(n * a)] - 1) * 100
        cells.append(f"{tr_bh:+7.0f}%|{te_bh:+6.0f}%")
    for s, df in data.items():
        full_bh += (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    print(f"{'BUY & HOLD':<20} | " + " | ".join(cells) + f" | {full_bh:+7.0f}%")


def main() -> None:
    cfg = load_config("config.yaml")
    symbols = [i.symbol for i in cfg.watchlist if i.type == "stock"]
    data = {}
    use_stooq = False
    for s in symbols:
        try:
            if use_stooq:
                df = fetch_daily_stooq(s)
            else:
                try:
                    df = fetch_daily_ibkr(s, cfg.ibkr)
                except Exception:
                    print("(IBKR no disponible — usando Stooq para todos)")
                    use_stooq = True
                    df = fetch_daily_stooq(s)
        except Exception as exc:  # noqa: BLE001
            print(f"({s}: error {exc})")
            continue
        if len(df) >= 200:
            data[s] = df
        else:
            print(f"({s}: sólo {len(df)} días, excluido)")
    disconnect()
    print(f"{len(data)} símbolos con ≥200 días de historia")

    for fee in (2.0, 0.1):
        for short in (False, True):
            table(data, fee, short)

    # por símbolo con fee realista, L-only
    daily_trend.FEE_RT_PCT = 2.0
    print("\n--- por símbolo (fee 2% RT, L-only, 2 años) ---")
    print(f"{'símbolo':<8} {'estrategia':>11} {'buy&hold':>10} {'trades':>7}")
    beat = 0
    for s, df in data.items():
        net, tr, _ = daily_trend.run(df, "sma", 10, 40, False)
        bh = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
        mark = "✓" if net > bh else " "
        beat += 1 if net > bh else 0
        print(f"{s:<8} {net:>+10.0f}% {bh:>+9.0f}% {tr:>7} {mark}")
    print(f"gana al B&H en {beat}/{len(data)}")


if __name__ == "__main__":
    main()
