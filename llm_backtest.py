"""Sampled historical replay of an LLM strategy (A3) with a hard budget cap.

At each decision point (every N hours over the last D days) it rebuilds the
exact payload the live system would have sent at that moment, asks the model,
and simulates the resulting signal against subsequent 1-candle data with the
same rules as the paper-trading evaluator.

Usage:
    python llm_backtest.py --symbols BNBUSDT,ETHUSDT --days 30 --step-hours 4 \
        --budget-usd 8
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from analyzer import build_user_message, load_system_prompt, parse_verdict
from backtest import fetch_5m_history
from config import load_config
from evaluator import simulate_signal
from indicators import build_payload

# claude-sonnet-5 intro pricing (USD per Mtok)
PRICE_IN, PRICE_OUT = 2.0, 10.0
FUTURES_FEE_RT_PCT = 0.10


def resample_ohlcv(df5: pd.DataFrame, minutes: int) -> pd.DataFrame:
    o = df5.set_index("time").resample(f"{minutes}min").agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}
    ).dropna()
    return o.reset_index()


def frames_asof(df5: pd.DataFrame, t: pd.Timestamp, n: int = 50) -> dict:
    """Candle frames per TF using only data up to t (no lookahead)."""
    past = df5[df5["time"] < t]
    return {
        "5m": past.tail(n).reset_index(drop=True),
        "15m": resample_ohlcv(past, 15).tail(n).reset_index(drop=True),
        "30m": resample_ohlcv(past, 30).tail(n).reset_index(drop=True),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BNBUSDT")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--step-hours", type=int, default=4)
    ap.add_argument("--budget-usd", type=float, default=8.0)
    ap.add_argument("--strategy", default="A3")
    args = ap.parse_args()

    load_dotenv(".env")
    from anthropic import Anthropic

    cfg = load_config("config.yaml")
    cfg.activate_strategy(args.strategy)
    sdef = cfg.active_strategy_def()
    system = load_system_prompt(cfg.anthropic.prompt_path)
    client = Anthropic()
    out_path = Path(f"logs/llm_backtest_{args.strategy}.jsonl")
    out_path.parent.mkdir(exist_ok=True)

    spent = 0.0
    stats = {"calls": 0, "signals": 0, "tp": 0, "sl": 0, "other": 0,
             "net_r": 0.0, "net_usd": 0.0}
    symbols = [s.strip().upper() for s in args.symbols.split(",")]

    for symbol in symbols:
        print(f"[{symbol}] bajando {args.days + 3} días de 5m…", flush=True)
        df5 = fetch_5m_history(symbol, args.days + 3)
        t0 = df5["time"].iloc[0] + pd.Timedelta(hours=30)   # indicator warmup
        t_end = df5["time"].iloc[-1] - pd.Timedelta(hours=13)  # room to resolve
        points = pd.date_range(t0, t_end, freq=f"{args.step_hours}h")

        for t in points:
            if spent >= args.budget_usd:
                print(f"presupuesto agotado (${spent:.2f})", flush=True)
                break
            candles = frames_asof(df5, t)
            if len(candles["30m"]) < 40:
                continue
            payload = build_payload(
                symbol, "crypto", candles, cfg.indicators, cfg.price_decimals,
                round_trip_cost_pct=2 * (sdef.fee_pct_per_side or 0.05),
                timing_candles=cfg.payload.timing_candles,
                context_candles=cfg.payload.context_candles,
            )
            try:
                resp = client.messages.create(
                    model=cfg.anthropic.model,
                    max_tokens=cfg.anthropic.max_tokens,
                    system=system,
                    output_config={"effort": cfg.anthropic.effort}
                    if cfg.anthropic.effort else None,
                    messages=[{"role": "user",
                               "content": build_user_message(payload)}],
                )
            except TypeError:
                resp = client.messages.create(
                    model=cfg.anthropic.model, max_tokens=cfg.anthropic.max_tokens,
                    system=system,
                    messages=[{"role": "user",
                               "content": build_user_message(payload)}],
                )
            spent += (resp.usage.input_tokens * PRICE_IN
                      + resp.usage.output_tokens * PRICE_OUT) / 1e6
            stats["calls"] += 1
            text = "".join(b.text for b in resp.content if b.type == "text")
            v = parse_verdict(symbol, text)

            record = {"symbol": symbol, "t": t.isoformat(),
                      "verdict": v.verdict, "confidence": v.confidence,
                      "spent": round(spent, 3)}

            if v.parsed and v.verdict in ("LONG", "SHORT"):
                try:
                    entry = float(v.entry.replace(",", ""))
                    sl = float(v.stop_loss.replace(",", ""))
                    tp = float(v.take_profit.replace(",", ""))
                except ValueError:
                    entry = sl = tp = None
                if entry and sl and tp:
                    fut = df5[df5["time"] >= t]
                    fwd = [(int(r.time.timestamp()), r.open, r.high, r.low, r.close)
                           for r in fut.itertuples(index=False)]
                    res = simulate_signal(
                        v.verdict, entry, sl, tp, fwd,
                        int(t.timestamp()), int(fut["time"].iloc[-1].timestamp()),
                    )
                    record["status"] = res.status
                    stats["signals"] += 1
                    if res.exit_price is not None:
                        sign = 1 if v.verdict == "LONG" else -1
                        move_pct = (res.exit_price - entry) / entry * 100 * sign
                        risk_pct = abs(entry - sl) / entry * 100
                        gross_r = move_pct / risk_pct if risk_pct else 0
                        fee_r = FUTURES_FEE_RT_PCT / risk_pct if risk_pct else 0
                        net_r = gross_r - fee_r
                        net_usd = 1000 * (move_pct - FUTURES_FEE_RT_PCT) / 100
                        record.update(net_r=round(net_r, 2),
                                      net_usd=round(net_usd, 2))
                        stats["net_r"] += net_r
                        stats["net_usd"] += net_usd
                        key = res.status if res.status in ("tp", "sl") else "other"
                        stats[key] = stats.get(key, 0) + 1
                    else:
                        stats["other"] += 1

            with out_path.open("a") as f:
                f.write(json.dumps(record) + "\n")
            if stats["calls"] % 10 == 0:
                print(f"[{symbol}] {stats['calls']} calls, ${spent:.2f}, "
                      f"{stats['signals']} señales, netR {stats['net_r']:+.1f}",
                      flush=True)
        if spent >= args.budget_usd:
            break

    closed = stats["tp"] + stats["sl"] + stats["other"]
    print("\n=== RESUMEN LLM BACKTEST", args.strategy, "===")
    print(f"decisiones: {stats['calls']}  señales: {stats['signals']} "
          f"(tp {stats['tp']} / sl {stats['sl']} / otras {stats['other']})")
    if closed:
        print(f"netR total: {stats['net_r']:+.1f}  "
              f"net USD (margen $100 x10): {stats['net_usd']:+.2f}")
    print(f"gasto API: ${spent:.2f}")
    print(f"detalle: {out_path}")


if __name__ == "__main__":
    main()
