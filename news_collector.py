"""Recolector de noticias/sentiment crypto — fase 1: SOLO recolectar.

El "bot de Twitter" pragmático: la API de X cuesta $200/mes (viola la
prioridad de costos), así que se cubren las mismas señales por vías
gratis:
  1. CryptoPanic (agrega noticias + tweets de influencers, filtrable por
     moneda). Requiere key gratuita: cryptopanic.com/developers/api →
     .env CRYPTOPANIC_KEY. Si falta, se saltea con aviso.
  2. Anuncios oficiales de Binance (¡delistings! — el evento que más nos
     importa tras DEXE).
  3. RSS de CoinTelegraph y Decrypt (titulares generales).

Guarda todo en news_items etiquetado con las monedas del universo que
menciona. NO opera ni toca los motores: es materia prima para la
hipótesis event_exit_news (lab), que se validará FORWARD cuando haya
semanas de datos. Cron: cada 30 min.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from config import load_config
from storage import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_items (
    source TEXT NOT NULL,
    ext_id TEXT NOT NULL,
    published_at TEXT,
    title TEXT NOT NULL,
    url TEXT,
    currencies TEXT,           -- CSV de monedas del universo mencionadas
    collected_at TEXT NOT NULL,
    UNIQUE(source, ext_id)
);
"""

RSS_FEEDS = [
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("decrypt", "https://decrypt.co/feed"),
]


def _bases(cfg) -> list[str]:
    return [i.symbol.removesuffix("USDT") for i in cfg.watchlist
            if i.type == "crypto"]


def _tag_currencies(text: str, bases: list[str]) -> str:
    pat = re.compile(r"\b(" + "|".join(map(re.escape, bases)) + r")\b", re.I)
    found = {m.upper() for m in pat.findall(text)}
    return ",".join(sorted(found))


def collect_cryptopanic(conn, bases, now) -> int:
    key = os.getenv("CRYPTOPANIC_KEY")
    if not key:
        print("cryptopanic: sin CRYPTOPANIC_KEY en .env — salteado "
              "(key gratis en cryptopanic.com/developers/api)")
        return 0
    try:
        r = requests.get(
            "https://cryptopanic.com/api/v1/posts/",
            params={"auth_token": key, "currencies": ",".join(bases[:50]),
                    "public": "true"},
            timeout=20)
        r.raise_for_status()
        posts = r.json().get("results", [])
    except Exception as exc:
        print(f"cryptopanic: error {exc}")
        return 0
    n = 0
    for p in posts:
        curr = ",".join(sorted(c.get("code", "") for c in
                               (p.get("currencies") or [])))
        conn.execute(
            "INSERT OR IGNORE INTO news_items VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("cryptopanic", str(p.get("id")), p.get("published_at"),
             (p.get("title") or "")[:400],
             (p.get("url") or "")[:400], curr, now))
        n += 1
    return n


def collect_binance_announcements(conn, bases, now) -> int:
    try:
        r = requests.get(
            "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/"
            "list/query",
            params={"type": 1, "pageNo": 1, "pageSize": 20, "catalogId": 161},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        arts = (r.json().get("data") or {}).get("catalogs", [])
        items = []
        for c in arts:
            items.extend(c.get("articles") or [])
        if not items:
            items = ((r.json().get("data") or {}).get("articles")) or []
    except Exception as exc:
        print(f"binance announcements: error {exc}")
        return 0
    n = 0
    for a in items:
        title = a.get("title") or ""
        ts = a.get("releaseDate")
        pub = (datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
               .isoformat(timespec="seconds") if ts else None)
        conn.execute(
            "INSERT OR IGNORE INTO news_items VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("binance", str(a.get("id") or a.get("code")), pub, title[:400],
             f"https://www.binance.com/en/support/announcement/"
             f"{a.get('code','')}",
             _tag_currencies(title, bases), now))
        n += 1
    return n


def collect_rss(conn, bases, now) -> int:
    n = 0
    for name, url in RSS_FEEDS:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"},
                             timeout=20)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub = (item.findtext("pubDate") or "").strip()
                if not title:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO news_items VALUES "
                    "(?, ?, ?, ?, ?, ?, ?)",
                    (name, link or title, pub, title[:400], link[:400],
                     _tag_currencies(title, bases), now))
                n += 1
        except Exception as exc:
            print(f"{name}: error {exc}")
    return n


def main() -> None:
    load_dotenv(".env")
    cfg = load_config("config.yaml")
    bases = _bases(cfg)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.executescript(_SCHEMA)
        a = collect_cryptopanic(conn, bases, now)
        b = collect_binance_announcements(conn, bases, now)
        c = collect_rss(conn, bases, now)
        tot = conn.execute("SELECT COUNT(*) c FROM news_items").fetchone()["c"]
        tagged = conn.execute(
            "SELECT COUNT(*) c FROM news_items WHERE currencies != ''"
        ).fetchone()["c"]
    print(f"news: +{a} cryptopanic, +{b} binance, +{c} rss | "
          f"total {tot} ({tagged} con moneda del universo)")


if __name__ == "__main__":
    main()
