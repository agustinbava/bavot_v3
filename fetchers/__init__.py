"""Data fetchers for OHLCV candles."""
from .base import Candles, FetchError

__all__ = ["Candles", "FetchError", "fetch_instrument"]


def fetch_instrument(inst, timeframes, num_candles, ibkr_cfg):
    """Dispatch to the right fetcher based on instrument type.

    Imports are lazy so crypto-only runs don't require ib_insync installed.
    """
    if inst.type == "crypto":
        from .binance import fetch_binance

        return fetch_binance(inst.symbol, timeframes, num_candles)
    if inst.type == "stock":
        from .ibkr import fetch_ibkr

        return fetch_ibkr(inst.symbol, timeframes, num_candles, ibkr_cfg)
    raise FetchError(f"unknown instrument type: {inst.type}")
