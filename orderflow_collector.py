"""Recolector de order flow — aggTrades de Binance → barras delta de 5m.

Materia prima para la hipótesis orderflow_fade (lab). Baja los archivos
diarios públicos de data.binance.vision (gratis), agrega cada trade por
lado agresor (taker buy/sell) en velas de 5 minutos y guarda SOLO el
agregado compacto (los crudos no se persisten: BTC solo pesa ~100MB/día).

Tabla orderflow_5m: symbol, bar_ts, buy_qty, sell_qty, buy_quote,
sell_quote, n_trades → delta = buy_quote - sell_quote.

Uso:
    python orderflow_collector.py                  # el día UTC de ayer
    python orderflow_collector.py --backfill 7     # últimos N días
Cron: diario 03:30 ART (el archivo D-1 ya está publicado).
"""
from __future__ import annotations

import argparse
import csv
import io
import zipfile
from datetime import datetime, timedelta, timezone

import requests

from config import load_config
from storage import connect

BASE = "https://data.binance.vision/data/spot/daily/aggTrades"
BAR_S = 300  # 5 minutos

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orderflow_5m (
    symbol TEXT NOT NULL,
    bar_ts INTEGER NOT NULL,      -- epoch segundos, inicio de la barra
    buy_qty REAL NOT NULL,        -- volumen base comprado por takers
    sell_qty REAL NOT NULL,
    buy_quote REAL NOT NULL,      -- nocional USDT
    sell_quote REAL NOT NULL,
    n_trades INTEGER NOT NULL,
    UNIQUE(symbol, bar_ts)
);
"""


def collect_day(conn, symbol: str, day: str) -> int:
    url = f"{BASE}/{symbol}/{symbol}-aggTrades-{day}.zip"
    try:
        r = requests.get(url, timeout=120)
        if r.status_code != 200:
            return -1  # no publicado (o el símbolo no existe en spot)
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        raw = zf.read(zf.namelist()[0]).decode()
    except Exception as exc:
        print(f"  {symbol} {day}: error {exc}")
        return -1

    bars: dict[int, list] = {}
    for row in csv.reader(io.StringIO(raw)):
        if not row or not row[0].isdigit():
            continue  # header o basura
        price, qty = float(row[1]), float(row[2])
        ts_ms = int(row[5])
        # timestamps nuevos vienen en microsegundos
        if ts_ms > 10**14:
            ts_ms //= 1000
        is_buyer_maker = row[6] in ("True", "true", "1")
        bar = (ts_ms // 1000) // BAR_S * BAR_S
        b = bars.setdefault(bar, [0.0, 0.0, 0.0, 0.0, 0])
        if is_buyer_maker:      # el agresor fue el vendedor
            b[1] += qty
            b[3] += qty * price
        else:                   # taker buy
            b[0] += qty
            b[2] += qty * price
        b[4] += 1

    for bar, (bq, sq, bn, sn, n) in bars.items():
        conn.execute(
            "INSERT OR IGNORE INTO orderflow_5m VALUES (?, ?, ?, ?, ?, ?, ?)",
            (symbol, bar, round(bq, 6), round(sq, 6),
             round(bn, 2), round(sn, 2), n))
    return len(bars)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=2,
                    help="días hacia atrás (default 2: Binance publica los "
                         "archivos diarios con ~1 día de retraso)")
    args = ap.parse_args()

    cfg = load_config("config.yaml")
    symbols = [i.symbol for i in cfg.watchlist if i.type == "crypto"]
    today = datetime.now(timezone.utc).date()

    with connect() as conn:
        conn.executescript(_SCHEMA)
        for back in range(args.backfill, 0, -1):
            day = (today - timedelta(days=back)).isoformat()
            total = missing = 0
            for sym in symbols:
                done = conn.execute(
                    "SELECT COUNT(*) c FROM orderflow_5m WHERE symbol=? "
                    "AND bar_ts >= ? AND bar_ts < ?",
                    (sym,
                     int(datetime.fromisoformat(day + "T00:00:00+00:00")
                         .timestamp()),
                     int(datetime.fromisoformat(day + "T00:00:00+00:00")
                         .timestamp()) + 86400)).fetchone()["c"]
                if done > 200:
                    continue  # día ya recolectado
                n = collect_day(conn, sym, day)
                if n < 0:
                    missing += 1
                else:
                    total += n
            print(f"{day}: {total} barras nuevas"
                  + (f" ({missing} símbolos sin archivo)" if missing else ""))
        rows = conn.execute("SELECT COUNT(*) c, COUNT(DISTINCT symbol) s "
                            "FROM orderflow_5m").fetchone()
        print(f"orderflow_5m total: {rows['c']} barras, {rows['s']} símbolos")


if __name__ == "__main__":
    main()
