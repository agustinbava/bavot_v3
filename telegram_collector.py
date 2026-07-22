"""Recolector de canales de Telegram — fase 1: SOLO escuchar y guardar.

Igual que funding/OI: primero se acumula historia propia; el paper trading
(C1) se construye encima cuando haya muestra. NO opera, NO responde, NO
ejecuta nada que diga un mensaje: los mensajes son datos, no órdenes.

Canales (match por nombre, case-insensitive, sobre tus diálogos):
    Lady Market - Crypto y Mercados
    Cripto with Jack
    V.I.P de Jack (agregado 2026-07-20; señales de BTC/ETH)

Uso:
    python telegram_collector.py --login   # UNA sola vez: pide el código
    python telegram_collector.py           # incremental (cron cada 30 min)

Credenciales en .env: TELEGRAM_API_ID / TELEGRAM_API_HASH.
La sesión queda en bavot_tg.session (local, no subir a ningún lado).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from storage import connect

CHANNELS = ["lady market", "cripto with jack", "v.i.p de jack"]
SESSION = "bavot_tg"
BACKFILL_LIMIT = 500          # mensajes hacia atrás en la primera pasada

_SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_messages (
    channel TEXT NOT NULL,
    msg_id INTEGER NOT NULL,
    msg_date TEXT NOT NULL,
    text TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    sender TEXT,                  -- id del remitente (grupos con charla)
    UNIQUE(channel, msg_id)
);
"""


def client():
    from telethon import TelegramClient
    load_dotenv(".env")
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        sys.exit("Faltan TELEGRAM_API_ID / TELEGRAM_API_HASH en .env")
    return TelegramClient(SESSION, int(api_id), api_hash)


async def resolve_channels(tg) -> dict:
    """Matchea los canales configurados contra tus diálogos por nombre."""
    found = {}
    async for d in tg.iter_dialogs():
        name = (d.name or "").lower()
        for want in CHANNELS:
            if want in name:
                found[want] = d.entity
    missing = [c for c in CHANNELS if c not in found]
    for m in missing:
        print(f"⚠ canal no encontrado en tus diálogos: '{m}' — "
              "¿estás unido con esta cuenta?")
    return found


async def collect() -> None:
    tg = client()
    await tg.connect()
    if not await tg.is_user_authorized():
        print("Sin sesión: corré  .venv/bin/python telegram_collector.py "
              "--login  una vez (pide el código de Telegram).")
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    chans = await resolve_channels(tg)
    with connect() as conn:
        conn.executescript(_SCHEMA)
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(telegram_messages)")]
        if "sender" not in cols:
            conn.execute("ALTER TABLE telegram_messages ADD COLUMN sender TEXT")
        for want, entity in chans.items():
            last = conn.execute(
                "SELECT MAX(msg_id) m FROM telegram_messages WHERE channel=?",
                (want,),
            ).fetchone()["m"]
            kwargs = {"min_id": last} if last else {"limit": BACKFILL_LIMIT}
            added = 0
            async for msg in tg.iter_messages(entity, **kwargs):
                if not msg.text:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO telegram_messages VALUES "
                    "(?, ?, ?, ?, ?, ?)",
                    (want, msg.id,
                     msg.date.isoformat(timespec="seconds"), msg.text, now,
                     str(msg.sender_id) if msg.sender_id else None),
                )
                added += 1
            tot = conn.execute(
                "SELECT COUNT(*) c FROM telegram_messages WHERE channel=?",
                (want,),
            ).fetchone()["c"]
            print(f"{want}: +{added} mensajes (total {tot})")
    await tg.disconnect()


async def login() -> None:
    tg = client()
    await tg.start()   # interactivo: teléfono + código (una sola vez)
    me = await tg.get_me()
    print(f"Sesión creada como {me.first_name} — listo. El cron ya puede "
          "recolectar solo.")
    await tg.disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true")
    args = ap.parse_args()
    asyncio.run(login() if args.login else collect())
