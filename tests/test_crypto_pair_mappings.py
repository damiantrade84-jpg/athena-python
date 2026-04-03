from __future__ import annotations

import importlib.util
from pathlib import Path

from bybit_executor import bybit_map_symbol
from scoring import CORR_CLUSTERS, get_pair_score_group


_ATHENA_PATH = Path(__file__).resolve().parents[1] / "athena.py"
_ATHENA_SPEC = importlib.util.spec_from_file_location("athena_main", _ATHENA_PATH)
assert _ATHENA_SPEC and _ATHENA_SPEC.loader
_ATHENA_MOD = importlib.util.module_from_spec(_ATHENA_SPEC)
_ATHENA_SPEC.loader.exec_module(_ATHENA_MOD)

CRYPTO_PAIRS = _ATHENA_MOD.CRYPTO_PAIRS

_EXPECTED_CRYPTOS = {
    "POL/USDT": "POLUSDT",
    "AAVE/USDT": "AAVEUSDT",
    "ALGO/USDT": "ALGOUSDT",
    "ATOM/USDT": "ATOMUSDT",
    "BCH/USDT": "BCHUSDT",
    "ETC/USDT": "ETCUSDT",
    "TRX/USDT": "TRXUSDT",
    "XLM/USDT": "XLMUSDT",
    "UNI/USDT": "UNIUSDT",
    "FIL/USDT": "FILUSDT",
    "ICP/USDT": "ICPUSDT",
    "HBAR/USDT": "HBARUSDT",
    "ARB/USDT": "ARBUSDT",
    "OP/USDT": "OPUSDT",
    "SEI/USDT": "SEIUSDT",
}


def test_crypto_universe_contains_verified_binance_bybit_pairs():
    by_display = {pair["display"]: pair for pair in CRYPTO_PAIRS}

    assert "MATIC/USDT" not in by_display

    for display, symbol in _EXPECTED_CRYPTOS.items():
        pair = by_display.get(display)
        assert pair is not None, f"{display} missing from CRYPTO_PAIRS"
        assert pair["symbol"] == symbol
        assert pair["type"] == "crypto"
        assert pair["source"] == "binance"
        assert pair["enabled"] is True


def test_bybit_map_symbol_matches_new_crypto_pairs():
    for display, symbol in _EXPECTED_CRYPTOS.items():
        expected = f"{display}:USDT"
        assert bybit_map_symbol(display) == expected
        assert bybit_map_symbol(symbol) == expected


def test_crypto_scoring_and_correlation_sets_handle_pol_and_new_pairs():
    assert get_pair_score_group({"display": "POL/USDT", "type": "crypto"}) == "crypto_alt_majors"
    assert get_pair_score_group({"display": "AAVE/USDT", "type": "crypto"}) == "crypto_other"

    cluster = set(CORR_CLUSTERS["crypto_major"])
    for display in _EXPECTED_CRYPTOS:
        assert display in cluster
