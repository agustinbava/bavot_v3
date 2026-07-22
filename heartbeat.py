"""Dead-man switch — el latido que avisa cuando Bavot muere.

Cada 15 min (cron) hace ping a una URL de healthchecks.io, PERO sólo si
el pipeline está sano (último snapshot de equity < 2h). Así la alerta
externa cubre los dos modos de muerte:
  - server caído / sin internet / cron roto → no hay ping → alerta
  - server vivo pero pipeline trabado      → no hay ping → alerta

Setup (una vez, por el usuario): cuenta gratis en healthchecks.io →
crear check "bavot" con period 30 min y grace 30 min → copiar la Ping
URL al .env como HEALTHCHECK_URL=https://hc-ping.com/...
Sin esa variable, el script no hace nada (silencioso).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from storage import connect


def pipeline_healthy() -> bool:
    try:
        with connect() as conn:
            last = conn.execute(
                "SELECT MAX(ts) m FROM equity_snapshots").fetchone()["m"]
        if not last:
            return False
        age_h = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(last)).total_seconds() / 3600
        return age_h < 2
    except Exception:
        return False


def main() -> None:
    load_dotenv(".env")
    url = os.getenv("HEALTHCHECK_URL")
    if not url:
        return  # sin configurar: silencio (no spamear el cron.log)
    if not pipeline_healthy():
        # ping a /fail: healthchecks alerta de inmediato con contexto
        try:
            requests.get(url.rstrip("/") + "/fail", timeout=10)
        except Exception:
            pass
        print("heartbeat: pipeline NO sano — enviado /fail")
        return
    try:
        requests.get(url, timeout=10)
    except Exception as exc:
        print(f"heartbeat: no pude hacer ping ({exc})")


if __name__ == "__main__":
    main()
