"""SQLite persistence for analysis runs.

Schema:
    runs(id, started_at)                     -- one row per batch run
    analyses(id, run_id, symbol, type,       -- one row per instrument analyzed
             verdict, entry, stop_loss, take_profit, rr, confidence,
             error, raw_response, created_at)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from analyzer import Verdict

DB_PATH = Path("bavot.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    symbol TEXT NOT NULL,
    type TEXT,
    verdict TEXT,
    entry TEXT,
    stop_loss TEXT,
    take_profit TEXT,
    rr TEXT,
    confidence TEXT,
    error TEXT,
    raw_response TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_run ON analyses(run_id);
CREATE INDEX IF NOT EXISTS idx_analyses_created ON analyses(created_at);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL UNIQUE REFERENCES analyses(id),
    symbol TEXT NOT NULL,
    type TEXT,                      -- stock | crypto
    direction TEXT NOT NULL,        -- LONG | SHORT
    entry REAL, sl REAL, tp REAL,
    signal_at TEXT NOT NULL,        -- when the analysis was produced
    status TEXT NOT NULL DEFAULT 'pending',
        -- pending | not_triggered | tp | sl | expired | ambiguous | invalid | error
    entry_at TEXT, exit_at TEXT,
    exit_price REAL,
    pnl_pct REAL,                   -- signed %, from entry to exit
    r_multiple REAL,                -- signed R (reward measured in risk units)
    note TEXT,
    evaluated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE TABLE IF NOT EXISTS strategies (
    name TEXT PRIMARY KEY,
    description TEXT,
    created_at TEXT NOT NULL
);
"""

_DEFAULT_STRATEGY = "A1"


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema (idempotent)."""
    for table in ("analyses", "signals"):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "strategy" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN strategy TEXT")
            conn.execute(
                f"UPDATE {table} SET strategy = ? WHERE strategy IS NULL",
                (_DEFAULT_STRATEGY,),
            )
    sig_cols = {r["name"] for r in conn.execute("PRAGMA table_info(signals)")}
    if "pnl_usd" not in sig_cols:
        conn.execute("ALTER TABLE signals ADD COLUMN pnl_usd REAL")
        conn.execute("ALTER TABLE signals ADD COLUMN position_usd REAL")
    if "fees_usd" not in sig_cols:
        conn.execute("ALTER TABLE signals ADD COLUMN fees_usd REAL")
    if "leverage" not in sig_cols:
        conn.execute("ALTER TABLE signals ADD COLUMN leverage REAL DEFAULT 1")
        conn.execute("ALTER TABLE signals ADD COLUMN entry_window_min INTEGER")
        conn.execute("ALTER TABLE signals ADD COLUMN max_duration_min INTEGER")
        conn.execute("ALTER TABLE signals ADD COLUMN fee_side_pct REAL")
    if "last_price" not in sig_cols:
        conn.execute("ALTER TABLE signals ADD COLUMN last_price REAL")
        conn.execute("ALTER TABLE signals ADD COLUMN unrealized_pct REAL")
        conn.execute("ALTER TABLE signals ADD COLUMN unrealized_usd REAL")
        conn.execute("ALTER TABLE signals ADD COLUMN checked_at TEXT")
        conn.execute("ALTER TABLE signals ADD COLUMN latest_signal TEXT")
        conn.execute("ALTER TABLE signals ADD COLUMN latest_signal_at TEXT")


def connect(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def upsert_strategy(name: str, description: str,
                    db_path: str | Path = DB_PATH) -> None:
    """Register a strategy (or refresh its description)."""
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO strategies (name, description, created_at)
               VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET description = excluded.description""",
            (name, description, datetime.now().isoformat(timespec="seconds")),
        )


def fetch_strategies(db_path: str | Path = DB_PATH) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM strategies ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def save_run(
    verdicts: list[Verdict],
    inst_types: dict[str, str],
    db_path: str | Path = DB_PATH,
    strategy: str = _DEFAULT_STRATEGY,
) -> int:
    """Persist a batch run and its per-instrument results. Returns run id."""
    now = datetime.now().isoformat(timespec="seconds")
    with connect(db_path) as conn:
        cur = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (now,))
        run_id = cur.lastrowid
        conn.executemany(
            """INSERT INTO analyses
               (run_id, symbol, type, verdict, entry, stop_loss, take_profit,
                rr, confidence, error, raw_response, created_at, strategy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    run_id,
                    v.symbol,
                    inst_types.get(v.symbol),
                    "ERROR" if v.error else (v.verdict if v.parsed else "UNPARSED"),
                    v.entry,
                    v.stop_loss,
                    v.take_profit,
                    v.rr,
                    v.confidence,
                    v.error,
                    v.raw,
                    now,
                    strategy,
                )
                for v in verdicts
            ],
        )
    return run_id


def fetch_runs(
    date: str | None = None, db_path: str | Path = DB_PATH
) -> list[dict[str, Any]]:
    """Return runs (newest first) with their analyses.

    date: 'YYYY-MM-DD' to filter to one day; None = all days.
    """
    with connect(db_path) as conn:
        if date:
            run_rows = conn.execute(
                "SELECT * FROM runs WHERE started_at LIKE ? ORDER BY started_at DESC",
                (f"{date}%",),
            ).fetchall()
        else:
            run_rows = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC"
            ).fetchall()

        runs = []
        for r in run_rows:
            analyses = conn.execute(
                "SELECT * FROM analyses WHERE run_id = ? ORDER BY id", (r["id"],)
            ).fetchall()
            runs.append(
                {"id": r["id"], "started_at": r["started_at"],
                 "analyses": [dict(a) for a in analyses]}
            )
        return runs


def register_signals(
    db_path: str | Path = DB_PATH,
    strategy_params: dict[str, dict[str, Any]] | None = None,
    position_usd: float | None = None,
) -> int:
    """Create signal rows for LONG/SHORT analyses that don't have one yet.

    strategy_params: per-strategy snapshot {name: {leverage, entry_window_min,
    max_duration_min, fee_side_pct}} recorded on each signal so later config
    changes don't rewrite history. Returns the number of new signals.
    """
    strategy_params = strategy_params or {}

    def _num(s: str | None) -> float | None:
        if not s:
            return None
        try:
            return float(s.replace(",", ""))
        except ValueError:
            return None

    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT a.* FROM analyses a
               LEFT JOIN signals s ON s.analysis_id = a.id
               WHERE a.verdict IN ('LONG', 'SHORT') AND s.id IS NULL"""
        ).fetchall()
        created = 0
        for a in rows:
            # One open position per ticker: if a pending signal exists for
            # this symbol, don't open another — record the new reading on
            # the open signal instead (visible as "señal ahora: X").
            open_sig = conn.execute(
                "SELECT id, direction FROM signals "
                "WHERE symbol = ? AND status = 'pending'",
                (a["symbol"],),
            ).fetchone()
            if open_sig:
                conn.execute(
                    "UPDATE signals SET latest_signal = ?, latest_signal_at = ? "
                    "WHERE id = ?",
                    (a["verdict"], a["created_at"], open_sig["id"]),
                )
                conn.execute(
                    """INSERT INTO signals
                       (analysis_id, symbol, type, direction, signal_at,
                        status, note, strategy)
                       VALUES (?, ?, ?, ?, ?, 'conflict', ?, ?)""",
                    (
                        a["id"], a["symbol"], a["type"], a["verdict"],
                        a["created_at"],
                        f"bloqueada: ya hay {open_sig['direction']} abierto en {a['symbol']}",
                        a["strategy"] or _DEFAULT_STRATEGY,
                    ),
                )
                continue

            entry, sl, tp = _num(a["entry"]), _num(a["stop_loss"]), _num(a["take_profit"])
            ok = None not in (entry, sl, tp)
            if ok:  # sanity: stop on the correct side of entry
                if a["verdict"] == "LONG":
                    ok = sl < entry < tp
                else:
                    ok = tp < entry < sl
            sp = strategy_params.get(a["strategy"] or _DEFAULT_STRATEGY, {})
            conn.execute(
                """INSERT INTO signals
                   (analysis_id, symbol, type, direction, entry, sl, tp,
                    signal_at, status, note, strategy,
                    leverage, entry_window_min, max_duration_min, fee_side_pct,
                    position_usd)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    a["id"], a["symbol"], a["type"], a["verdict"],
                    entry, sl, tp, a["created_at"],
                    "pending" if ok else "invalid",
                    None if ok else "entry/SL/TP no parseables o incoherentes",
                    a["strategy"] or _DEFAULT_STRATEGY,
                    sp.get("leverage", 1.0),
                    sp.get("entry_window_min"),
                    sp.get("max_duration_min"),
                    sp.get("fee_side_pct"),
                    position_usd,
                ),
            )
            created += 1
        return created


def pending_signals(db_path: str | Path = DB_PATH) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM signals WHERE status = 'pending' ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def update_signal(sig_id: int, fields: dict[str, Any],
                  db_path: str | Path = DB_PATH) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect(db_path) as conn:
        conn.execute(
            f"UPDATE signals SET {cols}, evaluated_at = ? WHERE id = ?",
            [*fields.values(), datetime.now().isoformat(timespec="seconds"), sig_id],
        )


def fetch_signals(db_path: str | Path = DB_PATH) -> list[dict[str, Any]]:
    """All signals, newest first."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY signal_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_dates(db_path: str | Path = DB_PATH) -> list[str]:
    """Distinct dates (YYYY-MM-DD) that have runs, newest first."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT substr(started_at, 1, 10) AS d FROM runs ORDER BY d DESC"
        ).fetchall()
    return [r["d"] for r in rows]
