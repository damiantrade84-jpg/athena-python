from __future__ import annotations

import importlib.util
from pathlib import Path

from duka_volume import DUKA_SYMBOL_MAP


ROOT = Path(__file__).resolve().parents[1]
ATHENA_PATH = ROOT / "athena.py"


def _load_athena_main():
    spec = importlib.util.spec_from_file_location("athena_main_sentiment_test", ATHENA_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_eodhd_sentiment_ticker_map_excludes_unsupported_asset_classes():
    athena = _load_athena_main()

    ticker_map = athena._eodhd_sentiment_ticker_map(
        [
            {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"},
            {"display": "AAPL", "symbol": "AAPL.US", "type": "stock"},
            {"display": "BTC/USD", "symbol": "BTCUSDT", "type": "crypto"},
            {"display": "Gold", "symbol": "GC=F", "type": "commodity"},
            {"display": "S&P 500", "symbol": "GSPC.INDX", "type": "index"},
        ]
    )

    assert ticker_map == {
        "EURUSD.FOREX": "EUR/USD",
        "AAPL.US": "AAPL",
        "BTC-USD.CC": "BTC/USD",
    }


def test_duka_symbol_map_covers_aud_crosses():
    assert DUKA_SYMBOL_MAP["AUD/CHF"] == "AUDCHF"
    assert DUKA_SYMBOL_MAP["AUD/NZD"] == "AUDNZD"
