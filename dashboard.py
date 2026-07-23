"""Local HTML dashboard for Bavot analysis history.

Serves a single page from the SQLite database (bavot.db): stat tiles for the
selected day, plus every run with its per-instrument verdicts and the full
model analysis behind an expandable row.

Usage:
    python dashboard.py            # http://127.0.0.1:8787 (today's runs)
    python dashboard.py --port 9000
"""
from __future__ import annotations

import argparse
import html
from datetime import date
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from storage import fetch_dates, fetch_runs, fetch_signals, fetch_strategies

VERDICT_META = {
    "LONG": ("var(--good)", "▲"),
    "SHORT": ("var(--critical)", "▼"),
    "NO TRADE": ("var(--muted)", "■"),
    "UNPARSED": ("var(--warning)", "?"),
    "ERROR": ("var(--serious)", "!"),
}

_CSS = """
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb;
  --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --border: rgba(11,11,11,0.10);
  --good: #0ca30c; --critical: #d03b3b; --serious: #ec835a; --warning: #b17c00;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #050b16; --surface: #0a1322;
  --ink: #eef4ff; --ink-2: #9fb0cc; --muted: #5d6f8f;
  --grid: #14233c; --border: rgba(96,165,250,0.16);
  --good: #22c55e; --critical: #ef4444; --warning: #f5a623;
  --accent1: #4f9df7; --accent2: #b06ef0;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #050b16; --surface: #0a1322;
    --ink: #eef4ff; --ink-2: #9fb0cc; --muted: #5d6f8f;
    --grid: #14233c; --border: rgba(96,165,250,0.16);
    --good: #22c55e; --critical: #ef4444; --warning: #f5a623;
    --accent1: #4f9df7; --accent2: #b06ef0;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--page); color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 1500px; margin: 0 auto; padding: 24px 24px 64px; }
header { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
h1 { font-size: 20px; margin: 0; }
header .sub { color: var(--ink-2); }
.controls { display: flex; gap: 8px; align-items: center; margin: 16px 0; flex-wrap: wrap; }
.controls a {
  color: var(--ink-2); text-decoration: none; padding: 4px 10px;
  border: 1px solid var(--border); border-radius: 6px; background: var(--surface);
}
.controls a.active { color: var(--ink); font-weight: 600; border-color: var(--ink-2); }
#theme-toggle {
  margin-left: auto; cursor: pointer; border: 1px solid var(--border);
  background: var(--surface); color: var(--ink-2); border-radius: 6px; padding: 4px 10px;
}
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; margin: 16px 0 24px; }
.tile {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 12px 14px;
}
.tile .v { font-size: 26px; font-weight: 650; }
.tile .l { color: var(--ink-2); font-size: 12px; }
a.tile-link { text-decoration: none; color: inherit; display: block; cursor: pointer; }
a.tile-link:hover { border-color: var(--ink-2); }
.run {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; margin-bottom: 16px; overflow: hidden;
}
.run > h2 {
  font-size: 13px; font-weight: 600; color: var(--ink-2);
  margin: 0; padding: 10px 14px; border-bottom: 1px solid var(--grid);
}
table { width: 100%; border-collapse: collapse; }
th {
  text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
  color: var(--muted); font-weight: 600; padding: 8px 14px; border-bottom: 1px solid var(--grid);
}
td { padding: 8px 14px; border-bottom: 1px solid var(--grid); white-space: nowrap; }
td .badge { white-space: nowrap; }
tr:last-child > td { border-bottom: none; }
td.num { font-variant-numeric: tabular-nums; }
.badge { display: inline-flex; align-items: center; gap: 6px; font-weight: 600; }
.badge .dot { font-size: 11px; }
.tag {
  display: inline-block; font-size: 11px; font-weight: 600; letter-spacing: .03em;
  padding: 1px 8px; border-radius: 10px; border: 1px solid var(--border);
  color: var(--ink-2); background: var(--page); vertical-align: middle;
}
details.strategy {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; margin: 0 0 16px; padding: 10px 14px;
}
details.strategy summary { cursor: pointer; font-weight: 600; font-size: 13px; }
details.strategy p { color: var(--ink-2); margin: 6px 0 4px 16px; font-size: 13px; }
details.strategy-item { margin: 8px 0 0 8px; }
details.strategy-item summary { cursor: pointer; font-size: 13px; }
details.sigblock {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; margin-bottom: 16px; overflow: hidden;
}
details.sigblock > summary {
  cursor: pointer; font-size: 13px; font-weight: 600; color: var(--ink-2);
  padding: 10px 14px; list-style-position: inside;
}
details.sigblock[open] > summary { border-bottom: 1px solid var(--grid); }
details.sigblock .tiles { padding: 12px 14px 0; margin: 0 0 12px; }
details.raw summary { cursor: pointer; color: var(--ink-2); font-size: 12px; }
details.raw pre {
  white-space: pre-wrap; font: 12px/1.5 ui-monospace, monospace;
  color: var(--ink-2); background: var(--page); border: 1px solid var(--grid);
  border-radius: 6px; padding: 10px; margin: 8px 0 0; max-height: 420px; overflow: auto;
}
.empty { color: var(--muted); padding: 32px 0; text-align: center; }
footer { color: var(--muted); font-size: 12px; margin-top: 24px; }
:root { --accent1: #4f8ef7; --accent2: #c07ef2; }
.wrap { animation: fadein .3s ease; }
@keyframes fadein { from { opacity: .5; } to { opacity: 1; } }
.tile { transition: transform .15s ease, border-color .15s ease; }
.tile:hover { transform: translateY(-1px); border-color: var(--ink-2); }
.chart-wrap { padding: 8px 14px 4px; }
svg.chart { width: 100%; height: auto; display: block; }
svg.chart text { font: 11px system-ui; fill: var(--muted); }
svg.chart .zero { stroke: var(--grid); stroke-dasharray: 3 3; }
svg.chart polyline { fill: none; stroke-width: 2; stroke-linejoin: round; }
.legend { display: flex; gap: 18px; padding: 4px 14px 12px; color: var(--ink-2); font-size: 12px; flex-wrap: wrap; }
.legend .sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; vertical-align: -1px; }
.chart-ranges { display: flex; gap: 6px; padding: 10px 14px 0; }
.range-btn {
  cursor: pointer; border: 1px solid var(--border); background: var(--surface);
  color: var(--ink-2); border-radius: 6px; padding: 3px 12px; font-size: 12px;
}
.range-btn.active { color: var(--ink); font-weight: 600; border-color: var(--ink-2); }
svg.chart .grid { stroke: var(--grid); stroke-width: 1; opacity: .6; }
h1 { font-size: 24px; letter-spacing: .02em; }
header .sub { text-transform: uppercase; font-size: 11px; letter-spacing: .14em; }
details.sigblock > summary {
  text-transform: uppercase; letter-spacing: .1em; font-size: 12px;
  color: var(--ink);
}
details.sigblock > summary::before { content: "▮ "; color: var(--accent1); }
.tile { border-radius: 10px; }
.tile .v { font-size: 28px; font-variant-numeric: tabular-nums; }
.tile .l {
  text-transform: uppercase; font-size: 10px; letter-spacing: .08em;
  color: var(--muted); margin-top: 2px;
}
.wbar {
  height: 5px; background: var(--grid); border-radius: 3px;
  margin-top: 6px; overflow: hidden;
}
.wbar i {
  display: block; height: 100%; border-radius: 3px;
  background: linear-gradient(90deg, var(--accent1), var(--accent2));
}
.chip { display: inline-block; font-size: 11px; font-weight: 600;
  padding: 1px 9px; border-radius: 10px; white-space: nowrap; }
.chip.lady { background: rgba(79,157,247,.16); color: var(--accent1); }
.chip.jack { background: rgba(176,110,240,.16); color: var(--accent2); }
.chip.other { background: var(--grid); color: var(--ink-2); }
.meter { position: relative; height: 8px; min-width: 96px; border-radius: 4px;
  background: linear-gradient(90deg, rgba(239,68,68,.35), rgba(148,163,184,.12) 50%, rgba(34,197,94,.35)); }
.meter .mk { position: absolute; top: -3px; width: 3px; height: 14px;
  border-radius: 1px; background: var(--ink); box-shadow: 0 0 4px rgba(0,0,0,.4); }
.meter .en { position: absolute; top: 0; width: 1px; height: 8px; background: var(--muted); opacity: .7; }
.tprow { display: flex; align-items: center; gap: 8px; }
.tprow small { color: var(--muted); font-variant-numeric: tabular-nums; min-width: 34px; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
.cols details.sigblock { margin-bottom: 16px; }
@media (max-width: 1150px) { .cols { grid-template-columns: 1fr; } }
.scrollbox { max-height: 380px; overflow-y: auto; }
.scrollbox thead th {
  position: sticky; top: 0; background: var(--surface); z-index: 1;
}
.tile { padding: 10px 12px; }
.tile .v { font-size: 21px; }
.tiles { gap: 6px; margin: 10px 0 14px; grid-template-columns: repeat(auto-fit, minmax(108px, 1fr)); }
details.sigblock .tiles { padding: 10px 12px 0; margin: 0 0 8px; }
td, th { padding: 6px 12px; }
.chart-tip {
  position: fixed; pointer-events: none; z-index: 10; display: none;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 6px; padding: 6px 10px; font-size: 12px; line-height: 1.6;
  box-shadow: 0 4px 14px rgba(0,0,0,.25); font-variant-numeric: tabular-nums;
}
"""

_JS = """
const t = document.getElementById('theme-toggle');
t.addEventListener('click', () => {
  const cur = document.documentElement.dataset.theme
    || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('bavot-theme', next);
});
const saved = localStorage.getItem('bavot-theme');
if (saved) document.documentElement.dataset.theme = saved;

// selector de rango de los gráficos (persistido en localStorage)
const range = localStorage.getItem('bavot-range') || 'Todo';
const views = document.querySelectorAll('.chart-range-view');
if (views.length) {
  let found = false;
  views.forEach(v => { if (v.dataset.range === range) found = true; });
  const eff = found ? range : 'Todo';
  views.forEach(v => { v.style.display = v.dataset.range === eff ? '' : 'none'; });
  document.querySelectorAll('.range-btn').forEach(b => {
    if (b.dataset.range === eff) b.classList.add('active');
    b.addEventListener('click', () => {
      localStorage.setItem('bavot-range', b.dataset.range);
      views.forEach(v => {
        v.style.display = v.dataset.range === b.dataset.range ? '' : 'none'; });
      document.querySelectorAll('.range-btn').forEach(x =>
        x.classList.toggle('active', x === b));
    });
  });
}

// recarga cada 15 s, pausada mientras el mouse está sobre un gráfico
let chartHover = false;
setInterval(() => { if (!chartHover) location.reload(); }, 15000);

const NS = 'http://www.w3.org/2000/svg';
document.querySelectorAll('svg.chart[data-chart]').forEach(svg => {
  const cfg = JSON.parse(svg.dataset.chart);
  const cross = document.createElementNS(NS, 'line');
  cross.setAttribute('stroke', 'var(--muted)');
  cross.setAttribute('stroke-width', '1');
  cross.style.display = 'none';
  svg.appendChild(cross);
  const dots = cfg.series.map(s => {
    const c = document.createElementNS(NS, 'circle');
    c.setAttribute('r', '4'); c.setAttribute('fill', s.color);
    c.style.display = 'none'; svg.appendChild(c); return c;
  });
  const tip = document.createElement('div');
  tip.className = 'chart-tip'; document.body.appendChild(tip);
  const spanX = (cfg.x1 - cfg.x0) || 1, spanY = (cfg.y1 - cfg.y0) || 1;
  const plotW = cfg.W - cfg.L - cfg.R, plotH = cfg.H - cfg.T - cfg.B;
  svg.addEventListener('mousemove', e => {
    const r = svg.getBoundingClientRect();
    const vx = (e.clientX - r.left) / r.width * cfg.W;
    const ts = cfg.x0 + (Math.min(Math.max(vx, cfg.L), cfg.W - cfg.R) - cfg.L)
               / plotW * spanX;
    const rows = [];
    cfg.series.forEach((s, i) => {
      let best = 0, bd = Infinity;
      s.pts.forEach((p, j) => {
        const d = Math.abs(p[0] - ts); if (d < bd) { bd = d; best = j; }
      });
      const p = s.pts[best];
      dots[i].setAttribute('cx', cfg.L + (p[0] - cfg.x0) / spanX * plotW);
      dots[i].setAttribute('cy', cfg.T + (cfg.y1 - p[1]) / spanY * plotH);
      dots[i].style.display = '';
      const sign = p[1] >= 0 ? '+' : '';
      rows.push(`<span style="color:${s.color}">●</span> ${s.name}: ` +
                `<strong>${sign}${p[1].toFixed(2)} $</strong>`);
    });
    const cx = cfg.L + (ts - cfg.x0) / spanX * plotW;
    cross.setAttribute('x1', cx); cross.setAttribute('x2', cx);
    cross.setAttribute('y1', cfg.T); cross.setAttribute('y2', cfg.H - cfg.B);
    cross.style.display = '';
    const d = new Date(ts * 1000);
    const when = d.toLocaleString('es-AR',
      { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    tip.innerHTML = `<div style="color:var(--muted)">${when}</div>` +
                    rows.join('<br>');
    tip.style.display = 'block';
    tip.style.left = Math.min(e.clientX + 14, innerWidth - 200) + 'px';
    tip.style.top = Math.min(e.clientY + 14, innerHeight - 120) + 'px';
  });
  svg.addEventListener('mouseenter', () => { chartHover = true; });
  svg.addEventListener('mouseleave', () => {
    chartHover = false;
    tip.style.display = 'none'; cross.style.display = 'none';
    dots.forEach(d => d.style.display = 'none');
  });
});
"""


def _badge(verdict: str) -> str:
    color, dot = VERDICT_META.get(verdict, ("var(--muted)", "•"))
    return (
        f'<span class="badge"><span class="dot" style="color:{color}">{dot}</span>'
        f"{html.escape(verdict)}</span>"
    )


CLOSED_STATUSES = ("tp", "sl", "ambiguous", "expired")


def _wins_losses(signals: list[dict]) -> tuple[list[dict], list[dict]]:
    # sólo crypto: las stocks están detenidas y el usuario pidió que las
    # stats reflejen únicamente el trabajo vigente (2026-07-17)
    closed = [s for s in signals if s["status"] in CLOSED_STATUSES
              and s.get("pnl_usd") is not None and s.get("type") == "crypto"]
    wins = [s for s in closed if s["pnl_usd"] > 0]
    losses = [s for s in closed if s["pnl_usd"] <= 0]
    return wins, losses


def _a5_wr() -> str:
    """Efectividad (win rate) de A5 sobre posiciones cerradas, o '-'."""
    try:
        with __import__("storage").connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) c, SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) w "
                "FROM a5_positions WHERE status='closed'").fetchone()
        return f"{row['w']/row['c']:.0%}" if row["c"] else "-"
    except Exception:
        return "-"


def _tiles(runs: list[dict], signals: list[dict]) -> str:
    rows = [a for r in runs for a in r["analyses"]]
    counts = {
        "Efectividad A5": _a5_wr(),
        "Long": sum(1 for a in rows if a["verdict"] == "LONG"),
        "Short": sum(1 for a in rows if a["verdict"] == "SHORT"),
        "Errores": sum(1 for a in rows if a["verdict"] in ("ERROR", "UNPARSED")),
    }
    tiles = "".join(
        f'<div class="tile"><div class="v">{v}</div><div class="l">{k}</div></div>'
        for k, v in counts.items()
    )
    wins, losses = _wins_losses(signals)
    tiles += (
        f'<a class="tile tile-link" href="/?trades=win">'
        f'<div class="v" style="color:var(--good)">{len(wins)}</div>'
        f'<div class="l">Trades positivos (crypto) →</div></a>'
        f'<a class="tile tile-link" href="/?trades=loss">'
        f'<div class="v" style="color:var(--critical)">{len(losses)}</div>'
        f'<div class="l">Trades negativos (crypto) →</div></a>'
    )
    return f'<div class="tiles">{tiles}</div>'


def _run_section(run: dict) -> str:
    body_rows = []
    for a in run["analyses"]:
        detail = ""
        raw = a["error"] or a["raw_response"]
        if raw:
            detail = (
                '<details class="raw"><summary>ver análisis</summary>'
                f"<pre>{html.escape(raw)}</pre></details>"
            )
        body_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(a['symbol'])}</strong> "
            f'<span style="color:var(--muted)">{html.escape(a["type"] or "")}</span></td>'
            f"<td>{_badge(a['verdict'] or '?')}</td>"
            f"<td class='num'>{html.escape(a['entry'] or '-')}</td>"
            f"<td class='num'>{html.escape(a['stop_loss'] or '-')}</td>"
            f"<td class='num'>{html.escape(a['take_profit'] or '-')}</td>"
            f"<td class='num'>{html.escape(a['rr'] or '-')}</td>"
            f"<td>{html.escape(a['confidence'] or '-')}</td>"
            f"<td>{detail}</td>"
            "</tr>"
        )
    time_label = run["started_at"].replace("T", " · ")
    strategies = sorted({a.get("strategy") or "?" for a in run["analyses"]})
    tags = " ".join(f'<span class="tag">{html.escape(s)}</span>' for s in strategies)
    return (
        f'<details class="sigblock"><summary>Corrida #{run["id"]} — {time_label} {tags}</summary>'
        "<table><thead><tr>"
        "<th>Ticker</th><th>Veredicto</th><th>Entry</th><th>SL</th><th>TP</th>"
        "<th>R/R</th><th>Conf.</th><th></th>"
        "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table></details>"
    )


SIGNAL_STATUS = {
    "tp": ("var(--good)", "✓ TP"),
    "sl": ("var(--critical)", "✗ SL"),
    "ambiguous": ("var(--critical)", "✗ SL (ambigua)"),
    "expired": ("var(--warning)", "◔ expirada"),
    "pending": ("var(--muted)", "… pendiente"),
    "not_triggered": ("var(--muted)", "– sin gatillar"),
    "invalidated": ("var(--warning)", "⊘ anulada (condición)"),
    "vetoed": ("var(--serious)", "⊗ vetada (auditor)"),
    "invalid": ("var(--serious)", "! inválida"),
}


def _fmt_num(x, suffix: str = "") -> str:
    return f"{x:+.2f}{suffix}" if isinstance(x, (int, float)) else "-"


def _strategies_section() -> str:
    strategies = fetch_strategies()
    if not strategies:
        return ""
    tags = " ".join(
        f'<span class="tag">{html.escape(s["name"])}</span>' for s in strategies
    )
    items = []
    for s in strategies:
        items.append(
            '<details class="strategy-item">'
            f'<summary><span class="tag">{html.escape(s["name"])}</span></summary>'
            f'<p>{html.escape(s["description"] or "(sin descripción)")}</p>'
            "</details>"
        )
    return (
        '<details class="strategy">'
        f"<summary>Estrategias {tags}</summary>"
        + "".join(items)
        + "</details>"
    )


def _signals_table(signals: list[dict], limit: int = 50) -> str:
    rows = _signal_rows(signals, limit)
    return (
        "<table><thead><tr>"
        "<th>Fecha</th><th>Ticker</th><th>Dir</th><th>Pos.</th><th>Entry</th><th>SL</th>"
        "<th>TP</th><th>Resultado</th><th>P&L %</th><th>P&L $ neto</th><th>R</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


def _a5_section() -> str:
    return _journal_section(
        "a5_positions",
        "A5.1 Tendencia Diaria — crypto (sizing por volatilidad, mecánica)")


def _b1_section() -> str:
    return _journal_section(
        "b1_positions",
        "B1 Momentum Relativo — crypto (rank 30d, rebalanceo semanal, "
        "7 long / 7 short, mecánica)")


def _line_chart(series: list[tuple[str, str, list[tuple[float, float]]]],
                height: int = 150) -> str:
    """SVG con líneas (nombre, color, [(ts, valor)…]) + grilla temporal.
    Sin dependencias; el tooltip lo maneja el JS con data-chart."""
    from datetime import datetime as _dt
    import json as _json
    import math

    pts_all = [p for _, _, pts in series for p in pts]
    if len(pts_all) < 3:
        return ""
    W, H, L, R, T, B = 1000, height, 46, 10, 12, 24
    x0 = min(p[0] for p in pts_all)
    x1 = max(p[0] for p in pts_all)
    y0 = min(0.0, min(p[1] for p in pts_all))
    y1 = max(0.0, max(p[1] for p in pts_all))
    if y1 - y0 < 1e-9:
        y1 = y0 + 1
    pad = (y1 - y0) * 0.08
    y0, y1 = y0 - pad, y1 + pad

    def sx(x): return L + (x - x0) / max(x1 - x0, 1) * (W - L - R)
    def sy(y): return T + (y1 - y) / (y1 - y0) * (H - T - B)

    cfg = _json.dumps({
        "W": W, "H": H, "L": L, "R": R, "T": T, "B": B,
        "x0": x0, "x1": x1, "y0": y0, "y1": y1,
        "series": [{"name": n, "color": c,
                    "pts": [[t, round(v, 2)] for t, v in pts]}
                   for n, c, pts in series],
    })
    parts = [f'<svg class="chart" data-chart=\'{cfg}\' viewBox="0 0 {W} {H}" '
             'preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">']

    # grilla temporal: elegir paso para ~5-9 marcas
    span = x1 - x0
    for step in (3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400, 2 * 86400,
                 7 * 86400):
        if span / step <= 9:
            break
    first = math.ceil(x0 / step) * step
    t = first
    while t < x1:
        gx = sx(t)
        parts.append(f'<line class="grid" x1="{gx:.1f}" y1="{T}" '
                     f'x2="{gx:.1f}" y2="{H-B}"/>')
        d = _dt.fromtimestamp(t)
        label = d.strftime("%H:%M") if step < 86400 else d.strftime("%d/%m")
        parts.append(f'<text x="{gx-16:.1f}" y="{H-8}">{label}</text>')
        t += step

    zy = sy(0)
    parts.append(f'<line class="zero" x1="{L}" y1="{zy:.1f}" '
                 f'x2="{W-R}" y2="{zy:.1f}"/>')
    parts.append(f'<text x="4" y="{sy(y1)+10:.0f}">{y1:+.0f}$</text>')
    parts.append(f'<text x="4" y="{sy(y0):.0f}">{y0:+.0f}$</text>')
    fill_names = {"Total"} if len(series) > 1 else {series[0][0]}
    for name, color, pts in series:
        if name in fill_names and len(pts) >= 2:
            area = (" ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in pts)
                    + f" {sx(pts[-1][0]):.1f},{sy(0):.1f}"
                    + f" {sx(pts[0][0]):.1f},{sy(0):.1f}")
            fc = "var(--accent1)" if name == "Total" else color
            parts.append(f'<polygon points="{area}" fill="{fc}" '
                         'opacity="0.12" stroke="none"/>')
    for _, color, pts in series:
        coords = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in pts)
        parts.append(f'<polyline points="{coords}" stroke="{color}"/>')
    parts.append("</svg>")
    return "".join(parts)


RANGES = (("24h", 86400), ("3d", 3 * 86400), ("7d", 7 * 86400), ("Todo", None))


def _charts_section() -> str:
    from datetime import datetime as _dt
    with __import__("storage").connect() as conn:
        try:
            snaps = [dict(r) for r in conn.execute(
                "SELECT * FROM equity_snapshots ORDER BY ts").fetchall()]
        except Exception:
            return ""
    if len(snaps) < 3:
        return ""
    per = {}
    for s in snaps:
        ts = _dt.fromisoformat(s["ts"]).timestamp()
        per.setdefault(s["engine"], []).append(
            (ts, s["long_usd"] + s["short_usd"], s["net_exposure_usd"]))
    colors = {"A5.1": "var(--accent1)", "B1": "var(--accent2)"}
    t_max = max(rows[-1][0] for rows in per.values())
    last = {eng: rows[-1][1] for eng, rows in per.items()}

    views, buttons = [], []
    for label, secs in RANGES:
        cut = (t_max - secs) if secs else float("-inf")
        flo_series, tot = [], {}
        for eng, rows in sorted(per.items()):
            pts = [(t, f) for t, f, _ in rows if t >= cut]
            if len(pts) >= 3:
                flo_series.append((eng, colors.get(eng, "var(--warning)"), pts))
            for t, f, _ in rows:
                if t >= cut:
                    tot[t] = tot.get(t, 0.0) + f
        if len(tot) < 3:
            continue
        flo_series.append(("Total", "var(--ink)", sorted(tot.items())))
        chart1 = _line_chart(flo_series)
        neta = [("Exposición neta A5.1", "var(--warning)",
                 [(t, n) for t, _, n in per.get("A5.1", []) if t >= cut])]
        chart2 = _line_chart(neta, height=90)
        if not chart1:
            continue
        leg1 = "".join(
            f'<span><span class="sw" style="background:{c}"></span>'
            f"{html.escape(n)}"
            + (f" {last[n]:+.2f} $" if n in last else
               f" {sum(last.values()):+.2f} $" if n == "Total" else "")
            + "</span>"
            for n, c, _ in flo_series)
        views.append(
            f'<div class="chart-range-view" data-range="{label}">'
            f'<div class="chart-wrap">{chart1}</div>'
            f'<div class="legend">{leg1}</div>'
            + (f'<div class="chart-wrap">{chart2}</div>'
               '<div class="legend"><span><span class="sw" '
               'style="background:var(--warning)"></span>Exposición neta '
               "A5.1 (+long / −short)</span></div>" if chart2 else "")
            + "</div>")
        buttons.append(f'<button class="range-btn" data-range="{label}">'
                       f"{label}</button>")
    if not views:
        return ""
    return (
        '<details class="sigblock" open>'
        "<summary>Evolución — flotante por motor y exposición neta "
        "(snapshots horarios)</summary>"
        f'<div class="chart-ranges">{"".join(buttons)}</div>'
        + "".join(views) + "</details>"
    )


def _gates_section() -> str:
    try:
        import gates as _g
        with __import__("storage").connect() as conn:
            gs = _g.gate_status(conn)
    except Exception:
        return ""
    met = sum(1 for x in gs if x["met"])
    rows = []
    for x in gs:
        icon = ('<span style="color:var(--good)">✓</span>' if x["met"]
                else '<span style="color:var(--muted)">○</span>')
        bar = ""
        if x["progress"] is not None and not x["met"]:
            bar = (f'<div class="wbar" style="max-width:160px;margin-top:5px">'
                   f'<i style="width:{x["progress"]*100:.0f}%"></i></div>')
        rows.append(
            '<div style="padding:8px 0;border-bottom:1px solid var(--grid)">'
            f'<div style="display:flex;gap:10px;align-items:baseline">'
            f'<span style="font-size:15px">{icon}</span>'
            f'<strong style="font-size:13px">{html.escape(x["name"])}</strong></div>'
            f'<div style="color:var(--ink-2);font-size:12px;margin-left:26px">'
            f'{html.escape(x["detail"])}</div>{bar}</div>')
    return (
        '<details class="sigblock">'
        f"<summary>Go-live — progreso hacia dinero real ({met}/5 gates)</summary>"
        '<div style="padding:4px 14px 12px">' + "".join(rows) + "</div></details>")


def _t1_section() -> str:
    with __import__("storage").connect() as conn:
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM t1_signals ORDER BY id DESC LIMIT 30"
            ).fetchall()]
            an = conn.execute(
                "SELECT value FROM t1_state WHERE key='analyzed_total'"
            ).fetchone()
            analyzed = an["value"] if an else "0"
            agg = conn.execute(
                "SELECT COUNT(*) c, "
                "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) p, "
                "SUM(CASE WHEN status IN ('tp','sl','ambiguous','expired') "
                "THEN 1 ELSE 0 END) cl, "
                "SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) w, "
                "COALESCE(SUM(pnl_usd),0) pnl FROM t1_signals").fetchone()
        except Exception:
            return ""
    prices = {}
    try:
        import requests as _rq
        prices = {d["symbol"]: float(d["price"]) for d in _rq.get(
            "https://api.binance.com/api/v3/ticker/price", timeout=5).json()}
    except Exception:
        pass
    wr = (f"{agg['w']/agg['cl']:.0%}" if agg["cl"] else "-")
    pnl_v = agg["pnl"] or 0
    pnl_c = "var(--good)" if pnl_v > 0 else ("var(--critical)" if pnl_v < 0 else "var(--ink)")
    tiles = (
        '<div class="tiles">'
        f'<div class="tile"><div class="v">{analyzed}</div>'
        '<div class="l">Mensajes analizados (LLM)</div></div>'
        f'<div class="tile"><div class="v">{agg["c"] or 0}</div>'
        '<div class="l">Señales interpretadas</div></div>'
        f'<div class="tile"><div class="v">{agg["p"] or 0}</div>'
        '<div class="l">Vivas (esperando / en posición)</div></div>'
        f'<div class="tile"><div class="v">{agg["cl"] or 0}</div>'
        '<div class="l">Cerradas</div></div>'
        f'<div class="tile"><div class="v">{wr}</div>'
        '<div class="l">Efectividad</div>'
        + (f'<div class="wbar"><i style="width:{agg["w"]/agg["cl"]*100:.0f}%"></i></div>' if agg["cl"] else "")
        + '</div>'
        f'<div class="tile"><div class="v" style="color:{pnl_c}">${pnl_v:+.2f}</div>'
        '<div class="l">P&L neto</div></div></div>'
    )

    def _meter(direction, entry, sl, tp, px):
        """Barra SL↔TP con marca del precio actual (0=SL, 1=TP)."""
        span = tp - sl
        if not span or px is None:
            return "-"
        pos = min(max((px - sl) / span, 0.0), 1.0) * 100
        en = min(max((entry - sl) / span, 0.0), 1.0) * 100
        return (f'<div class="meter"><span class="en" style="left:{en:.0f}%"></span>'
                f'<span class="mk" style="left:calc({pos:.0f}% - 1px)"></span></div>')

    ch_class = {"lady market": "lady", "v.i.p de jack": "jack",
                "cripto with jack": "jack"}
    body = []
    for r in rows:
        color, label = SIGNAL_STATUS.get(r["status"], ("var(--muted)", r["status"]))
        px = prices.get(r["symbol"])
        sign = 1 if r["direction"] == "LONG" else -1
        px_label = f"{px:g}" if px else "-"
        flo_html = "-"
        meter = "-"
        if r["status"] == "pending" and r["entry_at"]:
            label = "▶ en posición"
            meter = _meter(r["direction"], r["entry"], r["sl"], r["tp"], px)
            if px:
                u = (px - r["entry"]) / r["entry"] * sign / 100 * \
                    r["position_usd"] * 100
                fc = "var(--good)" if u >= 0 else "var(--critical)"
                flo_html = f"<span style='color:{fc}'>{u:+.2f} $</span>"
        elif r["status"] == "pending" and px:
            dist = abs(px / r["entry"] - 1) * 100
            label = f"… esperando entry ({dist:.1f}%)"
        pnl = f"{r['pnl_usd']:+.2f} $" if r["pnl_usd"] is not None else flo_html
        cc = ch_class.get(r["channel"], "other")
        body.append(
            "<tr>"
            f"<td>{html.escape(r['msg_date'][:16].replace('T', ' '))}</td>"
            f"<td><span class='chip {cc}'>{html.escape(r['channel'])}</span></td>"
            f"<td><strong>{html.escape(r['symbol'])}</strong></td>"
            f"<td>{_badge(r['direction'])}</td>"
            f"<td class='num'>{r['entry']:g}</td>"
            f"<td class='num'>{r['sl']:g}</td>"
            f"<td class='num'>{r['tp']:g}</td>"
            f"<td class='num'>{px_label}</td>"
            f"<td>{meter}</td>"
            f"<td><span style='color:{color}'>{label}</span>"
            f"{' <span class=\"tag\">defaults</span>' if r['defaults_used'] else ''}</td>"
            f"<td class='num'>{pnl}</td>"
            "</tr>"
        )
    table_html = (
        '<div class="scrollbox">'
        "<table><thead><tr><th>Mensaje</th><th>Canal</th><th>Símbolo</th>"
        "<th>Dir</th><th>Entry</th><th>SL</th><th>TP</th><th>Precio</th>"
        "<th>SL ↔ TP</th><th>Estado</th><th>Flotante / P&L</th>"
        "</tr></thead><tbody>" + "".join(body)
        + "</tbody></table></div>"
    ) if body else ('<div class="empty">Sin señales todavía — T1 escucha los '
                    'canales cada 15 min (forward-only desde 2026-07-18).</div>')
    return (
        '<details class="sigblock" open>'
        "<summary>T1 Señales de Telegram — paper trading (Lady Market + "
        "Cripto with Jack, $100/señal, defaults SL 3% / TP 6%)</summary>"
        f"{tiles}{table_html}</details>"
    )


def _journal_section(table: str, title: str) -> str:
    with __import__("storage").connect() as conn:
        try:
            open_rows = [dict(r) for r in conn.execute(
                f"SELECT * FROM {table} WHERE status='open' ORDER BY symbol"
            ).fetchall()]
            closed = conn.execute(
                "SELECT COUNT(*) c, COALESCE(SUM(pnl_usd),0) p, "
                "SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) w "
                f"FROM {table} WHERE status='closed'"
            ).fetchone()
        except Exception:
            return ""
    if not open_rows and not closed["c"]:
        return ""

    # precios spot actuales (una llamada, gratis)
    prices = {}
    try:
        import requests as _rq
        data = _rq.get("https://api.binance.com/api/v3/ticker/price",
                       timeout=5).json()
        prices = {d["symbol"]: float(d["price"]) for d in data}
    except Exception:
        pass

    from a5_daily_trend import FEE_RT_PCT

    floating = 0.0
    ranked = []  # (flotante, html) para ordenar de mayor a menor
    for r in open_rows:
        pos_usd = r["position_usd"] if "position_usd" in r.keys() else 100.0
        entry_fee = pos_usd * (FEE_RT_PCT / 2) / 100  # media vuelta al abrir
        px = prices.get(r["symbol"])
        px_label = f"{px:g}" if px else "-"
        upnl_html = "-"
        uusd = float("-inf")  # sin precio → al fondo
        if px:
            sign = 1 if r["direction"] == "LONG" else -1
            upnl = (px - r["entry"]) / r["entry"] * 100 * sign
            uusd = upnl / 100 * pos_usd
            floating += uusd
            color = "var(--good)" if uusd >= 0 else "var(--critical)"
            upnl_html = f'<span style="color:{color}">{uusd:+.2f} $</span>'
        ranked.append((uusd,
            "<tr>"
            f"<td><strong>{html.escape(r['symbol'])}</strong></td>"
            f"<td>{_badge(r['direction'])}</td>"
            f"<td class='num'>${pos_usd:.0f}</td>"
            f"<td class='num'>{r['entry']:g}</td>"
            f"<td class='num'>{px_label}</td>"
            f"<td>{html.escape(r['opened_at'][:10])}</td>"
            f"<td class='num'>{entry_fee:.2f} $</td>"
            f"<td class='num'>{upnl_html}</td>"
            "</tr>"
        ))
    ranked.sort(key=lambda t: t[0], reverse=True)
    rows = [h for _, h in ranked]
    net = sum((r["position_usd"] if "position_usd" in r.keys() else 100.0)
              * (1 if r["direction"] == "LONG" else -1) for r in open_rows)
    net_label = "long" if net > 0 else ("short" if net < 0 else "neutral")
    wr = f"{closed['w']/closed['c']:.0%}" if closed["c"] else "-"
    expect = (f"{closed['p']/closed['c']:+.2f} $" if closed["c"] else "-")
    tiles = (
        f'<div class="tiles">'
        f'<div class="tile"><div class="v">{len(open_rows)}</div><div class="l">Posiciones abiertas</div></div>'
        f'<div class="tile"><div class="v">${net:+,.0f}</div>'
        f'<div class="l">Exposición neta ({net_label})</div></div>'
        f'<div class="tile"><div class="v">{closed["c"]}</div><div class="l">Cerradas</div></div>'
        f'<div class="tile"><div class="v">{wr}</div><div class="l">Efectividad (trades cerrados)</div>'
        + (f'<div class="wbar"><i style="width:{closed["w"]/closed["c"]*100:.0f}%"></i></div>' if closed["c"] else "")
        + '</div>'
        f'<div class="tile"><div class="v">{expect}</div><div class="l">Expectativa por trade</div></div>'
        f'<div class="tile"><div class="v">${closed["p"]:+.2f}</div><div class="l">P&L neto (cerradas)</div></div>'
        f'<div class="tile"><div class="v" style="color:var(--muted)">≈${floating:+.2f}</div>'
        f'<div class="l">Flotante</div></div>'
        f"</div>"
    )
    table_html = (
        "<table><thead><tr><th>Símbolo</th><th>Dir</th><th>Tamaño</th>"
        "<th>Entry</th><th>Precio</th><th>Desde</th><th>Comisión entrada</th>"
        "<th>Flotante</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
    ) if rows else '<div class="empty">Sin posiciones abiertas.</div>'
    if rows:
        table_html = f'<div class="scrollbox">{table_html}</div>'
    return (
        f'<details class="sigblock" open>'
        f"<summary>{title}</summary>"
        f"{tiles}{table_html}</details>"
    )


def _signals_section(signals: list[dict]) -> str:
    if not signals:
        return (
            '<section class="run"><h2>Señales (paper trading)</h2>'
            '<div class="empty">Todavía no hubo veredictos LONG/SHORT. Cuando '
            "aparezca uno, acá vas a ver cómo le fue.</div></section>"
        )

    open_sigs = [s for s in signals if s["status"] == "pending"]
    closed_sigs = [s for s in signals if s["status"] != "pending"]

    closed = [s for s in signals if s["status"] in CLOSED_STATUSES]
    wins = sum(1 for s in closed if s["status"] == "tp")
    total_r = sum(s["r_multiple"] or 0 for s in closed)
    total_usd = sum(s.get("pnl_usd") or 0 for s in closed)
    total_fees = sum(s.get("fees_usd") or 0 for s in closed)
    invested = sum(s.get("position_usd") or 0 for s in closed)
    floating = sum(
        s.get("unrealized_usd") or 0
        for s in open_sigs if s.get("entry_at")
    )
    in_position = sum(1 for s in open_sigs if s.get("entry_at"))
    win_rate = f"{100 * wins / len(closed):.0f}%" if closed else "-"
    usd_label = (
        f"P&L neto (${invested:.0f} operados, ${total_fees:.2f} comisiones)"
        if invested else "P&L neto (cerradas)"
    )
    equity = total_usd + floating
    tiles = (
        f'<div class="tiles">'
        f'<div class="tile"><div class="v">{len(signals)}</div><div class="l">Señales</div></div>'
        f'<div class="tile"><div class="v">{win_rate}</div><div class="l">Win rate</div></div>'
        f'<div class="tile"><div class="v">{total_r:+.1f}R</div><div class="l">R acumulado</div></div>'
        f'<div class="tile"><div class="v">${total_usd:+.2f}</div><div class="l">{usd_label}</div></div>'
        f'<div class="tile"><div class="v" style="color:var(--muted)">≈${floating:+.2f}</div>'
        f'<div class="l">Flotante ({in_position} en posición)</div></div>'
        f'<div class="tile"><div class="v">≈${equity:+.2f}</div>'
        f'<div class="l">Billetera (realizado + flotante)</div></div>'
        f"</div>"
    )

    open_body = (
        _signals_table(open_sigs)
        if open_sigs else '<div class="empty">Sin señales abiertas ahora.</div>'
    )
    closed_body = (
        _signals_table(closed_sigs, limit=200)
        if closed_sigs else '<div class="empty">Todavía no hay señales cerradas.</div>'
    )
    return (
        f'<details class="sigblock" open>'
        f"<summary>Señales abiertas ({len(open_sigs)}) — paper trading</summary>"
        f"{tiles}{open_body}</details>"
        f'<details class="sigblock">'
        f"<summary>Señales cerradas ({len(closed_sigs)})</summary>"
        f"{closed_body}</details>"
    )


def _signal_rows(signals: list[dict], limit: int = 50) -> str:
    rows = []
    for s in signals[:limit]:
        tag = s.get("strategy") or "?"
        lev = s.get("leverage") or 1
        lev_html = f' <span class="tag">×{lev:g}</span>' if lev > 1 else ""
        pos = s.get("position_usd")
        pos_label = f"${pos:g}" if pos else "-"

        pnl_pct_cell = _fmt_num(s["pnl_pct"], "%")
        pnl_usd_cell = _fmt_num(s.get("pnl_usd"), " $")

        if s["status"] == "pending":
            if s.get("entry_at"):
                label = "⧗ en posición"
                lcolor = ("var(--good)" if (s.get("unrealized_usd") or 0) >= 0
                          else "var(--critical)")
            else:
                label = "… esperando entrada"
                lcolor = "var(--muted)"
            extra = ""
            if s.get("last_price") is not None:
                extra = (f'<div style="color:var(--muted);font-size:11px">'
                         f'px {s["last_price"]:g}</div>')
            if s.get("latest_signal") and s["latest_signal"] != s["direction"]:
                extra += (f'<div style="color:var(--warning);font-size:11px">'
                          f'⚠ señal ahora: {html.escape(s["latest_signal"])}</div>')
            status_cell = (f'<span class="badge" style="color:{lcolor}">{label}'
                           f"</span>{extra}")
            if s.get("unrealized_pct") is not None:
                pnl_pct_cell = (f'<span style="color:var(--muted)">'
                                f'≈{s["unrealized_pct"]:+.2f}%</span>')
                pnl_usd_cell = (f'<span style="color:var(--muted)">'
                                f'≈{s["unrealized_usd"]:+.2f} $</span>')
        else:
            color, label = SIGNAL_STATUS.get(
                s["status"], ("var(--muted)", s["status"])
            )
            status_cell = f'<span class="badge" style="color:{color}">{label}</span>'

        rows.append(
            "<tr>"
            f"<td>{html.escape(s['signal_at'][5:16].replace('T', ' '))}</td>"
            f"<td><strong>{html.escape(s['symbol'])}</strong> "
            f'<span class="tag">{html.escape(tag)}</span></td>'
            f"<td>{_badge(s['direction'])}{lev_html}</td>"
            f"<td class='num'>{pos_label}</td>"
            f"<td class='num'>{s['entry'] or '-'}</td>"
            f"<td class='num'>{s['sl'] or '-'}</td>"
            f"<td class='num'>{s['tp'] or '-'}</td>"
            f"<td>{status_cell}</td>"
            f"<td class='num'>{pnl_pct_cell}</td>"
            f"<td class='num'>{pnl_usd_cell}</td>"
            f"<td class='num'>{_fmt_num(s['r_multiple'], 'R')}</td>"
            "</tr>"
        )
    return "".join(rows)


def _page_shell(title_html: str, body_html: str) -> str:
    return f"""<!doctype html>
<html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bavot — dashboard</title>
<style>{_CSS}</style>
</head><body>
<div class="wrap">
  {title_html}
  {body_html}
  <footer>Análisis técnico, no consejo financiero. La página se recarga cada 15 s (pausado mientras inspeccionás un gráfico).</footer>
</div>
<script>{_JS}</script>
</body></html>"""


def render_trades_page(kind: str) -> str:
    signals = [s for s in fetch_signals() if s["status"] != "conflict"]
    wins, losses = _wins_losses(signals)
    selected = wins if kind == "win" else losses
    label = "positivos" if kind == "win" else "negativos"
    color = "var(--good)" if kind == "win" else "var(--critical)"
    total = sum(s["pnl_usd"] for s in selected)
    total_r = sum(s.get("r_multiple") or 0 for s in selected)

    header = (
        '<header><h1>Bavot</h1>'
        f'<span class="sub">trades {label} (histórico completo)</span></header>'
        '<div class="controls"><a href="/">← volver al dashboard</a>'
        '<button id="theme-toggle" title="Cambiar tema">◐ tema</button></div>'
    )
    tiles = (
        f'<div class="tiles">'
        f'<div class="tile"><div class="v" style="color:{color}">{len(selected)}</div>'
        f'<div class="l">Trades {label}</div></div>'
        f'<div class="tile"><div class="v">${total:+.2f}</div><div class="l">P&L neto total</div></div>'
        f'<div class="tile"><div class="v">{total_r:+.1f}R</div><div class="l">R total</div></div>'
        f"</div>"
    )
    if selected:
        body = f'<section class="run"><h2>Detalle</h2>{_signals_table(selected, limit=500)}</section>'
    else:
        body = f'<div class="empty">Todavía no hay trades {label} cerrados.</div>'
    return _page_shell(header, tiles + body)


def render_a5_page() -> str:
    header = (
        '<header><h1>Bavot</h1>'
        '<span class="sub">A5.1 tendencia + B1 momentum relativo — crypto</span></header>'
        '<div class="controls">'
        '<a href="/?view=full">histórico completo (estrategias anteriores)</a>'
        '<button id="theme-toggle" title="Cambiar tema">◐ tema</button></div>'
    )
    return _page_shell(header, _charts_section() + _gates_section() + '<div class="cols">' + _a5_section() + _b1_section() + '</div>' + _t1_section())


def render_page(selected_date: str | None) -> str:
    runs = fetch_runs(None)  # archivo: siempre todas las corridas

    body = "".join(_run_section(r) for r in runs) if runs else (
        '<div class="empty">Sin corridas para esta fecha. '
        "Ejecutá <code>python main.py</code> para generar análisis.</div>"
    )

    # 'conflict' rows are bookkeeping (blocked duplicates), not positions.
    signals = [s for s in fetch_signals() if s["status"] != "conflict"]
    return f"""<!doctype html>
<html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bavot — dashboard</title>
<style>{_CSS}</style>
</head><body>
<div class="wrap">
  <header><h1>Bavot</h1><span class="sub">archivo histórico (era LLM, congelado 2026-07-16)</span></header>
  <div class="controls"><a href="/">← volver al panel en vivo</a>
    <a class="" href="/?trades=win">trades positivos (crypto)</a>
    <a class="" href="/?trades=loss">trades negativos (crypto)</a>
    <button id="theme-toggle" title="Cambiar tema">◐ tema</button>
  </div>
  {_strategies_section()}
  {_signals_section(signals)}
  {body}
  <footer>Análisis técnico, no consejo financiero. La página se recarga cada 15 s (pausado mientras inspeccionás un gráfico).</footer>
</div>
<script>{_JS}</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/":
            self.send_error(404)
            return
        q = parse_qs(parsed.query)
        trades = q.get("trades", [None])[0]
        view = q.get("view", [None])[0]
        if trades in ("win", "loss"):
            page = render_trades_page(trades).encode()
        elif view == "full" or q.get("date"):
            selected = q.get("date", [None])[0]
            page = render_page(selected).encode()
        else:
            page = render_a5_page().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *args) -> None:  # silence per-request logs
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Bavot local dashboard.")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = HTTPServer((args.host, args.port), Handler)
    print(f"Bavot dashboard → http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
