"""Tests for the paper-trade simulation logic with synthetic 1m candles."""
from __future__ import annotations

from evaluator import SimResult, pnl, simulate_signal

T0 = 1_000_000  # arbitrary epoch base
M = 60


def c(i: int, o: float, h: float, l: float, cl: float):
    """Candle at minute i."""
    return (T0 + i * M, o, h, l, cl)


NOW_FAR = T0 + 10**6  # long past every deadline


def test_long_tp_win():
    candles = [
        c(0, 100, 100.5, 99.8, 100.2),   # touches entry 100
        c(1, 100.2, 101.0, 100.0, 100.9),
        c(2, 100.9, 102.1, 100.8, 102.0),  # touches TP 102
    ]
    r = simulate_signal("LONG", 100, 99, 102, candles, T0, NOW_FAR)
    assert r.status == "tp"
    assert r.exit_price == 102


def test_long_sl_loss():
    candles = [
        c(0, 100, 100.5, 99.9, 100.1),
        c(1, 100.1, 100.3, 98.9, 99.0),  # touches SL 99
    ]
    r = simulate_signal("LONG", 100, 99, 102, candles, T0, NOW_FAR)
    assert r.status == "sl"
    assert r.exit_price == 99


def test_short_tp_win():
    candles = [
        c(0, 100, 100.2, 99.9, 100.0),
        c(1, 100.0, 100.4, 97.9, 98.0),  # TP 98 touched
    ]
    r = simulate_signal("SHORT", 100, 101, 98, candles, T0, NOW_FAR)
    assert r.status == "tp"


def test_not_triggered_when_entry_never_touched():
    candles = [c(i, 105, 106, 104.5, 105.5) for i in range(300)]
    r = simulate_signal("LONG", 100, 99, 102, candles, T0, NOW_FAR)
    assert r.status == "not_triggered"


def test_ambiguous_candle_counts_as_loss():
    candles = [
        c(0, 100, 100.2, 99.9, 100.0),   # entry
        c(1, 100, 102.5, 98.5, 99.0),    # spans BOTH SL 99 and TP 102
    ]
    r = simulate_signal("LONG", 100, 99, 102, candles, T0, NOW_FAR)
    assert r.status == "ambiguous"
    assert r.exit_price == 99  # conservative: loss


def test_same_candle_entry_and_tp():
    candles = [c(0, 99.5, 102.5, 99.4, 102.0)]  # entry 100 and TP 102 same candle
    r = simulate_signal("LONG", 100, 99, 102, candles, T0, NOW_FAR)
    assert r.status == "tp"


def test_pending_when_recent_and_unresolved():
    candles = [c(0, 100, 100.2, 99.9, 100.0), c(1, 100, 100.4, 99.8, 100.1)]
    now = T0 + 2 * M  # signal is 2 minutes old
    r = simulate_signal("LONG", 100, 99, 102, candles, T0, now)
    assert r.status == "pending"
    assert r.entry_ts == T0


def test_expired_closes_at_market():
    # Entered, then drifts sideways past max duration.
    candles = [c(0, 100, 100.2, 99.9, 100.0)] + [
        c(i, 100.5, 100.8, 100.2, 100.6) for i in range(1, 700)
    ]
    r = simulate_signal("LONG", 100, 99, 102, candles, T0, NOW_FAR,
                        max_duration_s=480 * 60)
    assert r.status == "expired"
    assert r.exit_price == 100.6


def test_pnl_math():
    pct, r = pnl("LONG", 100, 99, 102)
    assert pct == 2.0 and r == 2.0
    pct, r = pnl("SHORT", 100, 101, 98)
    assert pct == 2.0 and r == 2.0
    pct, r = pnl("LONG", 100, 99, 99)  # stopped out
    assert pct == -1.0 and r == -1.0


def test_round_trip_fees():
    from evaluator import round_trip_fees
    from config import EvaluationConfig

    ev = EvaluationConfig(crypto_fee_pct=0.10, stock_fee_pct=0.0,
                          stock_fee_min_usd=1.0)
    # Binance: 0.10% por lado sobre 100 = 0.10 x 2
    assert round_trip_fees("crypto", 100, ev) == 0.20
    # IBKR: el minimo de $1/orden domina en posiciones chicas
    assert round_trip_fees("stock", 100, ev) == 2.00
    # IBKR: con posicion grande y % configurado, gana el %
    ev2 = EvaluationConfig(stock_fee_pct=0.05, stock_fee_min_usd=1.0)
    assert round_trip_fees("stock", 10_000, ev2) == 10.00


def test_fees_with_leverage_notional():
    from evaluator import round_trip_fees
    from config import EvaluationConfig

    ev = EvaluationConfig(crypto_fee_pct=0.10)
    # A3: margen 100 x10 = nocional 1000, futures taker 0.05%/lado
    assert round_trip_fees("crypto", 1000, ev, fee_side_pct=0.05) == 1.00
    # sin override usa el spot fee del config
    assert round_trip_fees("crypto", 1000, ev) == 2.00
