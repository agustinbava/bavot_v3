"""CLI entry point for the intraday scalping analyzer.

Flow: watchlist -> fetch OHLCV (5m/15m/30m) -> indicators -> compact JSON
payload -> Anthropic analysis -> summary table. Each run is archived under
runs/ for later auditing.

Examples:
    python main.py --watchlist config.yaml
    python main.py --only BTCUSDT,ETHUSDT --dry-run
    python main.py --only MELI --verbose
    python main.py --parallel 4
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

import output
from analyzer import (
    Verdict,
    analyze_batch_async,
    analyze_sync,
    load_system_prompt,
)
from config import Config, Instrument, load_config
from fetchers import FetchError, fetch_instrument
from indicators import build_payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Intraday scalping technical analysis.")
    p.add_argument("--watchlist", default="config.yaml", help="Path to config YAML.")
    p.add_argument("--only", help="Comma-separated symbols to include (filter).")
    p.add_argument(
        "--type",
        choices=["crypto", "stock"],
        dest="inst_type",
        help="Analyze only this instrument type.",
    )
    p.add_argument(
        "--market-hours-only",
        action="store_true",
        help="Skip stock instruments when the US market is closed (crypto unaffected).",
    )
    p.add_argument(
        "--strategy",
        help="Strategy name from the config catalog (overrides the default).",
    )
    p.add_argument(
        "--session-window",
        metavar="HH:MM-HH:MM",
        help="Skip stocks outside this US-session window (New York time), "
        "e.g. 09:30-11:30. Implies --market-hours-only behavior for stocks.",
    )
    p.add_argument(
        "--timeframes",
        help="Comma-separated timeframes, e.g. 5m,15m,30m (overrides config).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Build payloads without calling the API; print one example.",
    )
    p.add_argument(
        "--parallel",
        type=int,
        default=1,
        metavar="N",
        help="Analyze up to N instruments concurrently (default 1 = sequential).",
    )
    p.add_argument("--verbose", action="store_true", help="Print full analyses.")
    return p.parse_args(argv)


def us_market_open(
    now: datetime | None = None, window: str | None = None
) -> bool:
    """True if within US regular trading hours (Mon-Fri 9:30-16:00 ET).

    window: optional "HH:MM-HH:MM" (New York time) to restrict further,
    e.g. "09:30-11:30" for the first two hours of the session.
    Does not account for exchange holidays.
    """
    from zoneinfo import ZoneInfo

    now = now or datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:  # Sat/Sun
        return False
    minutes = now.hour * 60 + now.minute
    lo, hi = 9 * 60 + 30, 16 * 60
    if window:
        try:
            start, end = window.split("-")
            h1, m1 = (int(x) for x in start.split(":"))
            h2, m2 = (int(x) for x in end.split(":"))
        except ValueError as exc:
            raise ValueError(
                f"--session-window inválida: '{window}' (formato HH:MM-HH:MM)"
            ) from exc
        lo, hi = max(lo, h1 * 60 + m1), min(hi, h2 * 60 + m2)
    return lo <= minutes <= hi


def select_instruments(
    cfg: Config,
    only: str | None,
    inst_type: str | None = None,
    market_hours_only: bool = False,
    session_window: str | None = None,
) -> list[Instrument]:
    selected = cfg.watchlist
    if inst_type:
        selected = [i for i in selected if i.type == inst_type]
    if only:
        wanted = {s.strip().upper() for s in only.split(",") if s.strip()}
        selected = [i for i in selected if i.symbol.upper() in wanted]
        missing = wanted - {i.symbol.upper() for i in selected}
        if missing:
            output.warn(
                f"--only symbols not in watchlist, ignored: {', '.join(sorted(missing))}"
            )
    if (market_hours_only or session_window) and not us_market_open(
        window=session_window
    ):
        dropped = [i.symbol for i in selected if i.type == "stock"]
        selected = [i for i in selected if i.type != "stock"]
        if dropped:
            label = f"fuera de la ventana {session_window}" if session_window \
                else "US market closed"
            output.info(f"{label} — skipping stocks: {', '.join(dropped)}")
    return selected


def gather_payloads(
    cfg: Config, instruments: list[Instrument], timeframes: list[str]
) -> tuple[list[tuple[str, dict]], list[Verdict]]:
    """Fetch + build payloads. Returns (payloads, fetch_errors).

    A failing ticker is logged and turned into an error Verdict; the batch
    continues.
    """
    payloads: list[tuple[str, dict]] = []
    errors: list[Verdict] = []
    ev = cfg.evaluation
    sdef = cfg.active_strategy_def()
    crypto_side = (
        sdef.fee_pct_per_side
        if sdef and sdef.fee_pct_per_side is not None
        else ev.crypto_fee_pct
    )
    leverage = sdef.leverage if sdef else 1.0
    notional = ev.position_usd * leverage
    cost_pct = {
        "crypto": 2 * crypto_side,
        "stock": 2 * max(
            ev.stock_fee_pct,
            ev.stock_fee_min_usd / notional * 100 if notional else 0,
        ),
    }
    for inst in instruments:
        try:
            output.info(f"Fetching {inst.symbol} ({inst.type})…")
            candles = fetch_instrument(inst, timeframes, cfg.num_candles, cfg.ibkr)
            payload = build_payload(
                inst.symbol, inst.type, candles, cfg.indicators, cfg.price_decimals,
                round_trip_cost_pct=cost_pct.get(inst.type),
                timing_candles=cfg.payload.timing_candles,
                context_candles=cfg.payload.context_candles,
            )
            payloads.append((inst.symbol, payload))
        except (FetchError, Exception) as exc:  # noqa: BLE001 - keep batch alive
            output.error(f"{inst.symbol}: {exc}")
            errors.append(Verdict(symbol=inst.symbol, error=str(exc)))
    return payloads, errors


def make_run_dir() -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    run_dir = Path("runs") / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def save_run(run_dir: Path, payloads: list[tuple[str, dict]],
             verdicts: list[Verdict]) -> None:
    """Persist sent payload + full response per ticker for auditing."""
    by_symbol = {s: p for s, p in payloads}
    for v in verdicts:
        base = run_dir / v.symbol.replace("/", "_")
        if v.symbol in by_symbol:
            base.with_suffix(".payload.json").write_text(
                json.dumps(by_symbol[v.symbol], indent=2)
            )
        if v.error:
            base.with_suffix(".error.txt").write_text(v.error)
        elif v.raw:
            base.with_suffix(".response.txt").write_text(v.raw)


def run_analysis(cfg: Config, payloads: list[tuple[str, dict]],
                 system_prompt: str, parallel: int) -> list[Verdict]:
    """Dispatch to sequential or async-parallel analysis."""
    a = cfg.anthropic
    if parallel > 1:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY
        output.info(f"Analyzing {len(payloads)} instruments (parallel={parallel})…")
        return asyncio.run(
            analyze_batch_async(
                client, a.model, a.max_tokens, system_prompt, payloads, parallel,
                effort=a.effort,
            )
        )

    from anthropic import Anthropic

    client = Anthropic()
    results: list[Verdict] = []
    for symbol, payload in payloads:
        output.info(f"Analyzing {symbol}…")
        results.append(
            analyze_sync(client, a.model, a.max_tokens, system_prompt,
                         symbol, payload, effort=a.effort)
        )
    return results


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)

    cfg = load_config(args.watchlist)
    if args.strategy:
        cfg.activate_strategy(args.strategy)
        output.info(f"Estrategia activa: {cfg.strategy.name}")
    timeframes = (
        [t.strip() for t in args.timeframes.split(",")] if args.timeframes
        else cfg.timeframes
    )
    instruments = select_instruments(
        cfg, args.only, args.inst_type, args.market_hours_only,
        args.session_window,
    )
    if not instruments:
        if args.market_hours_only or args.session_window:
            output.info("Nothing to analyze (market closed). Exiting.")
            return 0
        output.error("No instruments selected.")
        return 1

    payloads, fetch_errors = gather_payloads(cfg, instruments, timeframes)

    # Close IB connection if it was opened during fetching.
    try:
        from fetchers.ibkr import disconnect

        disconnect()
    except Exception:  # noqa: BLE001
        pass

    if args.dry_run:
        output.info("Dry run — payloads built, no API calls.")
        if payloads:
            sym, example = payloads[0]
            output.info(f"Example payload for {sym}:")
            print(json.dumps(example, indent=2))
        output.info(f"{len(payloads)} payload(s) ready, {len(fetch_errors)} fetch error(s).")
        return 0

    if not payloads:
        output.error("No payloads to analyze (all fetches failed).")
        output.render_summary(fetch_errors)
        return 1

    if not os.getenv("ANTHROPIC_API_KEY"):
        output.error("ANTHROPIC_API_KEY not set (see .env.example). Use --dry-run to test fetching.")
        return 1

    system_prompt = load_system_prompt(cfg.anthropic.prompt_path)
    verdicts = run_analysis(cfg, payloads, system_prompt, args.parallel)

    all_results = verdicts + fetch_errors
    run_dir = make_run_dir()
    save_run(run_dir, payloads, all_results)
    output.info(f"Run saved to {run_dir}")

    # Persist to SQLite for the dashboard (see dashboard.py).
    from storage import save_run as save_run_db, upsert_strategy

    upsert_strategy(cfg.strategy.name, cfg.strategy.description)
    inst_types = {i.symbol: i.type for i in instruments}
    run_id = save_run_db(all_results, inst_types, strategy=cfg.strategy.name)
    output.info(f"Run #{run_id} stored in bavot.db [{cfg.strategy.name}]")

    # Registrar señales al instante (la evaluación contra el mercado sigue
    # siendo tarea del evaluator cada 15 min; esto solo crea las filas).
    from storage import register_signals

    strategy_params = {
        name: {
            "leverage": sd.leverage,
            "entry_window_min": sd.entry_window_min,
            "max_duration_min": sd.max_duration_min,
            "fee_side_pct": sd.fee_pct_per_side,
        }
        for name, sd in cfg.strategies.items()
    }
    new_signals = register_signals(
        strategy_params=strategy_params,
        position_usd=cfg.evaluation.position_usd,
    )
    if new_signals:
        output.info(f"{new_signals} señal(es) registrada(s)")

    output.render_summary(all_results)
    if args.verbose:
        output.render_verbose(verdicts)

    return 0


if __name__ == "__main__":
    sys.exit(main())
