"""Etiquetador de noticias con LLM LOCAL (Ollama) — costo $0.

El trabajo masivo de bajo riesgo que justifica un modelo local: clasificar
cada noticia recolectada (tipo de evento, sentiment, severidad) para que
cuando la hipótesis event_exit_news se desbloquee, el dataset ya esté
etiquetado. La interpretación de señales de trading (T1) sigue en la API
con auditor — acá la precisión fina no es crítica y el volumen sí.

Requiere Ollama corriendo en localhost:11434 con el modelo MODEL.
Cron: horario (:50), tope de BATCH noticias por corrida.

Uso: python news_tagger.py [--batch 60]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import requests

from storage import connect

OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen2.5:3b"

PROMPT = """Clasificá este titular de noticias cripto. Respondé SOLO JSON:
{"type": "delisting"|"hack"|"regulacion"|"listing"|"mercado"|"proyecto"|"ruido",
 "sentiment": -1|0|1, "severity": 0|1|2|3}
severity: 0=irrelevante, 1=leve, 2=importante, 3=catastrofico/urgente.
Titular: """

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_tags (
    source TEXT NOT NULL,
    ext_id TEXT NOT NULL,
    type TEXT, sentiment INTEGER, severity INTEGER,
    model TEXT, tagged_at TEXT,
    UNIQUE(source, ext_id)
);
"""


def tag(title: str) -> dict | None:
    try:
        r = requests.post(OLLAMA, json={
            "model": MODEL, "stream": False, "format": "json",
            "messages": [{"role": "user", "content": PROMPT + title[:300]}],
            "options": {"temperature": 0, "num_predict": 80},
        }, timeout=60)
        r.raise_for_status()
        out = json.loads(r.json()["message"]["content"])
        if out.get("type") in ("delisting", "hack", "regulacion", "listing",
                               "mercado", "proyecto", "ruido"):
            return out
    except Exception as exc:
        print(f"  tagger error: {exc}")
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=60)
    args = ap.parse_args()

    try:
        requests.get("http://127.0.0.1:11434/api/tags", timeout=5)
    except Exception:
        print("news_tagger: ollama no está corriendo — salteado")
        return

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.executescript(_SCHEMA)
        rows = conn.execute(
            "SELECT n.source, n.ext_id, n.title FROM news_items n "
            "LEFT JOIN news_tags t ON t.source=n.source AND t.ext_id=n.ext_id "
            "WHERE t.ext_id IS NULL ORDER BY n.collected_at DESC LIMIT ?",
            (args.batch,)).fetchall()
        done = 0
        for r in rows:
            out = tag(r["title"])
            if not out:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO news_tags VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r["source"], r["ext_id"], out["type"],
                 int(out.get("sentiment", 0)), int(out.get("severity", 0)),
                 MODEL, now))
            done += 1
        pend = conn.execute(
            "SELECT COUNT(*) c FROM news_items n LEFT JOIN news_tags t "
            "ON t.source=n.source AND t.ext_id=n.ext_id "
            "WHERE t.ext_id IS NULL").fetchone()["c"]
        sev = conn.execute(
            "SELECT COUNT(*) c FROM news_tags WHERE severity >= 2").fetchone()["c"]
    print(f"news_tagger: {done} etiquetadas | {pend} pendientes | "
          f"{sev} severas acumuladas")


if __name__ == "__main__":
    main()
