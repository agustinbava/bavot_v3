"""Backtest de A4 (stocks swing, LLM) en la ventana más volátil de los
últimos 2 años, vía Batch API (50% de descuento) con tope de gasto.

Flujo: detectar ventana de máxima volatilidad (datos Yahoo, gratis) →
construir payloads históricos as-of (sin lookahead) → Batch API → parsear
veredictos → simular con las reglas del evaluador (entrada 3d, hold 21d,
fees IBKR $2 RT sobre $100).

Uso: python a4_vol_backtest.py [--budget-usd 1.5]
"""
from __future__ import annotations

import argparse
import json
import time

import pandas as pd
import requests
from dotenv import load_dotenv

from analyzer import build_user_message, load_system_prompt, parse_verdict
from config import load_config
from evaluator import simulate_signal
from indicators import build_payload

# Batch pricing sonnet-5 (intro, 50% off): $1 in / $5 out por Mtok
PRICE_IN, PRICE_OUT = 1.0, 5.0
SYMBOLS_N = 5          # tickers más volátiles de la ventana
POINTS_PER_SYM = 12    # decisiones por ticker
WINDOW_DAYS = 45
FEE_RT_USD = 2.0       # IBKR $1/orden sobre posición $100
POSITION_USD = 100.0


def yahoo(symbol: str, interval: str, start: int, end: int) -> pd.DataFrame:
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"interval": interval, "period1": start, "period2": end},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=30,
    )
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({
        "time": pd.to_datetime(res["timestamp"], unit="s", utc=True),
        "open": q["open"], "high": q["high"],
        "low": q["low"], "close": q["close"], "volume": q["volume"],
    }).dropna()
    return df.reset_index(drop=True)


def find_vol_window(symbols: list[str]) -> tuple[pd.Timestamp, pd.Timestamp, list[str]]:
    """Ventana de 45 días con mayor vol realizada agregada; top símbolos."""
    now = int(time.time())
    frames = {}
    for s in symbols:
        try:
            frames[s] = yahoo(s, "1d", now - 730 * 86400, now)
        except Exception:
            pass
    rets = pd.DataFrame({
        s: df.set_index("time")["close"].pct_change() for s, df in frames.items()
    }).dropna(how="all")
    agg_vol = rets.abs().mean(axis=1).rolling(WINDOW_DAYS).mean()
    end = agg_vol.idxmax()
    start = end - pd.Timedelta(days=WINDOW_DAYS)
    win_vol = rets.loc[start:end].std().sort_values(ascending=False)
    top = list(win_vol.index[:SYMBOLS_N])
    return start, end, top


def frames_asof(h1: pd.DataFrame, d1: pd.DataFrame, t: pd.Timestamp) -> dict:
    past_h = h1[h1["time"] < t]
    past_d = d1[d1["time"] < t.normalize()]
    h4 = past_h.set_index("time").resample("4h").agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}).dropna().reset_index()
    return {"1h": past_h.tail(50).reset_index(drop=True),
            "4h": h4.tail(50).reset_index(drop=True),
            "1d": past_d.tail(50).reset_index(drop=True)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-usd", type=float, default=1.5)
    args = ap.parse_args()
    load_dotenv(".env")
    from anthropic import Anthropic

    cfg = load_config("config.yaml")
    cfg.activate_strategy("A4")
    system = load_system_prompt(cfg.anthropic.prompt_path)
    client = Anthropic()

    stock_syms = [i.symbol for i in cfg.watchlist if i.type == "stock"]
    w_start, w_end, top = find_vol_window(stock_syms)
    print(f"ventana más volátil: {w_start:%Y-%m-%d} → {w_end:%Y-%m-%d}")
    print(f"tickers: {', '.join(top)}")

    now = int(time.time())
    t0 = int((w_start - pd.Timedelta(days=90)).timestamp())
    requests_list, meta = [], {}
    for sym in top:
        h1 = yahoo(sym, "60m", max(t0, now - 729 * 86400), now)
        d1 = yahoo(sym, "1d", now - 730 * 86400, now)
        win_bars = h1[(h1["time"] >= w_start) & (h1["time"] <= w_end)]
        if len(win_bars) < POINTS_PER_SYM:
            continue
        step = max(1, len(win_bars) // POINTS_PER_SYM)
        points = win_bars.iloc[::step]["time"].tolist()[:POINTS_PER_SYM]
        for t in points:
            frames = frames_asof(h1, d1, t)
            if len(frames["1d"]) < 45 or len(frames["1h"]) < 45:
                continue
            payload = build_payload(
                sym, "stock", frames, cfg.indicators, cfg.price_decimals,
                round_trip_cost_pct=2.0,
                timing_candles=cfg.payload.timing_candles,
                context_candles=cfg.payload.context_candles,
            )
            cid = f"{sym}-{int(t.timestamp())}"
            meta[cid] = {"symbol": sym, "t": t, "h1": h1}
            requests_list.append({
                "custom_id": cid,
                "params": {
                    "model": cfg.anthropic.model,
                    "max_tokens": cfg.anthropic.max_tokens,
                    "system": system,
                    "output_config": {"effort": cfg.anthropic.effort},
                    "messages": [{"role": "user",
                                  "content": build_user_message(payload)}],
                },
            })

    est = len(requests_list) * (6000 * PRICE_IN + 1100 * PRICE_OUT) / 1e6
    print(f"{len(requests_list)} decisiones | costo estimado (batch): ${est:.2f}")
    if est > args.budget_usd:
        print(f"ABORTADO: estimado supera el tope de ${args.budget_usd}")
        return

    batch = client.messages.batches.create(requests=requests_list)
    print(f"batch {batch.id} enviado; esperando…", flush=True)
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        time.sleep(30)

    stats = {"signals": 0, "tp": 0, "sl": 0, "other": 0,
             "net_r": 0.0, "net_usd": 0.0, "no_trade": 0}
    in_toks = out_toks = 0
    details = []
    for result in client.messages.batches.results(batch.id):
        if result.result.type != "succeeded":
            continue
        msg = result.result.message
        in_toks += msg.usage.input_tokens
        out_toks += msg.usage.output_tokens
        m = meta[result.custom_id]
        text = next((blk.text for blk in msg.content if blk.type == "text"), "")
        v = parse_verdict(m["symbol"], text)
        if not (v.parsed and v.verdict in ("LONG", "SHORT")):
            stats["no_trade"] += 1
            continue
        try:
            entry = float(v.entry.replace(",", ""))
            sl = float(v.stop_loss.replace(",", ""))
            tp = float(v.take_profit.replace(",", ""))
        except (ValueError, AttributeError):
            continue
        stats["signals"] += 1
        fut = m["h1"][m["h1"]["time"] >= m["t"]]
        fwd = [(int(r.time.timestamp()), r.open, r.high, r.low, r.close)
               for r in fut.itertuples(index=False)]
        res = simulate_signal(
            v.verdict, entry, sl, tp, fwd,
            int(m["t"].timestamp()), int(fut["time"].iloc[-1].timestamp()),
            entry_window_s=4320 * 60, max_duration_s=30240 * 60,
        )
        if res.exit_price is not None:
            sign = 1 if v.verdict == "LONG" else -1
            move = (res.exit_price - entry) / entry * 100 * sign
            risk = abs(entry - sl) / entry * 100
            net_r = move / risk if risk else 0
            net_usd = POSITION_USD * move / 100 - FEE_RT_USD
            stats["net_r"] += net_r
            stats["net_usd"] += net_usd
            key = res.status if res.status in ("tp", "sl") else "other"
            stats[key] += 1
            details.append(f"  {m['symbol']} {v.verdict} {m['t']:%m-%d} "
                           f"→ {res.status} ({net_usd:+.2f} USD)")
        else:
            stats["other"] += 1

    cost = (in_toks * PRICE_IN + out_toks * PRICE_OUT) / 1e6
    closed = stats["tp"] + stats["sl"] + stats["other"]
    print("\n=== A4 EN VENTANA VOLÁTIL ===")
    print(f"decisiones: {len(requests_list)}  señales: {stats['signals']} "
          f"({stats['signals']/max(1,len(requests_list)):.0%})  "
          f"no-trade: {stats['no_trade']}")
    print(f"tp {stats['tp']} / sl {stats['sl']} / otras {stats['other']}")
    if closed:
        print(f"netR: {stats['net_r']:+.1f}  net USD (pos $100): "
              f"{stats['net_usd']:+.2f}  fees incl.")
    print("\n".join(details))
    print(f"gasto real (batch): ${cost:.2f}")


if __name__ == "__main__":
    main()
