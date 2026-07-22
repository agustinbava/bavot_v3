"""Loop de aprendizaje — Fase 1: agregador de lecciones (bot puro, 0 tokens).

Analiza los trades cerrados del paper trading y busca patrones por
estrategia, dirección, símbolo, confianza y hora. Marca cada corte como
CONCLUYENTE sólo si tiene muestra suficiente (>= MIN_SAMPLE); si no, lo
reporta como tendencia preliminar. Es el "maker" de datos; el "checker"
(humano o A6) decide si un patrón amerita cambiar el prompt.

Fase 2 (pendiente de muestra): con >=30 cerradas por estrategia, un paso
semanal de LLM lee este reporte + los análisis de los trades perdedores y
propone refinamientos del prompt → se versiona como A6.

Uso: python learn.py              # stats + agente (1 llamada LLM/día)
     python learn.py --no-agent   # sólo las stats deterministas
"""
from __future__ import annotations

from collections import defaultdict

from storage import connect

MIN_SAMPLE = 20  # por debajo de esto, un corte es anécdota, no patrón

CLOSED = ("tp", "sl", "ambiguous", "expired")


def cut(rows, keyfn, label):
    groups = defaultdict(list)
    for r in rows:
        groups[keyfn(r)].append(r)
    print(f"\n-- por {label} --")
    for key, g in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        wins = sum(1 for r in g if (r["pnl_usd"] or 0) > 0)
        pnl = sum(r["pnl_usd"] or 0 for r in g)
        r_sum = sum(r["r_multiple"] or 0 for r in g)
        solid = "CONCLUYENTE" if len(g) >= MIN_SAMPLE else "preliminar "
        print(f"  [{solid}] {str(key):<18} n={len(g):>3}  wr {wins/len(g):>4.0%}  "
              f"P&L {pnl:>+8.2f}  R {r_sum:>+6.1f}")


def agent_step(stats_text: str) -> None:
    """Agente LEARN: lee stats + su memoria, analiza, actualiza memoria y
    puede proponer hipótesis (entran como 'proposed', nunca se corren
    solas). 1 llamada LLM por día — el único costo de learn."""
    import json as _json
    from dotenv import load_dotenv
    load_dotenv(".env")
    from anthropic import Anthropic
    from config import load_config

    soul = open("agents/learn_soul.md").read()
    try:
        memory = open("agents/learn_memory.md").read()
    except FileNotFoundError:
        memory = "(sin memoria previa)"
    cfg = load_config("config.yaml")
    try:
        resp = Anthropic().messages.create(
            model=cfg.anthropic.model, max_tokens=2500, system=soul,
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content":
                       f"TU MEMORIA:\n{memory[:6000]}\n\n"
                       f"STATS DE HOY:\n{stats_text[:8000]}"}],
        )
        raw = next(b.text for b in resp.content if b.type == "text")
        raw = raw.strip().removeprefix("```json").removeprefix("```").strip("` \n")
        out = _json.loads(raw)
    except Exception as exc:
        print(f"\n[agente learn] error: {exc}")
        return

    print("\n=== ANÁLISIS DEL AGENTE LEARN ===")
    print(out.get("analysis", "(sin análisis)"))
    if out.get("memory"):
        open("agents/learn_memory.md", "w").write(out["memory"][:8000])
        print("[agente learn] memoria actualizada")
    props = out.get("proposals") or []
    if props:
        with connect() as conn:
            for p in props:
                conn.execute(
                    "INSERT OR IGNORE INTO hypotheses "
                    "(name, family, params, status, notes) VALUES (?,?,?,?,?)",
                    (p.get("name", "sin-nombre"), p.get("family", "note"),
                     _json.dumps(p.get("params", {})), "proposed",
                     "[propuesta del agente learn] " + (p.get("notes") or "")))
                print(f"[agente learn] propuesta encolada: {p.get('name')} "
                      "(status=proposed, requiere promoción humana)")


def main() -> None:
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM signals WHERE status IN {CLOSED}"
        ).fetchall()]
        no_trigger = conn.execute(
            "SELECT strategy, COUNT(*) n FROM signals "
            "WHERE status='not_triggered' GROUP BY strategy"
        ).fetchall()
        confs = {r["id"]: r["confidence"] for r in conn.execute(
            "SELECT s.id, a.confidence FROM signals s "
            "JOIN analyses a ON a.id = s.analysis_id"
        ).fetchall()}

    print(f"=== LECCIONES DEL PAPER TRADING — {len(rows)} trades cerrados ===")
    if len(rows) < MIN_SAMPLE:
        print(f"⚠ Muestra global chica (<{MIN_SAMPLE}): todo lo de abajo es "
              "PRELIMINAR. No cambiar estrategias en base a esto todavía.")

    cut(rows, lambda r: r["strategy"], "estrategia")
    cut(rows, lambda r: r["direction"], "dirección")
    cut(rows, lambda r: r["type"], "tipo de instrumento")
    cut(rows, lambda r: confs.get(r["id"]) or "?", "confianza del modelo")
    cut(rows, lambda r: r["symbol"], "símbolo")
    cut(rows, lambda r: (r["signal_at"] or "")[11:13] + "h", "hora de la señal")

    if no_trigger:
        print("\n-- señales que nunca gatillaron (entry demasiado lejos) --")
        for r in no_trigger:
            print(f"  {r['strategy']}: {r['n']}")

    # --- A5 (mecánica): vigilancia de deriva vs backtest, NO re-tuning ---
    with connect() as conn:
        try:
            a5 = [dict(r) for r in conn.execute(
                "SELECT * FROM a5_positions WHERE status='closed'").fetchall()]
        except Exception:
            a5 = []
    print(f"\n=== A5 TENDENCIA DIARIA — {len(a5)} posiciones cerradas ===")
    print("(estrategia validada: acá NO se ajustan reglas con pérdidas")
    print(" recientes — sólo se vigila que el vivo replique al backtest)")
    if a5:
        wins = sum(1 for r in a5 if (r["pnl_usd"] or 0) > 0)
        pnl = sum(r["pnl_usd"] or 0 for r in a5)
        wr = wins / len(a5)
        print(f"win rate vivo: {wr:.0%}  (backtest 2 años: ~35-50% según moneda)")
        print(f"P&L neto: {pnl:+.2f} USD")
        # perfil trend-following: pérdidas chicas frecuentes, ganancias grandes raras
        win_avg = (sum(r["pnl_usd"] for r in a5 if r["pnl_usd"] > 0) / wins) if wins else 0
        losses = [r["pnl_usd"] for r in a5 if (r["pnl_usd"] or 0) <= 0]
        loss_avg = sum(losses) / len(losses) if losses else 0
        print(f"ganancia promedio {win_avg:+.2f} vs pérdida promedio {loss_avg:+.2f}"
              f" — el perfil sano es ganancias >> |pérdidas|")
        if len(a5) >= 30 and wr < 0.20:
            print("⚠ DERIVA: win rate vivo muy por debajo del backtest con "
                  "muestra decente — revisar régimen de mercado o implementación.")
        elif len(a5) < 30:
            print(f"(muestra {len(a5)}/30 — sin conclusiones todavía)")
    else:
        print("(ninguna cerrada aún — las posiciones de tendencia viven semanas)")

    # --- flotante vs exposición neta (snapshots horarios del recolector) ---
    with connect() as conn:
        try:
            snaps = [dict(r) for r in conn.execute(
                "SELECT * FROM equity_snapshots ORDER BY ts").fetchall()]
        except Exception:
            snaps = []
    print(f"\n=== FLOTANTE vs EXPOSICIÓN — {len(snaps)} snapshots horarios ===")
    print("LECCIÓN REGISTRADA (2026-07-17): el flotante del libro se mueve")
    print("como (exposición neta) x (dirección del mercado). Con el libro")
    print("net-short en mercado cayendo, el flotante es positivo aunque")
    print("TODAS las long pierdan — no es señal de nada para operar: el")
    print("flotante sólo se realiza en el cruce/rebalanceo, y cerrarlo")
    print("antes ya se testeó (121 combos SL/TP: nada supera a la base).")
    if snaps:
        by_engine = {}
        for s in snaps:
            by_engine.setdefault(s["engine"], []).append(s)
        for eng, rows in sorted(by_engine.items()):
            last = rows[-1]
            flo = last["long_usd"] + last["short_usd"]
            print(f"  {eng}: flotante {flo:+.2f} (long {last['long_usd']:+.2f} / "
                  f"short {last['short_usd']:+.2f}), exposición neta "
                  f"{last['net_exposure_usd']:+.0f} USD, {last['open_n']} abiertas "
                  f"[{last['ts']}]")
        if len(snaps) >= 48:
            print("  (con >=1 semana de snapshots: acá va la correlación "
                  "flotante-vs-exposición para vigilar el riesgo direccional)")

    # --- concentración entre motores (snapshots del recolector) ---
    with connect() as conn:
        try:
            conc = [dict(r) for r in conn.execute(
                "SELECT * FROM concentration_snapshots ORDER BY ts").fetchall()]
        except Exception:
            conc = []
    print(f"\n=== CONCENTRACIÓN ENTRE MOTORES — {len(conc)} snapshots ===")
    print("LECCIÓN REGISTRADA (2026-07-18, caso HOME): cuando tendencia")
    print("(A5.1) y debilidad relativa (B1) coinciden en una moneda, ambos")
    print("motores cargan la MISMA apuesta y el flotante del libro depende")
    print("de un solo nombre. No es un error (cada motor siguió su regla),")
    print("pero es el riesgo a vigilar: diversificación aparente < real.")
    if conc:
        last = conc[-1]
        share = (abs(last["top_float_usd"]) / abs(last["book_float_usd"])
                 if last["book_float_usd"] else 0)
        print(f"  ahora: {last['overlap_n']} monedas repetidas en ambos motores "
              f"({last['overlap_syms'] or '-'})")
        print(f"  mayor apuesta combinada: {last['top_symbol']} "
              f"{last['top_float_usd']:+.2f} USD = {share:.0%} del flotante "
              f"del libro ({last['book_float_usd']:+.2f})")
        if share > 0.5 and abs(last["book_float_usd"]) > 10:
            print("  ⚠ CONCENTRADO: más de la mitad del flotante depende de "
                  "una sola moneda repetida — leer el P&L con ese lente.")

    print("\nUmbral para Fase 2 (refinamiento de prompt vía LLM → A6): "
          f">= 30 cerradas de una misma estrategia vigente. Ver learn.py.")


if __name__ == "__main__":
    import argparse
    import contextlib
    import io
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--no-agent", action="store_true")
    args = ap.parse_args()

    # correr las stats capturando la salida (es el input del agente)
    buf = io.StringIO()

    class _Tee:
        def write(self, s):
            sys.__stdout__.write(s)
            buf.write(s)
        def flush(self):
            sys.__stdout__.flush()

    with contextlib.redirect_stdout(_Tee()):
        main()
    if not args.no_agent:
        agent_step(buf.getvalue())
