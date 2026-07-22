"""Binance public klines fetcher (no auth required)."""
from __future__ import annotations

import pandas as pd
import requests

from .base import Candles, FetchError, validate_candles

_BASE_URL = "https://api.binance.com/api/v3/klines"

# Our timeframe labels map 1:1 to Binance intervals.
_INTERVAL_MAP = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "1d": "1d",
}


def _fetch_one(symbol: str, timeframe: str, limit: int) -> Candles:
    interval = _INTERVAL_MAP.get(timeframe)
    if interval is None:
        raise FetchError(f"{symbol}: unsupported timeframe '{timeframe}' for Binance")

    resp = requests.get(
        _BASE_URL,
        params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
        timeout=15,
    )
    if resp.status_code != 200:
        raise FetchError(
            f"{symbol}: Binance {timeframe} HTTP {resp.status_code}: {resp.text[:200]}"
        )

    rows = resp.json()
    if not isinstance(rows, list) or not rows:
        raise FetchError(f"{symbol}: empty Binance response for {timeframe}")

    # Kline row: [openTime, open, high, low, close, volume, closeTime, ...]
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["time", "open", "high", "low", "close", "volume"]
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return validate_candles(df, symbol)


def fetch_binance(
    symbol: str, timeframes: list[str], num_candles: int
) -> dict[str, Candles]:
    """Fetch OHLCV candles for each timeframe. Raises FetchError on failure."""
    out: dict[str, Candles] = {}
    for tf in timeframes:
        out[tf] = _fetch_one(symbol, tf, num_candles)
    return out
