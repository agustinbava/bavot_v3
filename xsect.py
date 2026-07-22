"""Familia cross-sectional: reversión de fuerza relativa entre las 10 cryptos.

Cada hora, rankea los retornos trailing 24h. Si el spread líder-rezagado
supera un umbral, abre el par (long rezagado / short líder) y lo mantiene H
horas. Fees de futuros 0.1% RT por pata. Walk-forward de 3 folds.

Uso: python xsect.py
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pandas as pd

from backtest import load_symbol
from config import load_config

DAYS = 180
FEE_PAIR_PCT = 0.4   # 0.1% RT x 2 patas, sobre nocional de cada pata -> en pct del par
FOLDS = [(0.5, 0.667), (0.667, 0.833), (0.833, 1.0)]


def hourly_closes(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    cols = {}
    for s, df in frames.items():
        cols[s] = df.set_index("time")["close"].resample("1h").last()
    return pd.DataFrame(cols).dropna()


def run(px: pd.DataFrame, spread_min: float, hold_h: int) -> tuple[float, int, int]:
    """Devuelve (net_pct_total, trades, wins). Una posición por vez."""
    ret24 = px.pct_change(24) * 100
    net_total, trades, wins = 0.0, 0, 0
    i = 24
    n = len(px)
    while i < n - hold_h:
        row = ret24.iloc[i]
        leader, laggard = row.idxmax(), row.idxmin()
        spread = row[leader] - row[laggard]
        if spread < spread_min:
            i += 1
            continue
        p0 = px.iloc[i]
        p1 = px.iloc[i + hold_h]
        long_ret = (p1[laggard] / p0[laggard] - 1) * 100
        short_ret = (p1[leader] / p0[leader] - 1) * 100
        net = (long_ret - short_ret) / 2 - FEE_PAIR_PCT / 2
        net_total += net
        trades += 1
        wins += 1 if net > 0 else 0
        i += hold_h  # sin solapamiento
    return net_total, trades, wins


def main() -> None:
    cfg = load_config("config.yaml")
    symbols = [i.symbol for i in cfg.watchlist if i.type == "crypto"]
    frames = {s: load_symbol(s, DAYS, Path("data/bt_cache")) for s in symbols}
    px = hourly_closes(frames)
    print(f"{len(px)} horas alineadas de {len(symbols)} símbolos\n")

    grid = list(itertools.product([3.0, 5.0, 8.0], [12, 24, 48]))
    print(f"{'params':<24} | fold1 (tr|te) | fold2 (tr|te) | fold3 (tr|te)")
    for spread_min, hold_h in grid:
        cells = []
        for a, b in FOLDS:
            tr = px.iloc[: int(len(px) * a)]
            te = px.iloc[int(len(px) * a): int(len(px) * b)]
            tr_net, tr_n, _ = run(tr, spread_min, hold_h)
            te_net, te_n, _ = run(te, spread_min, hold_h)
            cells.append(f"{tr_net:+6.1f}%({tr_n:>3}) | {te_net:+6.1f}%({te_n:>2})")
        print(f"spread≥{spread_min:g}% hold={hold_h:>2}h    | " + " | ".join(cells))


if __name__ == "__main__":
    main()
