"""B1 "Momentum Relativo" — momentum cross-sectional semanal (0 tokens).

Cada 7 días rankea el universo crypto por retorno de los últimos 30 días:
LONG las 7 más fuertes, SHORT las 7 más débiles, $100 por pata. Apuesta
relativa (fuertes vs débiles), casi neutral a la dirección del mercado.
Validada por TRAIN/TEST + stress test en RESEARCH.md (2026-07-16).

Las posiciones persisten entre rebalanceos si la moneda sigue en su
cuartil (sin churn ni fees innecesarios). Journal en tabla b1_positions.

Uso (cron diario; sólo actúa si pasaron >=7 días del último rebalanceo):
    python b1_xsect.py            # rebalancea si toca + muestra estado
    python b1_xsect.py --report   # sólo muestra estado
    python b1_xsect.py --force    # fuerza rebalanceo (primera vez)
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from config import load_config
from daily_trend import fetch_daily
from storage import connect

POSITION_USD = 100.0
FEE_RT_PCT = 0.10        # 0.05% por lado sobre el nocional
LOOKBACK = 30            # días de momentum (90d NO funciona, ver RESEARCH.md)
Q = 7                    # monedas por pata (cuartil de 29)
REBALANCE_DAYS = 7

_SCHEMA = """
CREATE TABLE IF NOT EXISTS b1_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,          -- LONG | SHORT
    entry REAL NOT NULL,
    position_usd REAL NOT NULL DEFAULT 100.0,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    exit_price REAL,
    pnl_usd REAL,                     -- neto de fees
    status TEXT NOT NULL DEFAULT 'open'   -- open | closed
);
CREATE TABLE IF NOT EXISTS b1_state (key TEXT PRIMARY KEY, value TEXT);
"""


def update(force: bool = False) -> None:
    cfg = load_config("config.yaml")
    symbols = [i.symbol for i in cfg.watchlist if i.type == "crypto"]
    now = datetime.now()
    now_s = now.isoformat(timespec="seconds")

    with connect() as conn:
        conn.executescript(_SCHEMA)
        last = conn.execute(
            "SELECT value FROM b1_state WHERE key='last_rebalance'"
        ).fetchone()
        if last and not force:
            elapsed = now - datetime.fromisoformat(last["value"])
            if elapsed < timedelta(days=REBALANCE_DAYS):
                print(f"B1: último rebalanceo hace {elapsed.days}d "
                      f"(<{REBALANCE_DAYS}d) — nada que hacer")
                return

        # momentum 30d de todo el universo (datos gratis)
        mom, prices = {}, {}
        for sym in symbols:
            try:
                closes = fetch_daily(sym, days=LOOKBACK + 15)["close"]
            except Exception as exc:
                print(f"{sym}: error de datos ({exc}), salteado")
                continue
            if len(closes) < LOOKBACK + 1:
                continue
            prices[sym] = float(closes.iloc[-1])
            mom[sym] = float(closes.iloc[-1] / closes.iloc[-1 - LOOKBACK] - 1)
        if len(mom) < 2 * Q + 4:
            print(f"B1: sólo {len(mom)} monedas con datos — no se rebalancea")
            return

        ranked = sorted(mom, key=mom.get)
        target = {s: "SHORT" for s in ranked[:Q]}
        target.update({s: "LONG" for s in ranked[-Q:]})

        # cerrar lo que ya no está en su cuartil (o salió de la watchlist)
        for r in conn.execute(
            "SELECT * FROM b1_positions WHERE status='open'"
        ).fetchall():
            if target.get(r["symbol"]) == r["direction"]:
                target.pop(r["symbol"])  # sigue en su cuartil: se mantiene
                continue
            px = prices.get(r["symbol"])
            if px is None:
                try:
                    px = float(fetch_daily(r["symbol"], days=5)["close"].iloc[-1])
                except Exception:
                    continue
            sign = 1 if r["direction"] == "LONG" else -1
            pnl = ((px - r["entry"]) / r["entry"] * 100 * sign
                   - FEE_RT_PCT) / 100 * r["position_usd"]
            conn.execute(
                """UPDATE b1_positions SET status='closed', closed_at=?,
                   exit_price=?, pnl_usd=? WHERE id=?""",
                (now_s, px, round(pnl, 4), r["id"]),
            )
            print(f"{r['symbol']}: cierra {r['direction']} @ {px:g} "
                  f"({pnl:+.2f} USD)")

        for sym, direction in sorted(target.items()):
            conn.execute(
                """INSERT INTO b1_positions
                   (symbol, direction, entry, position_usd, opened_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (sym, direction, prices[sym], POSITION_USD, now_s),
            )
            print(f"{sym}: abre {direction} @ {prices[sym]:g} "
                  f"(momentum 30d {mom[sym]*100:+.0f}%)")

        conn.execute("INSERT OR REPLACE INTO b1_state VALUES "
                     "('last_rebalance', ?)", (now_s,))


def report() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)
        open_rows = conn.execute(
            "SELECT * FROM b1_positions WHERE status='open' ORDER BY symbol"
        ).fetchall()
        closed = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(pnl_usd),0) p, "
            "SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) w "
            "FROM b1_positions WHERE status='closed'"
        ).fetchone()
    print(f"B1 Momentum Relativo — {len(open_rows)} posiciones abiertas")
    for r in open_rows:
        print(f"  {r['symbol']:<10} {r['direction']:<6} entry {r['entry']:g} "
              f"${r['position_usd']:.0f} desde {r['opened_at'][:10]}")
    if closed["c"]:
        print(f"cerradas: {closed['c']} | win rate "
              f"{closed['w']/closed['c']:.0%} | P&L neto {closed['p']:+.2f} USD")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if not args.report:
        update(force=args.force)
    report()
