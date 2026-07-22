"""Paper-trade evaluator: replay each LONG/SHORT signal against what the
market actually did afterwards, using 1-minute candles.

Rules (scalping-oriented, configurable in config.yaml under `evaluation`):
- The entry is a touch trigger: filled when a later candle's range includes
  the entry price. If not touched within `entry_window_min`, the signal is
  `not_triggered`.
- After entry, the first level touched decides: TP -> win, SL -> loss.
- If one candle spans BOTH levels, the outcome is unknowable at 1m resolution:
  counted conservatively as SL and flagged `ambiguous`.
- If still open after `max_duration_min`, it closes at the last candle close
  (`expired`), with mark-to-market P&L. Scalps never run indefinitely.

Usage:
    python evaluator.py           # register new signals + evaluate pending
    python evaluator.py --verbose
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import datetime

import requests

import output
from config import load_config
from storage import pending_signals, register_signals, update_signal

# A candle is (epoch_seconds, open, high, low, close).
Candle = tuple[int, float, float, float, float]

ENTRY_WINDOW_MIN = 240   # 4h to trigger the entry
MAX_DURATION_MIN = 480   # 8h max in-trade (scalps don't run overnight)


@dataclass
class SimResult:
    status: str                 # pending | not_triggered | tp | sl | expired | ambiguous
    entry_ts: int | None = None
    exit_ts: int | None = None
    exit_price: float | None = None
    note: str | None = None


def simulate_signal(
    direction: str, entry: float, sl: float, tp: float,
    candles: list[Candle], signal_ts: int, now_ts: int,
    entry_window_s: int = ENTRY_WINDOW_MIN * 60,
    max_duration_s: int = MAX_DURATION_MIN * 60,
) -> SimResult:
    """Replay one signal over 1m candles. Pure function — unit-testable."""
    is_long = direction == "LONG"
    entry_deadline = signal_ts + entry_window_s
    entered_at: int | None = None

    for ts, _o, high, low, _c in candles:
        if ts < signal_ts:
            continue

        if entered_at is None:
            if ts > entry_deadline:
                return SimResult("not_triggered")
            if low <= entry <= high:
                entered_at = ts
                # fall through: this same candle can also resolve the trade
            else:
                continue

        trade_deadline = entered_at + max_duration_s
        if ts > trade_deadline:
            break  # handled below as expired

        hit_sl = (low <= sl) if is_long else (high >= sl)
        hit_tp = (high >= tp) if is_long else (low <= tp)
        if hit_sl and hit_tp:
            return SimResult(
                "ambiguous", entered_at, ts, sl,
                note="SL y TP en la misma vela 1m; contado como pérdida",
            )
        if hit_sl:
            return SimResult("sl", entered_at, ts, sl)
        if hit_tp:
            return SimResult("tp", entered_at, ts, tp)

    # Ran out of candles without resolution.
    if entered_at is None:
        return (
            SimResult("not_triggered")
            if now_ts > entry_deadline
            else SimResult("pending")
        )
    if now_ts > entered_at + max_duration_s:
        in_window = [c for c in candles if c[0] <= entered_at + max_duration_s]
        last_close = in_window[-1][4] if in_window else entry
        return SimResult(
            "expired", entered_at, entered_at + max_duration_s, last_close,
            note="cerrado a mercado al vencer la duración máxima",
        )
    return SimResult("pending", entered_at)


def pnl(direction: str, entry: float, sl: float, exit_price: float) -> tuple[float, float]:
    """Signed (pnl_pct, r_multiple) for a closed trade, gross of fees."""
    sign = 1 if direction == "LONG" else -1
    move = (exit_price - entry) * sign
    risk = abs(entry - sl)
    return round(move / entry * 100, 3), round(move / risk, 2) if risk else 0.0


def round_trip_fees(inst_type: str, notional_usd: float, ev,
                    fee_side_pct: float | None = None) -> float:
    """Total commissions for entry + exit, on the NOTIONAL value.

    crypto: percentage per side — spot taker by default, or the signal's
    own snapshot (e.g. futures taker for leveraged strategies).
    stock: IBKR Pro fixed — percentage per side with a minimum per order
    (the minimum dominates for small positions).
    """
    if inst_type == "crypto":
        side_pct = fee_side_pct if fee_side_pct is not None else ev.crypto_fee_pct
        per_side = notional_usd * side_pct / 100
    else:
        per_side = max(notional_usd * ev.stock_fee_pct / 100, ev.stock_fee_min_usd)
    return round(2 * per_side, 4)


# --- Candle fetching -------------------------------------------------------

def _binance_klines(symbol: str, interval: str, start_ts: int,
                    end_ts: int) -> list[Candle]:
    step_ms = {"1m": 60_000, "30m": 1_800_000}[interval]
    out: list[Candle] = []
    cursor = start_ts * 1000
    while cursor < end_ts * 1000:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": interval,
                    "startTime": cursor, "limit": 1000},
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        out.extend(
            (r[0] // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]))
            for r in rows
        )
        cursor = rows[-1][0] + step_ms
        if len(rows) < 1000:
            break
    return out


def _ibkr_bars(symbol: str, ibkr_cfg, bar_size: str, duration: str) -> list[Candle]:
    """Recent bars from IBKR (sliced by caller)."""
    from fetchers.ibkr import _get_ib
    from ib_insync import Stock

    ib = _get_ib(ibkr_cfg)
    contract = ib.qualifyContracts(Stock(symbol, "SMART", "USD"))[0]
    bars = ib.reqHistoricalData(
        contract, endDateTime="", durationStr=duration, barSizeSetting=bar_size,
        whatToShow="TRADES", useRTH=True, formatDate=2,
    )
    return [
        (int(b.date.timestamp()), float(b.open), float(b.high),
         float(b.low), float(b.close))
        for b in bars
    ]


def _to_epoch(iso_local: str) -> int:
    """Naive local ISO timestamp -> epoch seconds."""
    return int(datetime.fromisoformat(iso_local).astimezone().timestamp())


def evaluate_all(verbose: bool = False) -> None:
    cfg = load_config("config.yaml")
    # Client ID propio: el evaluador convive con las corridas de main.py
    # (client_id base) y dos conexiones con el mismo ID chocan en Gateway.
    cfg.ibkr.client_id = cfg.ibkr.client_id + 1
    strategy_params = {
        name: {
            "leverage": sd.leverage,
            "entry_window_min": sd.entry_window_min,
            "max_duration_min": sd.max_duration_min,
            "fee_side_pct": sd.fee_pct_per_side,
        }
        for name, sd in cfg.strategies.items()
    }
    new = register_signals(
        strategy_params=strategy_params,
        position_usd=cfg.evaluation.position_usd,
    )
    if new:
        output.info(f"{new} señal(es) nueva(s) registrada(s)")

    pend = pending_signals()
    if not pend:
        output.info("Sin señales pendientes.")
        return

    now_ts = int(time.time())
    ev = cfg.evaluation
    for sig in pend:
        signal_ts = _to_epoch(sig["signal_at"])
        entry_window_s = (sig["entry_window_min"] or ev.entry_window_min) * 60
        max_duration_s = (sig["max_duration_min"] or ev.max_duration_min) * 60
        horizon = signal_ts + entry_window_s + max_duration_s

        # Resolution: 1m for intraday scalps; 30m for multi-day swings
        # (1m data over weeks is impractical and unnecessary — wider
        # SL/TP distances tolerate coarser candles).
        swing = (entry_window_s + max_duration_s) > 48 * 3600
        try:
            if sig["type"] == "crypto":
                candles = _binance_klines(
                    sig["symbol"], "30m" if swing else "1m",
                    signal_ts, min(now_ts, horizon),
                )
            else:
                bar_size, duration = ("30 mins", "1 M") if swing else ("1 min", "2 D")
                candles = [c for c in _ibkr_bars(sig["symbol"], cfg.ibkr, bar_size, duration)
                           if c[0] >= signal_ts]
        except Exception as exc:  # noqa: BLE001 - keep batch alive; retry next run
            output.warn(f"{sig['symbol']}: no pude bajar velas ({exc}); reintento luego")
            continue

        res = simulate_signal(
            sig["direction"], sig["entry"], sig["sl"], sig["tp"],
            candles, signal_ts, now_ts,
            entry_window_s=entry_window_s, max_duration_s=max_duration_s,
        )
        fields: dict = {"status": res.status, "note": res.note}
        if res.entry_ts:
            fields["entry_at"] = datetime.fromtimestamp(res.entry_ts).isoformat(timespec="seconds")

        if res.status == "pending":
            # Live tracking: last known price + unrealized P&L if in position.
            fields["checked_at"] = datetime.now().isoformat(timespec="seconds")
            if candles:
                last_close = candles[-1][4]
                fields["last_price"] = last_close
                if res.entry_ts:
                    sign = 1 if sig["direction"] == "LONG" else -1
                    upct = (last_close - sig["entry"]) / sig["entry"] * 100 * sign
                    notional = (sig["position_usd"] or ev.position_usd) * (sig["leverage"] or 1.0)
                    fields["unrealized_pct"] = round(upct, 3)
                    fields["unrealized_usd"] = round(notional * upct / 100, 4)
            update_signal(sig["id"], fields)
            if verbose:
                output.info(f"{sig['symbol']} {sig['direction']} → pending")
            continue

        # Closed: clear live-tracking fields.
        fields.update({"unrealized_pct": None, "unrealized_usd": None})
        if res.exit_price is not None:
            fields["exit_at"] = datetime.fromtimestamp(res.exit_ts).isoformat(timespec="seconds")
            fields["exit_price"] = res.exit_price
            fields["pnl_pct"], fields["r_multiple"] = pnl(
                sig["direction"], sig["entry"], sig["sl"], res.exit_price
            )
            position = sig["position_usd"] or ev.position_usd
            leverage = sig["leverage"] or 1.0
            notional = position * leverage
            fields["position_usd"] = position
            fields["fees_usd"] = round_trip_fees(
                sig["type"], notional, ev, fee_side_pct=sig["fee_side_pct"]
            )
            gross = notional * fields["pnl_pct"] / 100
            fields["pnl_usd"] = round(gross - fields["fees_usd"], 4)
        output.info(f"{sig['symbol']} {sig['direction']} → {res.status}")
        update_signal(sig["id"], fields)

    try:
        from fetchers.ibkr import disconnect
        disconnect()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evalúa señales de paper trading.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    evaluate_all(verbose=args.verbose)
