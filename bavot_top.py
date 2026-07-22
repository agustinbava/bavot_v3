"""bavot_top — monitor de terminal en vivo (estilo `top`), 0 tokens.

Muestra el estado del libro completo refrescando cada 10 s: motores,
posiciones ordenadas por flotante, señales T1 y últimos eventos.

Uso:
    .venv/bin/python bavot_top.py            # en el server
    ssh -t bavot 'cd scalp-analyzer && .venv/bin/python bavot_top.py'
    .venv/bin/python bavot_top.py --once     # un frame y sale (para cron/tests)
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime

import requests
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from storage import connect

REFRESH_S = 10
# la consola física (TERM=linux) no tiene glifos redondeados
import os
_CONSOLE = os.environ.get("TERM") == "linux"
BOX_PANEL = box.ASCII if _CONSOLE else box.ROUNDED
BOX_TABLE = box.ASCII_DOUBLE_HEAD if _CONSOLE else box.SIMPLE_HEAD


def fetch_prices() -> dict:
    try:
        data = requests.get("https://api.binance.com/api/v3/ticker/price",
                            timeout=8).json()
        return {d["symbol"]: float(d["price"]) for d in data}
    except Exception:
        return {}


def money(v: float) -> Text:
    style = "green" if v > 0 else ("red" if v < 0 else "dim")
    return Text(f"{v:+.2f}", style=style)


def build() -> Group:
    prices = fetch_prices()
    engines, positions = [], []
    with connect() as conn:
        for eng, table in (("A5.1", "a5_positions"), ("B1", "b1_positions")):
            rows = [dict(r) for r in conn.execute(
                f"SELECT * FROM {table} WHERE status='open'").fetchall()]
            cl = conn.execute(
                f"SELECT COUNT(*) c, COALESCE(SUM(pnl_usd),0) p, "
                f"SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) w "
                f"FROM {table} WHERE status='closed'").fetchone()
            flo = net = 0.0
            for r in rows:
                px = prices.get(r["symbol"])
                if not px:
                    continue
                s = 1 if r["direction"] == "LONG" else -1
                u = (px - r["entry"]) / r["entry"] * s * r["position_usd"]
                flo += u
                net += s * r["position_usd"]
                positions.append((eng, r["symbol"], r["direction"],
                                  r["position_usd"], u))
            engines.append((eng, len(rows), flo, net, cl["c"], cl["p"],
                            cl["w"] or 0))
        t1 = [dict(r) for r in conn.execute(
            "SELECT * FROM t1_signals ORDER BY id DESC LIMIT 5").fetchall()]

    btc = prices.get("BTCUSDT", 0)
    tot_flo = sum(e[2] for e in engines)
    tot_real = sum(e[5] for e in engines)
    header = Text.assemble(
        ("BAVOT ", "bold cyan"),
        (datetime.now().strftime("%d/%m %H:%M:%S"), "dim"),
        ("   BTC ", "bold"), (f"{btc:,.0f}  ", "yellow"),
        ("flotante ", "bold"), (f"{tot_flo:+.2f}  ",
                                "green" if tot_flo >= 0 else "red"),
        ("realizado ", "bold"), (f"{tot_real:+.2f}",
                                 "green" if tot_real >= 0 else "red"),
    )

    eng_t = Table(box=BOX_TABLE, expand=True, pad_edge=False)
    for col in ("motor", "abiertas", "flotante", "exposición neta",
                "cerradas", "realizado", "efectividad"):
        eng_t.add_column(col, justify="right")
    for eng, n, flo, net, c, p, w in engines:
        eng_t.add_row(Text(eng, style="bold"), str(n), money(flo),
                      f"{net:+.0f}", str(c), money(p),
                      f"{w/c:.0%}" if c else "-")

    positions.sort(key=lambda t: t[4], reverse=True)
    shown = positions[:6] + positions[-6:] if len(positions) > 12 else positions
    pos_t = Table(box=BOX_TABLE, expand=True, pad_edge=False,
                  title="mejores y peores posiciones", title_style="dim")
    for col in ("motor", "símbolo", "dir", "tamaño", "flotante"):
        pos_t.add_column(col, justify="right")
    for eng, sym, d, usd, u in shown:
        pos_t.add_row(eng, sym.removesuffix("USDT"),
                      Text(d, style="green" if d == "LONG" else "red"),
                      f"${usd:.0f}", money(u))

    t1_lines = []
    for s in t1:
        state = s["status"]
        if state == "pending":
            state = "EN POSICIÓN" if s["entry_at"] else "esperando entry"
        px = prices.get(s["symbol"])
        dist = (f"  (px {px:g}, {abs(px/s['entry']-1)*100:.1f}% del entry)"
                if px and not s["entry_at"] and s["status"] == "pending" else "")
        t1_lines.append(
            f"[{s['channel']}] {s['symbol']} {s['direction']} "
            f"entry {s['entry']:g} tp {s['tp']:g} sl {s['sl']:g} "
            f"→ {state}{dist}")
    t1_body = Text("\n".join(t1_lines) if t1_lines
                   else "sin señales todavía")

    return Group(
        Panel(header, box=BOX_PANEL),
        eng_t,
        pos_t,
        Panel(t1_body, title="T1 señales de Telegram", box=BOX_PANEL,
              title_align="left"),
        Text(f"refresh {REFRESH_S}s — Ctrl-C para salir", style="dim"),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    console = Console()
    if args.once:
        console.print(build())
        return
    with Live(build(), console=console, refresh_per_second=1,
              screen=True) as live:
        while True:
            time.sleep(REFRESH_S)
            live.update(build())


if __name__ == "__main__":
    main()
