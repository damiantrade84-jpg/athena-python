"""Targeted test for the TF-3 fail-closed fix in ``athena.fetch_mt5``.

Verifies that:
  * a valid timeframe ("H4") resolves to the matching MT5 constant and is the
    value passed to ``copy_rates_from_pos`` (NOT H1);
  * an unknown/malformed timeframe ("H2", "BAD", "", None) fails closed with an
    explicit error status, never resolves to H1, and never reaches the MT5
    rates fetch.

The ``athena.py`` monolith is loaded *by file path* (it is shadowed by the
``athena/`` package on a plain ``import athena``) — same pattern as
``engine_b_batch._worker_init`` / ``tools/run_backtest_matrix``. The real
``mt5_executor`` is stubbed so no live terminal is required.

NOTE: under the repo ``tests/`` conftest, collection currently imports the ASE
stack which requires ``sklearn``; in environments lacking it, run this file
directly: ``python tests/test_fetch_mt5_timeframe_resolution.py``.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakeMT5:
    """Minimal MT5 surface used by ``fetch_mt5`` timeframe resolution."""

    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 16385
    TIMEFRAME_H4 = 16388
    TIMEFRAME_D1 = 16408

    def __init__(self):
        self.copy_calls = []

    def symbol_select(self, *_a, **_k):
        return True

    def copy_rates_from_pos(self, symbol, tf, start, count):
        # Record the resolved timeframe constant, then return "no bars" so the
        # function exits via its normal empty-result path without running the
        # heavy timezone/boundary logic.
        self.copy_calls.append(tf)
        return None

    def last_error(self):
        return (0, "stub")


def _ensure_optional_deps():
    """Stub heavy optional deps that are not needed for TF resolution.

    Loading the monolith pulls in the ASE registry, which imports ``sklearn``
    only for its ``__version__`` string. Provide a minimal stub when it is
    absent so this targeted test runs without the full ML stack."""
    try:
        import sklearn  # noqa: F401
    except Exception:
        import types

        stub = types.ModuleType("sklearn")
        stub.__version__ = "0.0.0-stub"
        sys.modules["sklearn"] = stub


def _load_athena_monolith():
    os.environ.setdefault(
        "ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK"
    )
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    _ensure_optional_deps()
    path = os.path.join(_REPO_ROOT, "athena.py")
    spec = importlib.util.spec_from_file_location("athena_monolith_tftest", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _patch_mt5(mod):
    """Stub the broker layer so ``fetch_mt5`` reaches TF resolution offline."""
    import mt5_executor

    fake = _FakeMT5()
    mt5_executor.mt5_connect = lambda: True
    mt5_executor._get_mt5 = lambda: fake
    mt5_executor.mt5_map_symbol = lambda _s: "EURUSD"
    # Avoid any provider/network fallback on the (intentionally) empty fetch.
    mod._fetch_fallback_candles = lambda *_a, **_k: (None, None)
    return fake


def _make_pair():
    return {"display": "EUR/USD", "symbol": "EUR/USD", "type": "forex", "source": "mt5"}


def test_valid_timeframe_resolves_to_matching_constant():
    mod = _load_athena_monolith()
    fake = _patch_mt5(mod)
    res = mod.fetch_mt5(_make_pair(), "H4", 10)
    # Reached the rates fetch with the H4 constant — and NOT H1.
    assert fake.copy_calls == [_FakeMT5.TIMEFRAME_H4]
    assert fake.copy_calls[0] != _FakeMT5.TIMEFRAME_H1
    # Empty bars -> normal error status (no fallback patched in).
    assert res["error"] is True


_VALID_TF_CASES = [
    ("M1", _FakeMT5.TIMEFRAME_M1),
    ("M5", _FakeMT5.TIMEFRAME_M5),
    ("M15", _FakeMT5.TIMEFRAME_M15),
    ("M30", _FakeMT5.TIMEFRAME_M30),
    ("H1", _FakeMT5.TIMEFRAME_H1),
    ("H4", _FakeMT5.TIMEFRAME_H4),
    ("D1", _FakeMT5.TIMEFRAME_D1),
]


@pytest.mark.parametrize("tf, expected_const", _VALID_TF_CASES)
def test_every_supported_tf_resolves_to_its_own_constant(tf, expected_const):
    # Explicitly pins H1 -> TIMEFRAME_H1 and M15 -> TIMEFRAME_M15 (and the rest):
    # each supported TF reaches copy_rates_from_pos with its own constant.
    mod = _load_athena_monolith()
    fake = _patch_mt5(mod)
    mod.fetch_mt5(_make_pair(), tf, 10)
    assert fake.copy_calls == [expected_const]


@pytest.mark.parametrize("bad_tf", ["H2", "BAD", "", "   ", None, "M2"])
def test_unknown_timeframe_fails_closed_without_h1(bad_tf):
    mod = _load_athena_monolith()
    fake = _patch_mt5(mod)
    res = mod.fetch_mt5(_make_pair(), bad_tf, 10)
    # Must NOT have attempted any rates fetch (so it cannot have served H1).
    assert fake.copy_calls == []
    assert res["error"] is True
    assert "unsupported MT5 timeframe" in res["detail"]


def test_lowercase_valid_timeframe_normalizes_not_falls_back():
    # Previously "h4" missed the map and silently became H1; now it normalizes.
    mod = _load_athena_monolith()
    fake = _patch_mt5(mod)
    mod.fetch_mt5(_make_pair(), "h4", 10)
    assert fake.copy_calls == [_FakeMT5.TIMEFRAME_H4]


if __name__ == "__main__":
    # Standalone runner so the test works even where the tests/ conftest is
    # blocked by missing optional deps (e.g. sklearn).
    failures = 0
    test_valid_timeframe_resolves_to_matching_constant()
    print("PASS test_valid_timeframe_resolves_to_matching_constant")
    for tf, const in _VALID_TF_CASES:
        try:
            test_every_supported_tf_resolves_to_its_own_constant(tf, const)
            print(f"PASS test_every_supported_tf_resolves[{tf}]")
        except AssertionError as exc:  # pragma: no cover
            failures += 1
            print(f"FAIL valid[{tf}]: {exc}")
    for tf in ["H2", "BAD", "", "   ", None, "M2"]:
        try:
            test_unknown_timeframe_fails_closed_without_h1(tf)
            print(f"PASS test_unknown_timeframe_fails_closed_without_h1[{tf!r}]")
        except AssertionError as exc:  # pragma: no cover
            failures += 1
            print(f"FAIL [{tf!r}]: {exc}")
    test_lowercase_valid_timeframe_normalizes_not_falls_back()
    print("PASS test_lowercase_valid_timeframe_normalizes_not_falls_back")
    print("ALL_OK" if failures == 0 else f"{failures} FAILURE(S)")
    sys.exit(1 if failures else 0)
