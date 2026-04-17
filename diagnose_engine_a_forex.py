"""
Engine A forex diagnostic — Option A (read-only).

Simulates compute_forex_score() under "ideal" forex setup conditions at every
UTC hour 0-23 to measure how often the pipeline returns final_score <= 0 and
WHY, using the real `zero_score_reasons` list.

Does NOT change scoring, config, or data. Does NOT hit MT5/EODHD/Binance.
Uses stubbed OHLC + snapshot dicts that pass structural trend checks so only
the session/ADX/Hurst gates drive the failures.
"""

from __future__ import annotations

import collections
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from forex_scoring import (
    _session_state,
    _trend_gate_detail,
    compute_forex_score,
)
from config import CONFIG


# ─── Synthetic fixtures ──────────────────────────────────────────────────────


def _bull_d1_snap(adx: float = 28.0) -> dict:
    """Clean bullish D1: close > ema200, slope positive, ADX above gate."""
    return {
        "close": 1.1200,
        "ema200": 1.1000,
        "ema200Slope10": 0.0008,
        "adx": adx,
    }


def _bull_h4_snap(adx: float = 28.0) -> dict:
    return {
        "ema50": 1.1180,
        "ema200": 1.1050,
        "adx": adx,
        "rsi": 55.0,
        "macd": 0.0005,
        "macd_signal": 0.0003,
        "close": 1.1200,
    }


def _bull_h1_snap() -> dict:
    return {
        "close": 1.1195,
        "ema21": 1.1190,
        "ema50": 1.1180,
        "ema200": 1.1100,
        "rsi": 48.0,   # pullback zone — good entry quality
        "rsi14": 48.0,
        "adx": 22.0,
    }


def _fake_h1_candles_with_asian_range_break(last_utc_hour: int, bull: bool = True) -> list:
    """Generate 48 H1 candles ending at last_utc_hour with a clear Asian range
    and a London breakout candle at the last slot (if last_utc_hour in 7-9)."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    # align to last_utc_hour
    anchor = now.replace(hour=last_utc_hour)
    if anchor > now:
        anchor -= timedelta(days=1)

    candles = []
    base = 1.1000
    # 48 bars back, so we always span at least one Asian session (0-7 UTC)
    for i in range(48, 0, -1):
        t = anchor - timedelta(hours=i - 1)
        hr = t.hour
        # Asian session candles (0-7 UTC) — tight range 1.0990-1.1010
        if 0 <= hr < 7:
            o = base - 0.0005
            c = base + 0.0005
            hi = base + 0.0008
            lo = base - 0.0008
        else:
            o = base
            c = base + 0.0002
            hi = base + 0.0003
            lo = base - 0.0001
        candles.append({
            "time": t.isoformat(),
            "open": o,
            "high": hi,
            "low": lo,
            "close": c,
            "volume": 1000,
        })
    # Force the last candle to be a strong breakout above Asian high if bull
    last = candles[-1]
    asian_high = 1.1008
    asian_low = 1.0992
    if bull:
        last["open"] = asian_high + 0.0002
        last["close"] = asian_high + 0.0040
        last["high"] = asian_high + 0.0045
        last["low"] = asian_high + 0.0001
    else:
        last["open"] = asian_low - 0.0002
        last["close"] = asian_low - 0.0040
        last["high"] = asian_low - 0.0001
        last["low"] = asian_low - 0.0045
    return candles


_PAIR = {"display": "EUR/USD", "type": "forex", "symbol": "EURUSD"}


# ─── Harness ────────────────────────────────────────────────────────────────


def scan_all_hours(adx: float = 28.0, verbose: bool = False) -> dict:
    """Run compute_forex_score at every UTC hour with ideal bull conditions."""
    reason_counts = collections.Counter()
    hours_with_signal = []
    hours_zero = []

    for h in range(24):
        # Patch _local_to_utc_hour to force a specific UTC hour
        with patch("forex_scoring._local_to_utc_hour", return_value=h):
            candles = _fake_h1_candles_with_asian_range_break(h, bull=True)
            res = compute_forex_score(
                d1_snap=_bull_d1_snap(adx=adx),
                h4_snap=_bull_h4_snap(adx=adx),
                h1_snap=_bull_h1_snap(),
                h1_candles=candles,
                pair=_PAIR,
                backtest_mode=False,
            )
        reasons = res.components.get("zero_score_reasons", []) or []
        if res.final_score > 0:
            hours_with_signal.append((h, res.final_score, res.signal_type))
        else:
            hours_zero.append((h, reasons))
            for r in reasons:
                reason_counts[r] += 1
        if verbose:
            print(f"  UTC {h:02d}: score={res.final_score:.3f} "
                  f"type={res.signal_type:18s} "
                  f"reasons={reasons}")
    return {
        "reason_counts": reason_counts,
        "hours_with_signal": hours_with_signal,
        "hours_zero": hours_zero,
    }


def session_coverage_per_hour() -> None:
    print("\n── SESSION COVERAGE (current config) ──")
    print(f"  session_mode = {CONFIG.get('FOREX_ENGINE', {}).get('session_mode')}")
    print(f"  FOREX_SESSION_FILTER = {CONFIG.get('FOREX_SESSION_FILTER')}")
    print(f"  shoulder_enabled = {CONFIG.get('FOREX_ENGINE', {}).get('session_shoulder_enabled')}")
    print()
    print("  Hour | Non-Asian pair (EUR/USD) | Asian pair (USD/JPY)")
    print("  -----|--------------------------|----------------------")
    nonasian_active = 0
    asian_active = 0
    for h in range(24):
        a = _session_state(h, "EUR/USD")
        b = _session_state(h, "USD/JPY")
        a_tag = f"ACTIVE×{a['multiplier']:.2f}" if a["is_active"] else "BLOCKED"
        b_tag = f"ACTIVE×{b['multiplier']:.2f}" if b["is_active"] else "BLOCKED"
        if a["is_active"]: nonasian_active += 1
        if b["is_active"]: asian_active += 1
        print(f"   {h:02d}  | {a_tag:24s} | {b_tag}")
    print(f"\n  EUR/USD active hours: {nonasian_active}/24  ({100*nonasian_active/24:.0f}%)")
    print(f"  USD/JPY active hours: {asian_active}/24  ({100*asian_active/24:.0f}%)")


def trend_gate_coverage() -> None:
    print("\n── TREND GATE (D1/H4 bull, varying ADX) ──")
    fx = CONFIG.get("FOREX_ENGINE", {})
    print(f"  adx_min = {fx.get('trend_gate_adx_min')}  "
          f"adx_source = {fx.get('trend_gate_adx_source')}  "
          f"soft_enabled = {fx.get('trend_gate_adx_soft_enabled')}")
    print()
    print("  ADX | trend_ok | reason                     | gate_mult")
    print("  ----|----------|----------------------------|----------")
    for adx in [10, 15, 18, 20, 22, 25, 28, 30, 35]:
        d1 = _bull_d1_snap(adx=adx)
        h4 = _bull_h4_snap(adx=adx)
        ok, _dir, detail = _trend_gate_detail(d1, h4)
        gate = detail.get("adx_gate_multiplier", 1.0)
        print(f"   {adx:2d} |  {'PASS' if ok else 'FAIL':4s}    | "
              f"{detail.get('reason',''):26s} | {gate:.3f}")


def full_pipeline_tally() -> None:
    print("\n── FULL PIPELINE: ideal bull (ADX=28, D1+H4 aligned, Hurst neutral) ──")
    r = scan_all_hours(adx=28.0, verbose=True)
    n_signal = len(r["hours_with_signal"])
    print(f"\n  Hours with signal:     {n_signal}/24  ({100*n_signal/24:.0f}%)")
    print(f"  Hours with ZERO score: {24-n_signal}/24")
    print("\n  Zero-score reason frequency:")
    for reason, count in r["reason_counts"].most_common():
        print(f"    {reason:35s} × {count}")

    print("\n── STRESS: what if ADX=20 (below current gate=25)? ──")
    r2 = scan_all_hours(adx=20.0, verbose=False)
    n2 = len(r2["hours_with_signal"])
    print(f"  Hours with signal: {n2}/24")
    for reason, count in r2["reason_counts"].most_common():
        print(f"    {reason:35s} × {count}")

    print("\n── STRESS: ADX=18 (market slightly choppy) ──")
    r3 = scan_all_hours(adx=18.0, verbose=False)
    n3 = len(r3["hours_with_signal"])
    print(f"  Hours with signal: {n3}/24")
    for reason, count in r3["reason_counts"].most_common():
        print(f"    {reason:35s} × {count}")

    print("\n── ISOLATE: disable Hurst gate, leave ADX=28 + session=strict ──")
    fx_cfg = CONFIG.get("FOREX_ENGINE", {})
    fx_cfg["hurst_gate_enabled"] = False
    r4 = scan_all_hours(adx=28.0, verbose=False)
    n4 = len(r4["hours_with_signal"])
    print(f"  Hours with signal: {n4}/24")
    for reason, count in r4["reason_counts"].most_common():
        print(f"    {reason:35s} × {count}")
    fx_cfg["hurst_gate_enabled"] = True  # restore

    print("\n── ISOLATE: session=off + ADX=28 + Hurst on ──")
    fx_cfg["session_mode"] = "off"
    r5 = scan_all_hours(adx=28.0, verbose=False)
    n5 = len(r5["hours_with_signal"])
    print(f"  Hours with signal: {n5}/24")
    for reason, count in r5["reason_counts"].most_common():
        print(f"    {reason:35s} × {count}")
    fx_cfg["session_mode"] = "strict"  # restore

    print("\n── ISOLATE: session=soft + ADX soft enabled + Hurst off ──")
    fx_cfg["session_mode"] = "soft"
    fx_cfg["trend_gate_adx_soft_enabled"] = True
    fx_cfg["hurst_gate_enabled"] = False
    r6 = scan_all_hours(adx=20.0, verbose=False)
    n6 = len(r6["hours_with_signal"])
    print(f"  Hours with signal (all three softened): {n6}/24")
    for reason, count in r6["reason_counts"].most_common():
        print(f"    {reason:35s} × {count}")
    fx_cfg["session_mode"] = "strict"
    fx_cfg["trend_gate_adx_soft_enabled"] = False
    fx_cfg["hurst_gate_enabled"] = True


if __name__ == "__main__":
    print("=" * 70)
    print(" Engine A forex diagnostic  —  Option A, read-only")
    print("=" * 70)
    session_coverage_per_hour()
    trend_gate_coverage()
    full_pipeline_tally()
