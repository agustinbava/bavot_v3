"""T1 — paper trading de señales de canales de Telegram (forward-only).

Cada 15 min (cron): recolecta mensajes nuevos → prefiltro por regex
(gratis) → los que mencionan una crypto van al intérprete LLM (effort
low, centavos) → si hay señal operable se registra en el journal
t1_signals con $100 virtuales → el simulador del evaluador la resuelve
contra velas 15m de Binance (entry/SL/TP/expiración).

Defaults con sentido vs comisiones (fee RT 0.10% sobre $100 = $0.10):
  SL 3% / TP 6% (RR 2:1; TP = 60x el costo del viaje redondo)
  entrada válida 3 días; máximo 7 días en el trade.
FORWARD-ONLY: la primera corrida marca los mensajes existentes como
baseline y NUNCA los opera — a los canales se los juzga desde hoy.
Los mensajes son datos, no órdenes: esto simula, jamás ejecuta.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from config import load_config
from evaluator import simulate_signal
from storage import connect
from telegram_collector import CHANNELS, collect

POSITION_USD = 100.0
FEE_RT_USD = 0.10          # 0.05% x 2 lados sobre $100
DEF_SL_PCT, DEF_TP_PCT = 0.03, 0.06
ENTRY_WINDOW_S = 3 * 86400
MAX_DURATION_S = 7 * 86400
MAX_LLM_PER_RUN = 10       # tope de costo por corrida

# En grupos con charla de miembros, sólo se interpreta al autor de las
# señales (los demás mensajes ni llegan al LLM). id de Telegram por canal.
SENDER_FILTER = {"v.i.p de jack": "1694918320"}   # Jack

COIN_RE = re.compile(
    r"\b(BTC|ETH|SOL|XRP|BNB|DOGE|ADA|AVAX|LINK|PEPE|NEAR|SUI|TON|TRX|LTC|"
    r"XLM|HBAR|INJ|UNI|AAVE|BCH|WLD|TAO|ENA|ONDO|ZEC|BITCOIN|ETHEREUM)\b",
    re.I)

PARSER_SYSTEM = """Sos el intérprete de señales de Bavot. Recibís UN mensaje \
de un canal de análisis de trading en español. Tu única salida es JSON \
válido, sin markdown ni texto extra.

Si el mensaje contiene una idea operable sobre UNA criptomoneda que cotiza \
en Binance contra USDT, devolvé:
{"signal": true, "symbol": "ETHUSDT", "direction": "LONG"|"SHORT", \
"entry": <número, o null si es a mercado>, "sl": <número o null>, \
"tp": <número o null>, "reason": "<máx 12 palabras>"}

Reglas:
- "si supera/rompe/pasa X" → LONG con entry X. "si pierde/quiebra X" → \
SHORT con entry X. Zona (p.ej. 1875-1890) que debe superar → entry = borde \
superior; que debe defender → SHORT si la pierde, entry = borde inferior.
- Sólo niveles que el mensaje da explícitamente; NUNCA inventes números.
- Stocks, índices, oro, forex, comentarios generales, chistes, resúmenes \
de mercado sin nivel operable, o marketing → {"signal": false}.
- Un solo activo por mensaje: el principal."""

VERIFIER_SYSTEM = """Sos el auditor de señales de Bavot. Recibís un \
mensaje original de un canal de trading y la interpretación que otro \
sistema propuso. Tu única salida es JSON válido, sin markdown.

Aprobá SOLO si la interpretación es fiel al mensaje: mismo activo, misma \
dirección, y niveles que el mensaje realmente da (o null). Rechazá si \
inventa niveles, invierte la dirección, elige mal el activo, o si el \
mensaje en realidad no propone un trade operable.
Respondé: {"approve": true|false, "why": "<máx 12 palabras>"}"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS t1_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    msg_id INTEGER NOT NULL,
    msg_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry REAL NOT NULL,
    sl REAL NOT NULL,
    tp REAL NOT NULL,
    defaults_used TEXT,            -- 'sl,tp' si se aplicaron defaults
    position_usd REAL NOT NULL DEFAULT 100.0,
    status TEXT NOT NULL DEFAULT 'pending',
    entry_at TEXT, exit_at TEXT, exit_price REAL,
    pnl_usd REAL, fees_usd REAL,
    reason TEXT, raw_msg TEXT,
    UNIQUE(channel, msg_id)
);
CREATE TABLE IF NOT EXISTS t1_state (key TEXT PRIMARY KEY, value TEXT);
"""


def price_of(symbol: str) -> float | None:
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price",
                         params={"symbol": symbol}, timeout=10)
        return float(r.json()["price"]) if r.status_code == 200 else None
    except Exception:
        return None


def parse_message(client, model: str, text: str) -> dict | None:
    try:
        resp = client.messages.create(
            model=model, max_tokens=600, system=PARSER_SYSTEM,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": text[:2000]}],
        )
        raw = next(b.text for b in resp.content if b.type == "text")
        raw = raw.strip().removeprefix("```json").removeprefix("```").strip("` \n")
        return json.loads(raw)
    except Exception as exc:
        print(f"  parser error: {exc}")
        return None


def verify_signal(client, model: str, msg: str, parsed: dict) -> tuple[bool, str]:
    """Peer review: un segundo pase adversarial audita la interpretación
    (regla del sistema: ningún agente califica su propio trabajo)."""
    try:
        resp = client.messages.create(
            model=model, max_tokens=300, system=VERIFIER_SYSTEM,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content":
                       f"MENSAJE:\n{msg[:1500]}\n\nINTERPRETACIÓN:\n"
                       f"{json.dumps(parsed, ensure_ascii=False)}"}],
        )
        raw = next(b.text for b in resp.content if b.type == "text")
        raw = raw.strip().removeprefix("```json").removeprefix("```").strip("` \n")
        out = json.loads(raw)
        return bool(out.get("approve")), out.get("why") or ""
    except Exception as exc:
        return False, f"auditor error: {exc}"


def interpret_new(conn) -> None:
    load_dotenv(".env")
    from anthropic import Anthropic
    cfg = load_config("config.yaml")
    client = None

    for channel in CHANNELS:
        row = conn.execute("SELECT value FROM t1_state WHERE key=?",
                           (f"last_{channel}",)).fetchone()
        maxid = conn.execute(
            "SELECT COALESCE(MAX(msg_id),0) m FROM telegram_messages "
            "WHERE channel=?", (channel,)).fetchone()["m"]
        if row is None:
            # primera corrida: baseline forward-only, NO se opera lo viejo
            conn.execute("INSERT INTO t1_state VALUES (?, ?)",
                         (f"last_{channel}", str(maxid)))
            print(f"{channel}: baseline en msg {maxid} (forward-only)")
            continue
        last = int(row["value"])
        msgs = conn.execute(
            "SELECT * FROM telegram_messages WHERE channel=? AND msg_id>? "
            "ORDER BY msg_id", (channel, last)).fetchall()
        budget = MAX_LLM_PER_RUN
        analyzed = no_signal = 0
        for m in msgs:
            if budget <= 0:
                break
            want_sender = SENDER_FILTER.get(channel)
            if want_sender and m["sender"] != want_sender:
                continue
            if not COIN_RE.search(m["text"]):
                continue
            analyzed += 1
            if client is None:
                client = Anthropic()
            parsed = parse_message(client, cfg.anthropic.model, m["text"])
            budget -= 1
            if not parsed or not parsed.get("signal"):
                no_signal += 1
                continue
            sym = (parsed.get("symbol") or "").upper()
            if not sym.endswith("USDT"):
                sym += "USDT"
            px = price_of(sym)
            direction = parsed.get("direction")
            if px is None or direction not in ("LONG", "SHORT"):
                continue
            entry = float(parsed["entry"]) if parsed.get("entry") else px
            sign = 1 if direction == "LONG" else -1
            used = []
            sl = parsed.get("sl")
            if sl is None:
                sl = entry * (1 - sign * DEF_SL_PCT)
                used.append("sl")
            tp = parsed.get("tp")
            if tp is None:
                tp = entry * (1 + sign * DEF_TP_PCT)
                used.append("tp")
            ok, why = verify_signal(client, cfg.anthropic.model,
                                    m["text"], parsed)
            budget -= 1
            status = "pending" if ok else "vetoed"
            conn.execute(
                """INSERT OR IGNORE INTO t1_signals
                   (channel, msg_id, msg_date, symbol, direction, entry,
                    sl, tp, defaults_used, position_usd, status, reason,
                    raw_msg)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (channel, m["msg_id"], m["msg_date"], sym, direction,
                 entry, float(sl), float(tp), ",".join(used) or None,
                 POSITION_USD, status,
                 (parsed.get("reason") or "") + (f" | VETADA: {why}" if not ok else ""),
                 m["text"][:500]))
            if ok:
                print(f"T1 señal: {channel} → {sym} {direction} "
                      f"entry {entry:g} sl {sl:g} tp {tp:g} "
                      f"{'(defaults: ' + ','.join(used) + ')' if used else ''}")
            else:
                print(f"T1 VETADA por el auditor: {sym} {direction} — {why}")
        conn.execute("UPDATE t1_state SET value=? WHERE key=?",
                     (str(maxid), f"last_{channel}"))
        if msgs:
            print(f"T1 {channel}: {len(msgs)} nuevos, {analyzed} al LLM, "
                  f"{no_signal} sin señal operable")
            prev = conn.execute(
                "SELECT value FROM t1_state WHERE key='analyzed_total'"
            ).fetchone()
            tot = (int(prev["value"]) if prev else 0) + analyzed
            conn.execute("INSERT OR REPLACE INTO t1_state VALUES "
                         "('analyzed_total', ?)", (str(tot),))


def evaluate_open(conn) -> None:
    now_ts = int(time.time())
    rows = conn.execute(
        "SELECT * FROM t1_signals WHERE status='pending'").fetchall()
    for r in rows:
        sig_ts = int(datetime.fromisoformat(r["msg_date"]).timestamp())
        try:
            resp = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": r["symbol"], "interval": "15m",
                        "startTime": sig_ts * 1000, "limit": 1000},
                timeout=15)
            candles = [(int(k[0] / 1000), float(k[1]), float(k[2]),
                        float(k[3]), float(k[4])) for k in resp.json()]
        except Exception:
            continue
        res = simulate_signal(
            r["direction"], r["entry"], r["sl"], r["tp"], candles,
            sig_ts, now_ts, entry_window_s=ENTRY_WINDOW_S,
            max_duration_s=MAX_DURATION_S)
        entry_at = (datetime.fromtimestamp(res.entry_ts, tz=timezone.utc)
                    .isoformat(timespec="seconds") if res.entry_ts else None)
        if res.status == "pending":
            conn.execute("UPDATE t1_signals SET entry_at=? WHERE id=?",
                         (entry_at, r["id"]))
            continue
        pnl = fees = None
        exit_at = (datetime.fromtimestamp(res.exit_ts, tz=timezone.utc)
                   .isoformat(timespec="seconds") if res.exit_ts else None)
        if res.exit_price is not None and res.entry_ts:
            sign = 1 if r["direction"] == "LONG" else -1
            pct = (res.exit_price - r["entry"]) / r["entry"] * 100 * sign
            fees = FEE_RT_USD
            pnl = round(pct / 100 * r["position_usd"] - fees, 4)
        conn.execute(
            """UPDATE t1_signals SET status=?, entry_at=?, exit_at=?,
               exit_price=?, pnl_usd=?, fees_usd=? WHERE id=?""",
            (res.status, entry_at, exit_at, res.exit_price, pnl, fees,
             r["id"]))
        print(f"T1 {r['symbol']} {r['direction']} → {res.status}"
              f"{f' ({pnl:+.2f} USD)' if pnl is not None else ''}")


def main() -> None:
    asyncio.run(collect())
    with connect() as conn:
        conn.executescript(_SCHEMA)
        interpret_new(conn)
        evaluate_open(conn)
        n = conn.execute("SELECT COUNT(*) c FROM t1_signals").fetchone()["c"]
        print(f"T1: {n} señales en el journal")


if __name__ == "__main__":
    main()
