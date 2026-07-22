"""Trend following en velas DIARIAS (la escala donde la literatura encuentra
edge; el intradía ya demostró no tenerlo).

Estrategias: cruce SMA 10/40 y Donchian 20d, long-only y long/short, sobre
2 años de velas 1d de Binance. Fees futuros 0.1% RT por trade (despreciable
a esta escala). Walk-forward de 3 folds.

Uso: python daily_trend.py
"""
from __future__ import annotations

import itertools
import time

import pandas as pd
import requests

from config import load_config

FEE_RT_PCT = 0.10
DAYS = 720
FOLDS = [(0.5, 0.667), (0.667, 0.833), (0.833, 1.0)]


def fetch_daily(symbol: str, days: int = DAYS) -> pd.DataFrame:
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    r = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": symbol, "interval": "1d",
                "startTime": start, "limit": 1000},
        timeout=20,
    )
    r.raise_for_status()
    df = pd.DataFrame(r.json()).iloc[:, :6]
    df.columns = ["time", "open", "high", "low", "close", "volume"]
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df


def run(df: pd.DataFrame, kind: str, fast: int, slow: int,
        allow_short: bool) -> tuple[float, int, int]:
    """Devuelve (net_pct_total, trades, wins). Señal al cierre, pos al día sig."""
    close = df["close"].to_numpy()
    if kind == "sma":
        f = df["close"].rolling(fast).mean().to_numpy()
        s = df["close"].rolling(slow).mean().to_numpy()
        want = pd.Series(0, index=df.index)
        for i in range(len(df)):
            if pd.isna(s[i]):
                continue
            want.iloc[i] = 1 if f[i] > s[i] else (-1 if allow_short else 0)
    else:  # donchian
        hh = df["high"].rolling(slow).max().shift(1)
        ll = df["low"].rolling(slow).min().shift(1)
        mid = (hh + ll) / 2
        want = pd.Series(0, index=df.index)
        pos = 0
        for i in range(len(df)):
            if pd.isna(hh.iloc[i]):
                want.iloc[i] = 0
                continue
            if close[i] > hh.iloc[i]:
                pos = 1
            elif close[i] < ll.iloc[i]:
                pos = -1 if allow_short else 0
            elif (pos == 1 and close[i] < mid.iloc[i]) or \
                 (pos == -1 and close[i] > mid.iloc[i]):
                pos = 0
            want.iloc[i] = pos

    net, trades, wins = 0.0, 0, 0
    pos, entry = 0, 0.0
    for i in range(1, len(df)):
        w = int(want.iloc[i - 1])          # se opera al cierre siguiente
        if w != pos:
            if pos != 0:                    # cerrar
                ret = (close[i] - entry) / entry * 100 * pos - FEE_RT_PCT
                net += ret
                trades += 1
                wins += 1 if ret > 0 else 0
            pos, entry = w, close[i]
    if pos != 0:                            # mark-to-market final
        ret = (close[-1] - entry) / entry * 100 * pos - FEE_RT_PCT
        net += ret
        trades += 1
        wins += 1 if ret > 0 else 0
    return net, trades, wins


def main() -> None:
    cfg = load_config("config.yaml")
    symbols = [i.symbol for i in cfg.watchlist if i.type == "crypto"]
    data = {}
    for s in symbols:
        df = fetch_daily(s)
        if len(df) >= 200:
            data[s] = df
        else:
            print(f"({s}: sólo {len(df)} días, excluido)")
    print(f"{len(data)} símbolos con historia suficiente\n")

    variants = [
        ("sma", 10, 40, False), ("sma", 10, 40, True),
        ("sma", 20, 100, False), ("sma", 20, 100, True),
        ("don", 0, 20, False), ("don", 0, 20, True),
        ("don", 0, 55, False), ("don", 0, 55, True),
    ]
    print(f"{'variante':<22} | fold1 (tr|te) | fold2 (tr|te) | fold3 (tr|te) | full")
    for kind, fa, sl, sh in variants:
        cells = []
        full_net, full_tr = 0.0, 0
        for a, b in FOLDS:
            tr_net = te_net = 0.0
            tr_n = te_n = 0
            for s, df in data.items():
                n = len(df)
                r1 = run(df.iloc[: int(n * a)], kind, fa, sl, sh)
                r2 = run(df.iloc[int(n * a): int(n * b)].reset_index(drop=True),
                         kind, fa, sl, sh)
                tr_net += r1[0]; tr_n += r1[1]
                te_net += r2[0]; te_n += r2[1]
            cells.append(f"{tr_net:+7.0f}%|{te_net:+6.0f}%")
        for s, df in data.items():
            r = run(df, kind, fa, sl, sh)
            full_net += r[0]; full_tr += r[1]
        tag = f"{kind} {fa}/{sl}" + (" L/S" if sh else " L")
        print(f"{tag:<22} | " + " | ".join(cells) +
              f" | {full_net:+8.0f}% ({full_tr}t)")

    # Benchmark buy & hold por fold (mismo agregado)
    cells = []
    full_bh = 0.0
    for a, b in FOLDS:
        tr_bh = te_bh = 0.0
        for s, df in data.items():
            n = len(df)
            c = df["close"]
            tr_bh += (c.iloc[int(n*a)-1] / c.iloc[0] - 1) * 100
            te_bh += (c.iloc[int(n*b)-1] / c.iloc[int(n*a)] - 1) * 100
        cells.append(f"{tr_bh:+7.0f}%|{te_bh:+6.0f}%")
    for s, df in data.items():
        full_bh += (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    print(f"{'BUY & HOLD (bench)':<22} | " + " | ".join(cells) +
          f" | {full_bh:+8.0f}%")


if __name__ == "__main__":
    main()
