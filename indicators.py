"""Technical indicators and per-instrument payload construction.

All indicator math lives here so it can be unit-tested with synthetic data,
independent of any network fetcher.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import pandas_ta as ta

from config import IndicatorParams


def round_price(x: float, decimals: int = 2) -> float:
    """Round a price, auto-bumping precision for sub-dollar values.

    Stocks/ETFs and BTC/ETH round to `decimals` (default 2). Cheap coins keep
    enough digits to stay meaningful rather than collapsing to 0.00.
    """
    if x is None or pd.isna(x):
        return None  # type: ignore[return-value]
    ax = abs(x)
    if ax >= 1:
        return round(float(x), decimals)
    if ax >= 0.01:
        return round(float(x), 5)
    return round(float(x), 8)


def stoch_rsi(close: pd.Series, params: IndicatorParams) -> dict[str, float | None]:
    """Return the latest Stoch RSI %K and %D. None if not enough data."""
    df = ta.stochrsi(
        close,
        length=params.stochrsi_stoch_length,
        rsi_length=params.stochrsi_rsi_length,
        k=params.stochrsi_k,
        d=params.stochrsi_d,
    )
    if df is None or df.empty:
        return {"k": None, "d": None}

    k_col = next((c for c in df.columns if "STOCHRSIk" in c), None)
    d_col = next((c for c in df.columns if "STOCHRSId" in c), None)
    last = df.iloc[-1]

    def _val(col: str | None) -> float | None:
        if col is None or pd.isna(last[col]):
            return None
        return round(float(last[col]), 2)

    return {"k": _val(k_col), "d": _val(d_col)}


def volume_ratio(volume: pd.Series, sma_length: int = 20) -> float | None:
    """Last candle volume divided by its N-period SMA. None if insufficient."""
    if len(volume) < sma_length:
        return None
    sma = volume.rolling(sma_length).mean().iloc[-1]
    if pd.isna(sma) or sma == 0:
        return None
    return round(float(volume.iloc[-1] / sma), 2)


def detect_levels(
    df: pd.DataFrame, lookback: int, decimals: int, max_each: int = 3
) -> dict[str, list[float]]:
    """Detect simple swing-based support/resistance around current price.

    A swing high is a bar whose high exceeds `lookback` bars on each side;
    swing low is the mirror. Returns up to `max_each` supports (below price)
    and resistances (above price), nearest first.
    """
    highs, lows = df["high"], df["low"]
    n = len(df)
    price = float(df["close"].iloc[-1])

    swing_highs: list[float] = []
    swing_lows: list[float] = []
    for i in range(lookback, n - lookback):
        window = range(i - lookback, i + lookback + 1)
        if all(highs.iloc[i] >= highs.iloc[j] for j in window):
            swing_highs.append(float(highs.iloc[i]))
        if all(lows.iloc[i] <= lows.iloc[j] for j in window):
            swing_lows.append(float(lows.iloc[i]))

    resistances = sorted({h for h in swing_highs if h > price})[:max_each]
    supports = sorted({l for l in swing_lows if l < price}, reverse=True)[:max_each]

    return {
        "supports": [round_price(x, decimals) for x in supports],
        "resistances": [round_price(x, decimals) for x in resistances],
    }


def round_volume(v: float) -> float | int:
    """Volume to 3 significant figures; integers stay integers (token saving)."""
    x = float(f"{float(v):.3g}")
    return int(x) if x == int(x) else x


def candles_to_arrays(df: pd.DataFrame, decimals: int) -> list[list[Any]]:
    """Compact candle representation: [o, h, l, c, v].

    Timestamps are intentionally omitted — the payload carries the first
    candle's start time and the interval is the timeframe key.
    """
    out: list[list[Any]] = []
    for row in df.itertuples(index=False):
        out.append(
            [
                round_price(row.open, decimals),
                round_price(row.high, decimals),
                round_price(row.low, decimals),
                round_price(row.close, decimals),
                round_volume(row.volume),
            ]
        )
    return out


def timeframe_features(
    df: pd.DataFrame, params: IndicatorParams, decimals: int,
    send_candles: int | None = None,
) -> dict[str, Any]:
    """Compute the indicator block + candle arrays for a single timeframe.

    Indicators always use the full frame; send_candles trims only the raw
    candle arrays shipped to the model (token saving on context TFs).
    """
    sent = df if send_candles is None else df.tail(send_candles)
    return {
        "stoch_rsi": stoch_rsi(df["close"], params),
        "volume_ratio": volume_ratio(df["volume"], params.volume_sma),
        "session_high": round_price(float(df["high"].max()), decimals),
        "session_low": round_price(float(df["low"].min()), decimals),
        "levels": detect_levels(df, params.swing_lookback, decimals),
        "candles_start": sent["time"].iloc[0].isoformat(),
        "candles": candles_to_arrays(sent, decimals),
    }


def build_payload(
    symbol: str,
    inst_type: str,
    candles_by_tf: dict[str, pd.DataFrame],
    params: IndicatorParams,
    decimals: int,
    round_trip_cost_pct: float | None = None,
    timing_candles: int | None = None,
    context_candles: int | None = None,
) -> dict[str, Any]:
    """Assemble the compact per-instrument JSON payload sent to the LLM.

    round_trip_cost_pct: total commissions (entry + exit) as % of the
    position — lets the strategy demand targets that clear the fee hurdle.
    timing_candles/context_candles: raw candles shipped for the first
    (timing) timeframe vs the rest; indicators always use the full data.
    """
    # Current price / timestamp come from the shortest available timeframe.
    ref_tf = next(iter(candles_by_tf))
    ref_df = candles_by_tf[ref_tf]
    last = ref_df.iloc[-1]

    payload = {
        "symbol": symbol,
        "type": inst_type,
        "price": round_price(float(last["close"]), decimals),
        "timestamp": last["time"].isoformat(),
        "candle_format": "[open, high, low, close, volume]; first candle "
                         "opens at each timeframe's candles_start, spaced by "
                         "the timeframe interval",
        "timeframes": {
            tf: timeframe_features(
                df, params, decimals,
                send_candles=timing_candles if tf == ref_tf else context_candles,
            )
            for tf, df in candles_by_tf.items()
        },
    }
    if round_trip_cost_pct is not None:
        payload["trading_costs"] = {"round_trip_pct": round(round_trip_cost_pct, 3)}
    return payload
