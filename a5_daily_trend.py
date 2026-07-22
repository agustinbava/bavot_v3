"""A5 "Tendencia Diaria" — estrategia mecánica candidata (0 tokens).

SMA 10/40 crossover long/short sobre velas diarias de Binance, validada por
walk-forward en RESEARCH.md (2026-07-15). Mantiene su propio journal de
posiciones virtuales en bavot.db (tabla a5_positions) y marca a mercado.

Uso (1 vez por día, después del cierre diario UTC):
    python a5_daily_trend.py            # actualiza posiciones + muestra estado
    python a5_daily_trend.py --report   # sólo muestra estado
"""
from __future__ import annotations

import argparse
from datetime import datetime

from config import load_config
from daily_trend import fetch_daily
from storage import connect

POSITION_USD = 100.0
FEE_RT_PCT = 0.10
FAST, SLOW = 10, 40

# A5.1 (validado 2026-07-16, ver RESEARCH.md): sizing por volatilidad.
# Al abrir cada pierna: usd = 100 * clip(TARGET_VOL / vol30, CAP_LO, CAP_HI).
# El tamaño queda fijo hasta el cierre. Sólo afecta piernas nuevas.
TARGET_VOL = 0.03   # 3%/día — una moneda promedio queda cerca de $100
CAP_LO, CAP_HI = 0.33, 3.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS a5_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,          -- LONG | SHORT
    entry REAL NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    exit_price REAL,
    pnl_usd REAL,                     -- neto de fees, sobre POSITION_USD
    status TEXT NOT NULL DEFAULT 'open'   -- open | closed
);
"""


def desired_position(df) -> int:
    """+1 long, -1 short según el cruce SMA 10/40 al último cierre."""
    fast = df["close"].rolling(FAST).mean().iloc[-1]
    slow = df["close"].rolling(SLOW).mean().iloc[-1]
    return 1 if fast > slow else -1


def sized_usd(df) -> float:
    """Tamaño A5.1 de una pierna nueva según la vol 30d previa (sin
    lookahead: el último cierre ya ocurrió, la vol usa hasta ese punto)."""
    vol30 = df["close"].pct_change().rolling(30).std().iloc[-1]
    if not vol30 or vol30 != vol30:  # NaN o 0 → tamaño base
        return POSITION_USD
    w = min(max(TARGET_VOL / vol30, CAP_LO), CAP_HI)
    return round(POSITION_USD * w, 2)


def _migrate(conn) -> None:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(a5_positions)")]
    if "position_usd" not in cols:
        conn.execute("ALTER TABLE a5_positions ADD COLUMN position_usd REAL "
                     "NOT NULL DEFAULT 100.0")


def update() -> None:
    cfg = load_config("config.yaml")
    symbols = [i.symbol for i in cfg.watchlist if i.type == "crypto"]
    now = datetime.now().isoformat(timespec="seconds")

    with connect() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        # Cerrar posiciones de símbolos que salieron de la watchlist
        for orphan in conn.execute(
            "SELECT * FROM a5_positions WHERE status='open'"
        ).fetchall():
            if orphan["symbol"] in symbols:
                continue
            try:
                px = float(fetch_daily(orphan["symbol"], days=5)["close"].iloc[-1])
            except Exception:
                continue
            sign = 1 if orphan["direction"] == "LONG" else -1
            pnl = ((px - orphan["entry"]) / orphan["entry"] * 100 * sign
                   - FEE_RT_PCT) / 100 * orphan["position_usd"]
            conn.execute(
                """UPDATE a5_positions SET status='closed', closed_at=?,
                   exit_price=?, pnl_usd=? WHERE id=?""",
                (now, px, round(pnl, 4), orphan["id"]),
            )
            print(f"{orphan['symbol']}: cierra {orphan['direction']} @ {px:g} "
                  f"(fuera de watchlist, {pnl:+.2f} USD)")
        for sym in symbols:
            df = fetch_daily(sym, days=80)
            if len(df) < SLOW + 2:
                print(f"{sym}: historia insuficiente, salteado")
                continue
            want = desired_position(df)
            price = float(df["close"].iloc[-1])
            open_pos = conn.execute(
                "SELECT * FROM a5_positions WHERE symbol=? AND status='open'",
                (sym,),
            ).fetchone()

            cur = 0
            if open_pos:
                cur = 1 if open_pos["direction"] == "LONG" else -1
            if cur == want:
                continue

            if open_pos:  # cerrar la posición vigente
                sign = 1 if open_pos["direction"] == "LONG" else -1
                pnl = ((price - open_pos["entry"]) / open_pos["entry"]
                       * 100 * sign - FEE_RT_PCT) / 100 * open_pos["position_usd"]
                conn.execute(
                    """UPDATE a5_positions SET status='closed', closed_at=?,
                       exit_price=?, pnl_usd=? WHERE id=?""",
                    (now, price, round(pnl, 4), open_pos["id"]),
                )
                print(f"{sym}: cierra {open_pos['direction']} @ {price:g} "
                      f"({pnl:+.2f} USD)")
            direction = "LONG" if want == 1 else "SHORT"
            usd = sized_usd(df)
            conn.execute(
                """INSERT INTO a5_positions
                   (symbol, direction, entry, opened_at, position_usd)
                   VALUES (?, ?, ?, ?, ?)""",
                (sym, direction, price, now, usd),
            )
            print(f"{sym}: abre {direction} @ {price:g} (${usd:.0f} A5.1)")


def report() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        open_rows = conn.execute(
            "SELECT * FROM a5_positions WHERE status='open' ORDER BY symbol"
        ).fetchall()
        closed = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(pnl_usd),0) p, "
            "SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) w "
            "FROM a5_positions WHERE status='closed'"
        ).fetchone()
    print(f"A5 Tendencia Diaria — {len(open_rows)} posiciones abiertas")
    for r in open_rows:
        print(f"  {r['symbol']:<10} {r['direction']:<6} entry {r['entry']:g} "
              f"${r['position_usd']:.0f} desde {r['opened_at'][:10]}")
    if closed["c"]:
        print(f"cerradas: {closed['c']} | win rate "
              f"{closed['w']/closed['c']:.0%} | P&L neto {closed['p']:+.2f} USD")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if not args.report:
        update()
    report()
