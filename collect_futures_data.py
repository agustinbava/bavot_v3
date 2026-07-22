"""Recolector de funding rates y open interest — Binance USDT-M futures.

Datos públicos, gratis, 0 tokens. NO opera: sólo acumula historia propia
para poder validar señales de futuros más adelante (¿el funding extremo
predice reversiones? ¿el OI creciente confirma tendencias?).

Por qué existe: el funding histórico se puede bajar ~2 años hacia atrás,
pero el open interest Binance sólo lo publica de los ÚLTIMOS 30 DÍAS —
si no lo juntamos nosotros, esa historia se pierde. Cuanto antes empiece
a correr, más muestra habrá el día que queramos backtestear.

Tablas en bavot.db:
    futures_funding(symbol, funding_time, rate)      cada 8h por símbolo
    futures_oi(symbol, ts, oi, oi_usd)               horario, últimos 30d+

Uso (cron horario; idempotente, sólo agrega lo nuevo):
    python collect_futures_data.py             # incremental
    python collect_futures_data.py --backfill  # primera vez: ~2 años funding
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

import requests

from config import load_config
from storage import connect

FAPI = "https://fapi.binance.com"
BACKFILL_DAYS = 730

# Monedas de precio microscópico: el futuro cotiza multiplicado x1000.
# Se guarda bajo el símbolo spot para que los joins con velas sean directos.
FUTURES_ALIAS = {"PEPEUSDT": "1000PEPEUSDT"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS futures_funding (
    symbol TEXT NOT NULL,
    funding_time TEXT NOT NULL,
    rate REAL NOT NULL,
    UNIQUE(symbol, funding_time)
);
CREATE TABLE IF NOT EXISTS futures_oi (
    symbol TEXT NOT NULL,
    ts TEXT NOT NULL,
    oi REAL NOT NULL,        -- contratos
    oi_usd REAL NOT NULL,    -- nocional en USDT
    UNIQUE(symbol, ts)
);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    ts TEXT NOT NULL,            -- hora redonda
    engine TEXT NOT NULL,        -- A5.1 | B1
    open_n INTEGER NOT NULL,
    long_usd REAL NOT NULL,      -- flotante del lado long
    short_usd REAL NOT NULL,     -- flotante del lado short
    net_exposure_usd REAL NOT NULL,
    UNIQUE(ts, engine)
);
CREATE TABLE IF NOT EXISTS concentration_snapshots (
    ts TEXT PRIMARY KEY,         -- hora redonda
    overlap_n INTEGER NOT NULL,  -- monedas con misma dirección en ambos motores
    overlap_syms TEXT NOT NULL,
    top_symbol TEXT,             -- moneda con mayor flotante combinado |abs|
    top_float_usd REAL,          -- flotante combinado de esa moneda
    book_float_usd REAL          -- flotante combinado del libro entero
);
"""


def _iso(ms: int) -> str:
    return (datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            .isoformat(timespec="seconds").removesuffix("+00:00"))


def collect_funding(conn, symbol: str, backfill: bool) -> int:
    row = conn.execute(
        "SELECT MAX(funding_time) m FROM futures_funding WHERE symbol=?",
        (symbol,),
    ).fetchone()
    if row["m"]:
        start = int(datetime.fromisoformat(row["m"]).timestamp() * 1000) + 1
    elif backfill:
        start = int((time.time() - BACKFILL_DAYS * 86400) * 1000)
    else:
        start = int((time.time() - 7 * 86400) * 1000)

    added = 0
    while True:
        r = requests.get(f"{FAPI}/fapi/v1/fundingRate",
                         params={"symbol": FUTURES_ALIAS.get(symbol, symbol),
                                 "startTime": start, "limit": 1000},
                         timeout=15)
        if r.status_code != 200:
            return -1  # sin futuros USDT-M para este símbolo
        rows = r.json()
        if not rows:
            break
        for it in rows:
            conn.execute(
                "INSERT OR IGNORE INTO futures_funding VALUES (?, ?, ?)",
                (symbol, _iso(it["fundingTime"]), float(it["fundingRate"])),
            )
            added += 1
        if len(rows) < 1000:
            break
        start = rows[-1]["fundingTime"] + 1
    return added


def collect_oi(conn, symbol: str, backfill: bool) -> int:
    limit = 500 if backfill else 48
    r = requests.get(f"{FAPI}/futures/data/openInterestHist",
                     params={"symbol": FUTURES_ALIAS.get(symbol, symbol),
                             "period": "1h", "limit": limit},
                     timeout=15)
    if r.status_code != 200:
        return -1
    added = 0
    for it in r.json():
        conn.execute(
            "INSERT OR IGNORE INTO futures_oi VALUES (?, ?, ?, ?)",
            (symbol, _iso(it["timestamp"]),
             float(it["sumOpenInterest"]),
             float(it["sumOpenInterestValue"])),
        )
        added += 1
    return added


def snapshot_equity(conn) -> None:
    """Foto horaria del flotante por lado y la exposición neta de cada
    motor — la materia prima para que learn.py analice cómo evoluciona
    el flotante vs la exposición (flotante NO es P&L realizado)."""
    try:
        data = requests.get("https://api.binance.com/api/v3/ticker/price",
                            timeout=10).json()
        prices = {d["symbol"]: float(d["price"]) for d in data}
    except Exception:
        return
    ts = datetime.now(timezone.utc).isoformat(timespec="hours")
    by_sym: dict[tuple, float] = {}   # (symbol) -> flotante combinado
    dirs: dict[str, dict] = {"A5.1": {}, "B1": {}}
    for engine, table in (("A5.1", "a5_positions"), ("B1", "b1_positions")):
        try:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE status='open'").fetchall()
        except Exception:
            continue
        long_f = short_f = net = 0.0
        for r in rows:
            px = prices.get(r["symbol"])
            if not px:
                continue
            sign = 1 if r["direction"] == "LONG" else -1
            u = (px - r["entry"]) / r["entry"] * sign * r["position_usd"]
            net += sign * r["position_usd"]
            by_sym[r["symbol"]] = by_sym.get(r["symbol"], 0.0) + u
            dirs[engine][r["symbol"]] = r["direction"]
            if sign == 1:
                long_f += u
            else:
                short_f += u
        conn.execute(
            "INSERT OR REPLACE INTO equity_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            (ts, engine, len(rows), round(long_f, 2), round(short_f, 2),
             round(net, 2)),
        )
    # concentración entre motores: misma moneda y misma dirección en ambos
    overlap = sorted(s for s in dirs["A5.1"]
                     if dirs["B1"].get(s) == dirs["A5.1"][s])
    if by_sym:
        top = max(by_sym, key=lambda s: abs(by_sym[s]))
        conn.execute(
            "INSERT OR REPLACE INTO concentration_snapshots VALUES "
            "(?, ?, ?, ?, ?, ?)",
            (ts, len(overlap), ",".join(overlap), top,
             round(by_sym[top], 2), round(sum(by_sym.values()), 2)),
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    args = ap.parse_args()

    cfg = load_config("config.yaml")
    symbols = [i.symbol for i in cfg.watchlist if i.type == "crypto"]
    no_futures = []
    with connect() as conn:
        conn.executescript(_SCHEMA)
        for sym in symbols:
            f = collect_funding(conn, sym, args.backfill)
            o = collect_oi(conn, sym, args.backfill)
            if f < 0 and o < 0:
                no_futures.append(sym)
                continue
            if args.backfill:
                print(f"{sym}: +{max(f,0)} funding, +{max(o,0)} OI")
        snapshot_equity(conn)
        tot_f = conn.execute("SELECT COUNT(*) c FROM futures_funding").fetchone()
        tot_o = conn.execute("SELECT COUNT(*) c FROM futures_oi").fetchone()
    print(f"totales: {tot_f['c']} funding rates, {tot_o['c']} snapshots de OI")
    if no_futures:
        print(f"sin futuros USDT-M: {', '.join(no_futures)}")


if __name__ == "__main__":
    main()
