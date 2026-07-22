"""Shared types for fetchers."""
from __future__ import annotations

import pandas as pd

# A Candles frame has columns: time (tz-aware UTC datetime), open, high, low,
# close, volume — sorted oldest -> newest.
Candles = pd.DataFrame

REQUIRED_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


class FetchError(Exception):
    """Raised when a ticker's data cannot be fetched."""


def validate_candles(df: Candles, symbol: str) -> Candles:
    """Ensure the frame has the expected columns and is non-empty."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise FetchError(f"{symbol}: fetched frame missing columns {missing}")
    if df.empty:
        raise FetchError(f"{symbol}: no candles returned")
    return df[REQUIRED_COLUMNS].reset_index(drop=True)
