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


def test_signal_pair_resolver_accepts_slashless_forex_alias():
    pair = _ATHENA_MOD._resolve_pair_from_signal({"symbol": "EURCHF"})

    assert pair["display"] == "EUR/CHF"
    assert pair["symbol"] == "EURCHF=X"
    assert pair["type"] == "forex"
    assert pair["source"] == "mt5"


def test_signal_pair_resolver_aliases_never_change_pair_source_or_type():
    checked = 0
    for expected in _ATHENA_MOD.ALL_PAIRS:
        aliases = {
            str(expected.get("symbol") or ""),
            str(expected.get("display") or ""),
        }
        display = expected.get("display")
        if display:
            slashless = str(display).replace("/", "")
            aliases.update({slashless, f"BINANCE:{slashless}", f"FX:{slashless}"})
        symbol = expected.get("symbol")
        if symbol:
            aliases.add(str(symbol).replace("=X", ""))

        for alias in {alias for alias in aliases if alias}:
            checked += 1
            pair = _ATHENA_MOD._resolve_pair_from_signal({"symbol": alias})
            assert pair is not None, alias
            assert pair["display"] == expected["display"], alias
            assert pair["symbol"] == expected["symbol"], alias
            assert pair["type"] == expected["type"], alias
            assert pair["source"] == expected["source"], alias

    assert checked > len(_ATHENA_MOD.ALL_PAIRS)
