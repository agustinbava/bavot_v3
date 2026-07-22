"""Interactive Brokers OHLCV fetcher via ib_insync.

Requires TWS or IB Gateway running and reachable at the configured host/port.
A single IB connection is reused across tickers within one run.
"""
from __future__ import annotations

import pandas as pd

from .base import Candles, FetchError, validate_candles

# timeframe -> (IB barSizeSetting, durationStr big enough for ~50 RTH bars)
_BAR_MAP: dict[str, tuple[str, str]] = {
    "5m": ("5 mins", "2 D"),
    "15m": ("15 mins", "4 D"),
    "30m": ("30 mins", "6 D"),
    "1h": ("1 hour", "10 D"),
    "4h": ("4 hours", "2 M"),
    "1d": ("1 day", "4 M"),
}

_ib_singleton = None  # reused connection


def _get_ib(ibkr_cfg):
    """Return a connected IB instance, connecting lazily on first use."""
    global _ib_singleton
    from ib_insync import IB

    if _ib_singleton is not None and _ib_singleton.isConnected():
        return _ib_singleton

    ib = IB()
    try:
        ib.connect(
            ibkr_cfg.host,
            ibkr_cfg.port,
            clientId=ibkr_cfg.client_id,
            timeout=15,
            readonly=True,  # never request order/trade permissions
        )
    except Exception as exc:  # connection refused, etc.
        raise FetchError(
            f"cannot connect to IB at {ibkr_cfg.host}:{ibkr_cfg.port} "
            f"(is TWS/Gateway running?): {exc}"
        ) from exc
    _ib_singleton = ib
    return ib


def _fetch_one(ib, symbol: str, timeframe: str, num_candles: int, use_rth: bool) -> Candles:
    from ib_insync import Stock

    if timeframe not in _BAR_MAP:
        raise FetchError(f"{symbol}: unsupported timeframe '{timeframe}' for IBKR")
    bar_size, duration = _BAR_MAP[timeframe]

    contract = Stock(symbol, "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise FetchError(f"{symbol}: could not qualify contract on SMART/USD")

    bars = ib.reqHistoricalData(
        qualified[0],
        endDateTime="",
        durationStr=duration,
        barSizeSetting=bar_size,
        whatToShow="TRADES",
        useRTH=use_rth,
        formatDate=2,  # UTC epoch
    )
    if not bars:
        raise FetchError(f"{symbol}: no historical data returned for {timeframe}")

    df = pd.DataFrame(
        [
            {
                "time": b.date,
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
            }
            for b in bars
        ]
    )
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").tail(num_candles)
    return validate_candles(df, symbol)


def fetch_ibkr(
    symbol: str, timeframes: list[str], num_candles: int, ibkr_cfg
) -> dict[str, Candles]:
    """Fetch OHLCV candles for each timeframe from IBKR. Raises FetchError."""
    ib = _get_ib(ibkr_cfg)
    out: dict[str, Candles] = {}
    for tf in timeframes:
        out[tf] = _fetch_one(ib, symbol, tf, num_candles, ibkr_cfg.use_rth)
    return out


def disconnect() -> None:
    """Close the shared IB connection if open (call at end of a run)."""
    global _ib_singleton
    if _ib_singleton is not None and _ib_singleton.isConnected():
        _ib_singleton.disconnect()
    _ib_singleton = None
