"""Laboratorio de hipótesis — genera, gatea y backtestea SOLO (0 tokens).

Cierra el loop de aprendizaje sin caer en el auto-overfitting:
  1. Toda idea vive en la tabla `hypotheses` con un estado:
     rejected      → refutada con datos; NUNCA se re-corre (lista negra)
     waiting_data  → esperando datos nuevos (fecha not_before o gate de datos)
     pending       → lista para testear en la próxima corrida
     confirmed     → PASÓ el protocolo; espera el OK del usuario
     active        → activada por el usuario
  2. El runner (cron semanal) corre lo desbloqueado con el protocolo:
     - variantes nuevas: TRAIN/TEST — valida sólo si supera a la base
       en Sharpe en AMBAS mitades (más estricto que elegir-por-TRAIN,
       como corresponde a un proceso automático).
     - re-tests (waiting_data con cutoff): se evalúan SÓLO sobre datos
       posteriores al congelamiento de la hipótesis (fold 100% virgen).
  3. Nada se activa solo: `confirmed` es el final del camino automático.

Uso:  python lab.py            # corre lo que esté desbloqueado
      python lab.py --list     # estado de todas las hipótesis
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime

import requests
import numpy as np
import pandas as pd

from config import load_config
from storage import connect

FEE = 0.0005
TARGET_VOL, CAP_LO, CAP_HI = 0.03, 0.33, 3.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    family TEXT NOT NULL,          -- sl_tp | tp_pullback | retest_cutoff | oi_confirm | note
    params TEXT NOT NULL,          -- JSON
    status TEXT NOT NULL,          -- rejected|waiting_data|pending|confirmed|active
    not_before TEXT,               -- no correr antes de esta fecha
    cutoff TEXT,                   -- re-tests: evaluar sólo datos > cutoff
    result TEXT,                   -- JSON del último test
    notes TEXT,
    tested_at TEXT
);
"""

# Lista negra: refutadas 2026-07-16/17 (detalle en RESEARCH.md). El seed
# las inserta como rejected para que ningún proceso las re-proponga.
BLACKLIST = [
    ("tp_fijo_y_esperar_cruce", "grilla 11 TPs: ninguna supera a la base"),
    ("sl_fijo", "grilla 11 SLs: la ganadora en TRAIN colapsa en TEST"),
    ("sl_tp_combinado", "121 combos: 118 peores que la base ya en TRAIN"),
    ("trailing_stop", "TEST peor que base"),
    ("filtro_macd", "mide lo mismo que el cruce, agrega churn"),
    ("regimen_global_btc", "switching no pasa TRAIN/TEST"),
    ("regimen_por_moneda", "agree200 destruye valor en TRAIN"),
    ("suspension_longs_perdiendo", "6 variantes: ninguna supera a la base"),
    ("funding_contrario", "sin edge: el funding extremo acompaña tendencia"),
    ("funding_como_filtro", "empeora A5.1 ya en TRAIN"),
    ("ensamble_velocidades", "20/100 empata, 50/200 no existe en crypto"),
    ("momentum_90d", "TEST -$308: el momentum crypto es de ciclo corto"),
]

SYMBOLS_CACHE: dict[str, np.ndarray] = {}


def fetch(sym: str) -> np.ndarray:
    if sym not in SYMBOLS_CACHE:
        r = requests.get("https://api.binance.com/api/v3/klines",
                         params={"symbol": sym, "interval": "1d",
                                 "limit": 1000}, timeout=15)
        r.raise_for_status()
        SYMBOLS_CACHE[sym] = np.array(
            [[float(k[0]) / 1000, float(k[4])] for k in r.json()[:-1]])
    return SYMBOLS_CACHE[sym]


def universe() -> list[str]:
    cfg = load_config("config.yaml")
    return [i.symbol for i in cfg.watchlist if i.type == "crypto"]


def sim_variant(closes: np.ndarray, p: dict) -> np.ndarray:
    """Simulador genérico A5.1 + variante (sl/tp/pullback). $ por día."""
    c = closes
    cs = pd.Series(c)
    sma_f = cs.rolling(p.get("fast", 10)).mean().values
    sma_s = cs.rolling(p.get("slow", 40)).mean().values
    sign = np.where(sma_f > sma_s, 1, -1)
    vol = cs.pct_change().rolling(30).std().values
    ref = {"sma10": sma_f, "sma40": sma_s}.get(p.get("pullback"))
    sl, tp = p.get("sl"), p.get("tp")
    n = len(c)
    out = np.zeros(n)
    pos, w, entry, waiting, stopped = 0, 100.0, 0.0, False, False
    for i in range(41, n):
        if sign[i] != sign[i - 1]:
            waiting = stopped = False
        day = pos * w * (c[i] / c[i - 1] - 1) if pos else 0.0
        target = 0 if stopped else sign[i]
        if waiting and ref is not None:
            near = (sign[i] == 1 and c[i] <= ref[i] * 1.01) or \
                   (sign[i] == -1 and c[i] >= ref[i] * 0.99)
            target = sign[i] if near else 0
            if near:
                waiting = False
        elif pos:
            exc = pos * (c[i] / entry - 1)
            if sl is not None and exc <= -sl:
                target, stopped = 0, True
            elif tp is not None and exc >= tp:
                if ref is not None:
                    target, waiting = 0, True
                else:
                    target, stopped = 0, True
        if target != pos:
            if pos:
                day -= w * FEE
            if target:
                v = vol[i - 1]
                w = 100 * float(np.clip(TARGET_VOL / v, CAP_LO, CAP_HI)) \
                    if v == v and v > 0 else 100.0
                day -= w * FEE
                entry = c[i]
            pos = target
        out[i] = day
    return out


def portfolio_daily(p: dict, after_ts: float | None = None):
    per, times = [], []
    for s in universe():
        try:
            arr = fetch(s)
        except Exception:
            continue
        d = sim_variant(arr[:, 1], p)
        t = arr[:, 0]
        if after_ts:
            mask = t > after_ts
            d, t = d[mask], t[mask]
        per.append(d)
    m = max(len(x) for x in per)
    al = np.zeros((len(per), m))
    for j, x in enumerate(per):
        al[j, m - len(x):] = x
    return al.sum(axis=0)


def sharpe(seg: np.ndarray) -> float:
    return float(seg.mean() / seg.std() * np.sqrt(365)) if seg.std() else 0.0


def run_train_test(p: dict) -> dict:
    d = portfolio_daily(p)
    b = portfolio_daily({})
    h = len(d) // 2
    return {"trainS": round(sharpe(d[:h]), 2), "testS": round(sharpe(d[h:]), 2),
            "train$": round(float(d[:h].sum())), "test$": round(float(d[h:].sum())),
            "base_trainS": round(sharpe(b[:h]), 2),
            "base_testS": round(sharpe(b[h:]), 2)}


def run_retest(p: dict, cutoff: str) -> dict:
    ts = datetime.fromisoformat(cutoff).timestamp()
    d = portfolio_daily(p, after_ts=ts)
    b = portfolio_daily({}, after_ts=ts)
    return {"fold_days": len(d), "S": round(sharpe(d), 2),
            "$": round(float(d.sum())), "base_S": round(sharpe(b), 2),
            "base_$": round(float(b.sum()))}


def seed(conn) -> None:
    def put(name, family, params, status, not_before=None, cutoff=None,
            notes=""):
        conn.execute(
            "INSERT OR IGNORE INTO hypotheses "
            "(name, family, params, status, not_before, cutoff, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, family, json.dumps(params), status, not_before, cutoff,
             notes))

    for name, why in BLACKLIST:
        put(name, "note", {}, "rejected", notes=why)
    put("tp20_pullback_sma40", "retest_cutoff",
        {"tp": 0.20, "pullback": "sma40"}, "waiting_data",
        not_before="2026-10-15", cutoff="2026-07-17",
        notes="Ganó TEST pero perdió TRAIN el 2026-07-17. Se re-testea sólo "
              "con datos posteriores al congelamiento.")
    put("tp20_pullback_sma10", "retest_cutoff",
        {"tp": 0.20, "pullback": "sma10"}, "waiting_data",
        not_before="2026-10-15", cutoff="2026-07-17",
        notes="Ídem, variante SMA10.")
    put("oi_confirmacion_tendencia", "oi_confirm", {}, "waiting_data",
        notes="Gate: >=60 días de OI propio recolectado (Binance sólo "
              "publica 30). Hipótesis: tendencia con OI creciente es más "
              "confiable para A5.1.")


def oi_gate_ok(conn) -> bool:
    row = conn.execute(
        "SELECT MIN(ts) a, MAX(ts) b FROM futures_oi").fetchone()
    if not row["a"]:
        return False
    span = (datetime.fromisoformat(row["b"]) -
            datetime.fromisoformat(row["a"])).days
    return span >= 60


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    today = date.today().isoformat()

    with connect() as conn:
        conn.executescript(_SCHEMA)
        seed(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM hypotheses ORDER BY status, name").fetchall()]

        if args.list:
            for r in rows:
                print(f"[{r['status']:<12}] {r['name']:<28} "
                      f"{(r['not_before'] or ''):<12} {r['notes'][:60]}")
            return

        ran = confirmed = 0
        for r in rows:
            if r["status"] not in ("pending", "waiting_data"):
                continue
            if r["not_before"] and today < r["not_before"]:
                continue
            if r["family"] == "oi_confirm" and not oi_gate_ok(conn):
                continue
            params = json.loads(r["params"])
            print(f"→ testeando {r['name']} ({r['family']})…")
            if r["family"] == "retest_cutoff":
                res = run_retest(params, r["cutoff"])
                ok = res["fold_days"] >= 60 and res["S"] > res["base_S"]
                verdict = "confirmed" if ok else (
                    "waiting_data" if res["fold_days"] < 60 else "rejected")
            elif r["family"] in ("sl_tp", "tp_pullback"):
                res = run_train_test(params)
                ok = (res["trainS"] > res["base_trainS"]
                      and res["testS"] > res["base_testS"])
                verdict = "confirmed" if ok else "rejected"
            else:
                continue
            ran += 1
            conn.execute(
                "UPDATE hypotheses SET status=?, result=?, tested_at=? "
                "WHERE id=?",
                (verdict, json.dumps(res), today, r["id"]))
            print(f"  {res} → {verdict.upper()}")
            if verdict == "confirmed":
                confirmed += 1
                print(f"  ★★★ {r['name']} VALIDADA — esperando OK del "
                      "usuario para activar. NO se activa sola. ★★★")

        pend = conn.execute(
            "SELECT COUNT(*) c FROM hypotheses WHERE status='confirmed'"
        ).fetchone()["c"]
        print(f"\nlab: {ran} hipótesis corridas hoy | {confirmed} nuevas "
              f"confirmadas | {pend} esperando OK del usuario")
        if not ran:
            nxt = conn.execute(
                "SELECT name, not_before FROM hypotheses "
                "WHERE status='waiting_data' ORDER BY not_before"
            ).fetchall()
            for n in nxt:
                print(f"  en espera: {n['name']} "
                      f"(desde {n['not_before'] or 'gate de datos'})")


if __name__ == "__main__":
    main()
