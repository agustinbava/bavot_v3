"""Estado de los criterios de GO-LIVE (pre-registrados 2026-07-20).

Un solo lugar de verdad para el tracker del dashboard y el digest semanal.
Cada gate devuelve: name, met (bool), progress (0-1 o None), detail.
Las decisiones cualitativas (gate 3 tramo adverso, gate 5 plan escrito)
las marca el humano acá cuando corresponda.
"""
from __future__ import annotations

from datetime import datetime, timezone

MIN_CLOSED = 30
MIN_WEEKS = 4

# --- flags cualitativos que el humano actualiza ---
ADVERSE_SURVIVED = True   # crash DEXE -83% absorbido sin intervención (2026-07-21)
EXEC_PLAN_WRITTEN = False  # gate 5: se escribe ~sept 2026


def _engine_stats(conn, table: str) -> tuple[int, float]:
    r = conn.execute(
        f"SELECT COUNT(*) c, COALESCE(SUM(pnl_usd),0) p "
        f"FROM {table} WHERE status='closed'").fetchone()
    return r["c"], r["p"]


def gate_status(conn) -> list[dict]:
    a5_n, a5_p = _engine_stats(conn, "a5_positions")
    b1_n, b1_p = _engine_stats(conn, "b1_positions")

    # inception: primer opened_at de A5.1
    row = conn.execute(
        "SELECT MIN(opened_at) m FROM a5_positions").fetchone()
    weeks = 0.0
    if row["m"]:
        start = datetime.fromisoformat(row["m"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        weeks = (datetime.now(timezone.utc) - start).days / 7

    def expect(n, p):
        return (p / n) if n else None

    e_a5, e_b1 = expect(a5_n, a5_p), expect(b1_n, b1_p)

    gates = [
        {
            "name": "Muestra ≥30 cerradas por motor",
            "met": a5_n >= MIN_CLOSED and b1_n >= MIN_CLOSED,
            "progress": min(a5_n, b1_n) / MIN_CLOSED,
            "detail": f"A5.1 {a5_n}/{MIN_CLOSED} · B1 {b1_n}/{MIN_CLOSED}",
        },
        {
            "name": "Expectativa neta por trade > 0",
            "met": (a5_n >= MIN_CLOSED and b1_n >= MIN_CLOSED
                    and (e_a5 or 0) > 0 and (e_b1 or 0) > 0),
            "progress": None,
            "detail": (f"A5.1 {e_a5:+.2f}$" if e_a5 is not None else "A5.1 —")
                      + (f" · B1 {e_b1:+.2f}$" if e_b1 is not None else " · B1 —")
                      + (" (sin muestra suficiente)" if min(a5_n, b1_n) < MIN_CLOSED else ""),
        },
        {
            "name": "Tramo adverso sobrevivido sin intervención",
            "met": ADVERSE_SURVIVED,
            "progress": None,
            "detail": "crash DEXE -83% absorbido con el 3% del libro (2026-07-21)"
                      if ADVERSE_SURVIVED else "pendiente",
        },
        {
            "name": "≥4 semanas sin incidentes operativos",
            "met": weeks >= MIN_WEEKS,
            "progress": min(weeks / MIN_WEEKS, 1.0),
            "detail": f"{weeks:.1f} semanas corriendo (juicio humano sobre "
                      "incidentes)",
        },
        {
            "name": "Plan de ejecución escrito",
            "met": EXEC_PLAN_WRITTEN,
            "progress": None,
            "detail": "pendiente (~septiembre 2026)"
                      if not EXEC_PLAN_WRITTEN else "escrito",
        },
    ]
    return gates


def summary_line(conn) -> str:
    g = gate_status(conn)
    met = sum(1 for x in g if x["met"])
    return f"{met}/5 gates de go-live cumplidos"
