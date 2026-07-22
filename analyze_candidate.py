"""Deep-dive de una variante candidata: walk-forward multi-fold + desglose.

Uso: python analyze_candidate.py
"""
from __future__ import annotations

from pathlib import Path

from backtest import Params, load_symbol, run_backtest
from config import load_config

DAYS = 180
CANDIDATES = [
    Params(strategy="slowmr", rsi_th=25, tp_pct=2.5, sl_pct=1.2,
           regime="none", max_hold=288),
    Params(strategy="slowmr", rsi_th=25, tp_pct=2.5, sl_pct=0.8,
           regime="none", max_hold=288),
]
# folds: (train_end_frac, test_end_frac) sobre 180 días
FOLDS = [(0.5, 0.667), (0.667, 0.833), (0.833, 1.0)]


def main() -> None:
    cfg = load_config("config.yaml")
    symbols = [i.symbol for i in cfg.watchlist if i.type == "crypto"]
    cache = Path("data/bt_cache")

    frames = {}
    for s in symbols:
        print(f"cargando {s} ({DAYS}d)…", flush=True)
        frames[s] = load_symbol(s, DAYS, cache)

    for p in CANDIDATES:
        print(f"\n##### candidata: rsi<{p.rsi_th:g} tp={p.tp_pct} sl={p.sl_pct} "
              f"mh={p.max_hold//12}h #####")

        print("\n-- walk-forward (3 folds, agregado 10 símbolos) --")
        for k, (a, b) in enumerate(FOLDS, 1):
            tr_r = te_r = 0.0
            tr_n = te_n = pos = have = 0
            for s, df in frames.items():
                n = len(df)
                dtr = df.iloc[: int(n * a)]
                dte = df.iloc[int(n * a): int(n * b)].reset_index(drop=True)
                rtr = run_backtest(dtr, p)
                rte = run_backtest(dte, p)
                if rtr.trades < 10:
                    continue
                have += 1
                tr_r += rtr.net_r; tr_n += rtr.trades
                te_r += rte.net_r; te_n += rte.trades
                pos += 1 if rte.net_r > 0 else 0
            print(f"fold {k}: train {tr_r:+7.1f}R ({tr_n:>4}t) | "
                  f"test {te_r:+7.1f}R ({te_n:>3}t) | sym+ {pos}/{have}")

        print("\n-- por símbolo (180 días completos) --")
        for s, df in frames.items():
            r = run_backtest(df, p)
            print(f"{s:<10} {r.trades:>4}t  wr {r.win_rate:>4.0%}  "
                  f"netR {r.net_r:>+7.1f}  mdd {r.max_drawdown_r:>+6.1f}R")


if __name__ == "__main__":
    main()
