"""Backtest de carry de funding: spot long + perp short, cobrando funding.

Estrategia delta-neutral documentada: mientras el funding del perpetuo es
positivo, el short del perp COBRA el funding cada 8h. Riesgo direccional ~0.
Datos: historial real de funding de Binance USDT-M (público).

Uso: python funding_carry.py
"""
from __future__ import annotations

import time

import pandas as pd
import requests

from config import load_config

ROTATION_COST_PCT = 0.30   # entrar+salir del par spot+perp (~0.1 spot y 0.05 perp por lado)
LOOKBACK_DAYS = 330        # ~límite de 1000 registros a 3/día


def fetch_funding(symbol: str) -> pd.Series:
    """Historial de funding rate (fracción por evento de 8h)."""
    end = int(time.time() * 1000)
    start = end - LOOKBACK_DAYS * 86_400_000
    out = []
    cursor = start
    while cursor < end:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={"symbol": symbol, "startTime": cursor, "limit": 1000},
            timeout=20,
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        out.extend(rows)
        cursor = rows[-1]["fundingTime"] + 1
        if len(rows) < 1000:
            break
    if not out:
        return pd.Series(dtype=float)
    s = pd.Series(
        [float(x["fundingRate"]) for x in out],
        index=pd.to_datetime([x["fundingTime"] for x in out], unit="ms", utc=True),
    )
    return s[~s.index.duplicated()]


def main() -> None:
    cfg = load_config("config.yaml")
    symbols = [i.symbol for i in cfg.watchlist if i.type == "crypto"]

    print(f"{'símbolo':<10} {'días':>5} {'APR siempre-on':>14} {'APR filtrado*':>14} "
          f"{'% tiempo en pos':>15} {'rotaciones':>10}")
    total_always, total_filt, count = 0.0, 0.0, 0
    for sym in symbols:
        try:
            f = fetch_funding(sym)
        except Exception as exc:  # noqa: BLE001
            print(f"{sym:<10} error: {exc}")
            continue
        if len(f) < 90:
            print(f"{sym:<10} {'sin datos suficientes':>20}")
            continue
        days = (f.index[-1] - f.index[0]).days or 1

        # Siempre en el carry: suma de todos los fundings (el short cobra el
        # funding positivo y PAGA el negativo), una sola rotación.
        gross_always = f.sum() * 100
        apr_always = (gross_always - ROTATION_COST_PCT) * 365 / days

        # Filtrado: en posición sólo si el promedio 7d (21 eventos) previo > 0.
        # Rotación cada vez que el filtro cambia de estado.
        avg7 = f.rolling(21).mean().shift(1)
        in_pos = (avg7 > 0).fillna(False)
        gross_filt = f[in_pos].sum() * 100
        rotations = int(in_pos.astype(int).diff().abs().sum() // 1)
        apr_filt = (gross_filt - rotations * ROTATION_COST_PCT) * 365 / days
        pct_time = in_pos.mean() * 100

        total_always += apr_always
        total_filt += apr_filt
        count += 1
        print(f"{sym:<10} {days:>5} {apr_always:>13.1f}% {apr_filt:>13.1f}% "
              f"{pct_time:>14.0f}% {rotations:>10}")

    if count:
        print(f"\npromedio cartera: siempre-on {total_always/count:+.1f}% APR | "
              f"filtrado {total_filt/count:+.1f}% APR")
    print("* filtrado: en posición sólo cuando el funding promedio 7d es positivo")
    print("Nota: APR sobre el capital de UNA pata. Capital real = 2 patas (spot")
    print("+ margen del perp), o sea el retorno sobre capital total es ~la mitad,")
    print("y el margen del perp permite apalancar la pata corta.")


if __name__ == "__main__":
    main()
