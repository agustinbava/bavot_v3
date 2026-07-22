"""Unit tests for indicators.py using synthetic OHLCV data (no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config import IndicatorParams
from indicators import (
    build_payload,
    candles_to_arrays,
    detect_levels,
    round_price,
    stoch_rsi,
    timeframe_features,
    volume_ratio,
)


def make_candles(n: int = 60, seed: int = 0) -> pd.DataFrame:
    """Deterministic random-walk OHLCV frame."""
    rng = np.random.default_rng(seed)
    times = pd.date_range("2026-07-14 13:30", periods=n, freq="5min", tz="UTC")
    steps = rng.normal(0, 1, n).cumsum()
    close = 100 + steps
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close - rng.normal(0, 0.5, n)
    volume = rng.uniform(1000, 5000, n)
    return pd.DataFrame(
        {"time": times, "open": open_, "high": high, "low": low,
         "close": close, "volume": volume}
    )


PARAMS = IndicatorParams()


def test_round_price_default_two_decimals():
    assert round_price(123.4567) == 123.46
    assert round_price(45.0) == 45.0


def test_round_price_bumps_precision_for_small_values():
    assert round_price(0.123456) == 0.12346      # <1 -> 5 decimals
    assert round_price(0.00001234) == 0.00001234  # <0.01 -> 8 decimals


def test_round_price_handles_nan():
    assert round_price(float("nan")) is None


def test_stoch_rsi_returns_k_and_d_in_range():
    df = make_candles(60)
    res = stoch_rsi(df["close"], PARAMS)
    assert set(res) == {"k", "d"}
    for val in res.values():
        assert val is None or 0.0 <= val <= 100.0


def test_stoch_rsi_insufficient_data_returns_none():
    df = make_candles(5)
    res = stoch_rsi(df["close"], PARAMS)
    assert res == {"k": None, "d": None} or all(v is None for v in res.values())


def test_volume_ratio_matches_manual():
    vol = pd.Series([100.0] * 19 + [200.0])  # sma20 == 105, last == 200
    expected = round(200.0 / (sum([100.0] * 19 + [200.0]) / 20), 2)
    assert volume_ratio(vol, 20) == expected


def test_volume_ratio_insufficient_data():
    assert volume_ratio(pd.Series([1.0, 2.0, 3.0]), 20) is None


def test_detect_levels_supports_below_resistances_above():
    df = make_candles(60, seed=3)
    price = float(df["close"].iloc[-1])
    levels = detect_levels(df, lookback=2, decimals=2)
    assert all(s < price for s in levels["supports"])
    assert all(r > price for r in levels["resistances"])
    assert len(levels["supports"]) <= 3
    assert len(levels["resistances"]) <= 3


def test_candles_to_arrays_shape_and_rounding():
    df = make_candles(10)
    arrays = candles_to_arrays(df, decimals=2)
    assert len(arrays) == 10
    for row in arrays:
        assert len(row) == 5  # [o, h, l, c, v] — sin timestamp por vela
        # price fields rounded to <=2 decimals for values >= 1
        assert round(row[3], 2) == row[3]


def test_round_volume_three_sig_figs():
    from indicators import round_volume

    assert round_volume(1254.47) == 1250
    assert round_volume(1234567.0) == 1230000
    assert round_volume(9.154) == 9.15
    assert round_volume(0.0) == 0


def test_timeframe_features_keys_and_trim():
    df = make_candles(60)
    feats = timeframe_features(df, PARAMS, decimals=2, send_candles=35)
    assert set(feats) == {
        "stoch_rsi", "volume_ratio", "session_high",
        "session_low", "levels", "candles", "candles_start",
    }
    assert feats["session_high"] >= feats["session_low"]
    # el recorte afecta sólo a las velas enviadas, no a los indicadores
    assert len(feats["candles"]) == 35
    full = timeframe_features(df, PARAMS, decimals=2)
    assert feats["stoch_rsi"] == full["stoch_rsi"]
    assert feats["session_high"] == full["session_high"]


def test_build_payload_structure():
    candles = {tf: make_candles(60, seed=i) for i, tf in enumerate(["5m", "15m", "30m"])}
    payload = build_payload("BTCUSDT", "crypto", candles, PARAMS, decimals=2)
    assert payload["symbol"] == "BTCUSDT"
    assert payload["type"] == "crypto"
    assert set(payload["timeframes"]) == {"5m", "15m", "30m"}
    assert isinstance(payload["price"], float)
    assert "T" in payload["timestamp"]  # ISO format
