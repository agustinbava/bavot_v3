"""Tests for signal registration: one open position per ticker."""
from __future__ import annotations

from storage import connect, fetch_signals, register_signals


def _add_analysis(db, symbol: str, verdict: str, created: str,
                  entry="100", sl="99", tp="102") -> None:
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO runs (started_at) VALUES (?)", (created,)
        )
        conn.execute(
            """INSERT INTO analyses (run_id, symbol, type, verdict, entry,
               stop_loss, take_profit, confidence, created_at, strategy)
               VALUES (?, ?, 'crypto', ?, ?, ?, ?, 'medium', ?, 'A3')""",
            (cur.lastrowid, symbol, verdict, entry, sl, tp, created),
        )


def test_opposite_signal_blocked_and_noted(tmp_path):
    db = tmp_path / "test.db"

    _add_analysis(db, "BTCUSDT", "LONG", "2026-07-14T10:00:00")
    assert register_signals(db_path=db, position_usd=100) == 1

    # Nueva lectura SHORT sobre el mismo ticker con posición abierta.
    _add_analysis(db, "BTCUSDT", "SHORT", "2026-07-14T11:00:00",
                  entry="100", sl="101", tp="98")
    register_signals(db_path=db, position_usd=100)

    signals = fetch_signals(db_path=db)
    open_pos = [s for s in signals if s["status"] == "pending"]
    conflicts = [s for s in signals if s["status"] == "conflict"]

    assert len(open_pos) == 1            # sigue habiendo UNA posición
    assert open_pos[0]["direction"] == "LONG"
    assert open_pos[0]["latest_signal"] == "SHORT"      # aviso de cambio
    assert open_pos[0]["latest_signal_at"] == "2026-07-14T11:00:00"
    assert len(conflicts) == 1           # la bloqueada queda registrada
    assert "ya hay LONG" in conflicts[0]["note"]


def test_second_symbol_not_blocked(tmp_path):
    db = tmp_path / "test.db"
    _add_analysis(db, "BTCUSDT", "LONG", "2026-07-14T10:00:00")
    _add_analysis(db, "ETHUSDT", "SHORT", "2026-07-14T10:00:00",
                  entry="100", sl="101", tp="98")
    assert register_signals(db_path=db, position_usd=100) == 2
    assert len([s for s in fetch_signals(db_path=db)
                if s["status"] == "pending"]) == 2
