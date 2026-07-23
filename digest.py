"""Digest semanal de Bavot → Telegram (Mensajes guardados), 0 tokens.

Resumen digerible del estado: libro, cada motor, T1, progreso de gates,
lab. Cron: domingos 21:45 (tras las corridas diarias). Reusa la sesión
bavot_tg y la lógica de gates.py.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import requests

import gates as _g
from storage import connect

WEEK_AGO = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()


def _prices() -> dict:
    try:
        return {d["symbol"]: float(d["price"]) for d in requests.get(
            "https://api.binance.com/api/v3/ticker/price", timeout=10).json()}
    except Exception:
        return {}


def build_digest(conn) -> str:
    px = _prices()
    lines = ["📊 *Digest semanal Bavot*", ""]
    book_flo = book_real = 0.0

    for eng, t in (("A5.1", "a5_positions"), ("B1", "b1_positions")):
        opens = [dict(r) for r in conn.execute(
            f"SELECT * FROM {t} WHERE status='open'").fetchall()]
        flo = 0.0
        for r in opens:
            p = px.get(r["symbol"])
            if p:
                s = 1 if r["direction"] == "LONG" else -1
                flo += (p - r["entry"]) / r["entry"] * s * r["position_usd"]
        wk = conn.execute(
            f"SELECT COUNT(*) c, COALESCE(SUM(pnl_usd),0) p, "
            f"SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) w FROM {t} "
            "WHERE status='closed' AND closed_at > ?", (WEEK_AGO,)).fetchone()
        tot = conn.execute(
            f"SELECT COUNT(*) c, COALESCE(SUM(pnl_usd),0) p FROM {t} "
            "WHERE status='closed'").fetchone()
        book_flo += flo
        book_real += tot["p"]
        wr = f"{wk['w']}/{wk['c']}" if wk["c"] else "0"
        lines.append(
            f"*{eng}*: {len(opens)} abiertas · flotante {flo:+.0f}$ · "
            f"esta semana {wk['c']} cerradas ({wr} win, {wk['p']:+.0f}$) · "
            f"acum {tot['c']} cerradas {tot['p']:+.0f}$")

    lines.append("")
    lines.append(f"*Libro*: flotante {book_flo:+.0f}$ · realizado "
                 f"{book_real:+.0f}$ · total {book_flo+book_real:+.0f}$")

    t1 = conn.execute(
        "SELECT COUNT(*) c, "
        "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) p, "
        "SUM(CASE WHEN status IN ('tp','sl') THEN 1 ELSE 0 END) cl, "
        "COALESCE(SUM(pnl_usd),0) pnl FROM t1_signals").fetchone()
    lines.append(f"*T1 Telegram*: {t1['c'] or 0} señales · {t1['p'] or 0} vivas "
                 f"· {t1['cl'] or 0} resueltas · {t1['pnl']:+.0f}$")

    # gates
    gs = _g.gate_status(conn)
    met = sum(1 for x in gs if x["met"])
    lines.append("")
    lines.append(f"*Go-live*: {met}/5 gates")
    for x in gs:
        mark = "✅" if x["met"] else "⬜"
        lines.append(f"  {mark} {x['name']} — {x['detail']}")

    # lab
    conf = conn.execute(
        "SELECT COUNT(*) c FROM hypotheses WHERE status='confirmed'").fetchone()["c"]
    wait = conn.execute(
        "SELECT COUNT(*) c FROM hypotheses WHERE status='waiting_data'").fetchone()["c"]
    lines.append("")
    lines.append(f"*Lab*: {conf} confirmadas esperando OK · {wait} en cola por datos")
    return "\n".join(lines)


async def send(text: str) -> bool:
    try:
        from telegram_collector import client
        tg = client()
        await tg.connect()
        if not await tg.is_user_authorized():
            await tg.disconnect()
            return False
        await tg.send_message("me", text)
        await tg.disconnect()
        return True
    except Exception:
        return False


def main() -> None:
    with connect() as conn:
        text = build_digest(conn)
    print(text)
    ok = asyncio.run(send(text))
    print(f"\n[enviado a telegram: {'ok' if ok else 'sin sesión'}]")


if __name__ == "__main__":
    main()
