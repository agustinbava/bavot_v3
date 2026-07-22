"""Notificador proactive-lite de Bavot: VIGILA Y AVISA, nunca actúa.

Eventos que dispara (cada uno con antispam en tabla notify_state):
  1. lab confirma una hipótesis (esperando OK del usuario) — una vez.
  2. learn: concentración >50% del flotante en una moneda — máx 1/día.
  3. learn: deriva de A5.1 (wr<20% con n>=30 cerradas) — máx 1/día.
  4. Sistema caído: snapshots de equity con >3h de atraso (cron muerto
     o Mac dormida) — máx 1/día.
  5. T1: señal nueva interpretada y cierre de señal (TP/SL/expirada).

Salidas: notificación de macOS (osascript) + mensaje de Telegram a tus
"Mensajes guardados" (reusa la sesión bavot_tg).

Uso: python notify.py           # chequea y avisa (cron cada 30 min)
     python notify.py --test    # manda una notificación de prueba
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
from datetime import datetime, timezone

from storage import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_state (key TEXT PRIMARY KEY, value TEXT);
"""

CLOSED = ("tp", "sl", "ambiguous", "expired", "not_triggered")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _once(conn, key: str, per_day: bool = False) -> bool:
    """True si este evento aún no fue notificado (y lo marca)."""
    k = f"{key}:{_today()}" if per_day else key
    row = conn.execute("SELECT 1 FROM notify_state WHERE key=?", (k,)).fetchone()
    if row:
        return False
    conn.execute("INSERT INTO notify_state VALUES (?, ?)",
                 (k, datetime.now(timezone.utc).isoformat()))
    return True


def gather(conn) -> list[str]:
    msgs: list[str] = []

    # 1) hipótesis confirmadas por el lab
    try:
        for r in conn.execute(
            "SELECT id, name, result FROM hypotheses WHERE status='confirmed'"
        ).fetchall():
            if _once(conn, f"hyp_{r['id']}"):
                msgs.append(f"🧪 Lab: hipótesis '{r['name']}' VALIDADA — "
                            f"esperando tu OK. {r['result'] or ''}")
    except Exception:
        pass

    # 2) concentración entre motores
    try:
        c = conn.execute("SELECT * FROM concentration_snapshots "
                         "ORDER BY ts DESC LIMIT 1").fetchone()
        if c and c["book_float_usd"] and abs(c["book_float_usd"]) > 10:
            share = abs(c["top_float_usd"]) / abs(c["book_float_usd"])
            if share > 0.5 and _once(conn, "conc", per_day=True):
                msgs.append(f"⚠ Concentración: {c['top_symbol']} explica "
                            f"{share:.0%} del flotante del libro "
                            f"({c['top_float_usd']:+.2f} de "
                            f"{c['book_float_usd']:+.2f} USD).")
    except Exception:
        pass

    # 3) deriva de A5.1
    try:
        a5 = conn.execute(
            "SELECT COUNT(*) c, SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) w "
            "FROM a5_positions WHERE status='closed'").fetchone()
        if a5["c"] and a5["c"] >= 30 and a5["w"] / a5["c"] < 0.20 \
                and _once(conn, "drift", per_day=True):
            msgs.append(f"⚠ Deriva A5.1: win rate vivo "
                        f"{a5['w']/a5['c']:.0%} con {a5['c']} cerradas — "
                        "revisar régimen/implementación (no re-tunear).")
    except Exception:
        pass

    # 4) sistema caído (snapshots viejos)
    try:
        last = conn.execute(
            "SELECT MAX(ts) m FROM equity_snapshots").fetchone()["m"]
        if last:
            age_h = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(last)).total_seconds() / 3600
            if age_h > 3 and _once(conn, "stale", per_day=True):
                msgs.append(f"🔴 Sistema: último snapshot hace {age_h:.0f}h — "
                            "¿cron caído o Mac dormida?")
    except Exception:
        pass

    # 5) T1: señales nuevas, gatillos de entrada y cierres
    try:
        for r in conn.execute("SELECT * FROM t1_signals").fetchall():
            if (r["entry_at"] and r["status"] == "pending"
                    and _once(conn, f"t1_entry_{r['id']}")):
                msgs.append(f"🎯 T1 EN POSICIÓN: {r['symbol']} "
                            f"{r['direction']} gatilló entry {r['entry']:g} "
                            f"→ TP {r['tp']:g} / SL {r['sl']:g}")
            if _once(conn, f"t1_new_{r['id']}"):
                msgs.append(f"📡 T1 señal nueva [{r['channel']}]: "
                            f"{r['symbol']} {r['direction']} entry "
                            f"{r['entry']:g} / SL {r['sl']:g} / TP {r['tp']:g}"
                            f"{' (defaults)' if r['defaults_used'] else ''}")
            if r["status"] in CLOSED and _once(conn, f"t1_close_{r['id']}"):
                pnl = (f" {r['pnl_usd']:+.2f} USD"
                       if r["pnl_usd"] is not None else "")
                msgs.append(f"📡 T1 cerrada: {r['symbol']} {r['direction']} "
                            f"→ {r['status']}{pnl}")
    except Exception:
        pass

    return msgs


def send_mac(text: str) -> None:
    try:
        safe = text.replace('"', "'")[:180]
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe}" with title "Bavot"'],
            timeout=10, capture_output=True)
    except Exception:
        pass


async def send_telegram(text: str) -> bool:
    try:
        from telegram_collector import client
        tg = client()
        await tg.connect()
        if not await tg.is_user_authorized():
            await tg.disconnect()
            return False
        await tg.send_message("me", f"🤖 Bavot\n{text}")
        await tg.disconnect()
        return True
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()

    if args.test:
        text = ("Notificador activo ✓ — te voy a avisar acá cuando el lab "
                "valide una hipótesis, learn alerte, T1 opere o el cron "
                "se caiga.")
        send_mac(text)
        ok = asyncio.run(send_telegram(text))
        print(f"prueba enviada (telegram: {'ok' if ok else 'sin sesión'})")
        return

    with connect() as conn:
        conn.executescript(_SCHEMA)
        msgs = gather(conn)
    if not msgs:
        print("notify: sin novedades")
        return
    text = "\n".join(msgs)
    send_mac(msgs[0] + (f" (+{len(msgs)-1} más)" if len(msgs) > 1 else ""))
    ok = asyncio.run(send_telegram(text))
    print(f"notify: {len(msgs)} avisos (telegram: {'ok' if ok else 'falló'})")
    for m in msgs:
        print(f"  {m}")


if __name__ == "__main__":
    main()
