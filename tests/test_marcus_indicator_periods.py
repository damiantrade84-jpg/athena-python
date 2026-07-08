"""Marcus raw H4 table must use per-group indicator periods, not fixed 50/14."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

ATHENA_PATH = Path(__file__).resolve().parents[1] / "athena.py"


def _load_build_signal_message():
    src = ATHENA_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(ATHENA_PATH))
    fn_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_build_signal_message"
    )
    module = ast.Module(body=[fn_node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "CONFIG": {"LEARNING_MIN_TRADES": 5},
        "json": json,
        "log": SimpleNamespace(debug=lambda *args, **kwargs: None),
        "fetch_dxy_context": lambda: None,
        "fetch_yield_curve": lambda: None,
        "fetch_div_split_context": lambda: None,
    }
    exec(compile(module, str(ATHENA_PATH), "exec"), namespace)
    return namespace["_build_signal_message"]


def _sample_candles(n: int = 80):
    candles = []
    price = 1.1000
    for i in range(n):
        price += 0.0001
        candles.append(
            {
                "t": f"2026-01-{i+1:02d}T12:00:00",
                "o": price,
                "h": price + 0.0005,
                "l": price - 0.0005,
                "c": price,
                "v": 1000,
            }
        )
    return candles


def test_forex_majors_raw_header_uses_per_group_periods_not_50_14():
    build_msg = _load_build_signal_message()
    signal = {
        "pair": "EURUSD",
        "direction": "LONG",
        "price": 1.1006,
        "sl": 1.0950,
        "tp1": 1.1100,
        "tp2": 1.1150,
        "rr1": 2.0,
        "rr2": 3.0,
        "confluenceScore": 2.1,
        "maxScore": 3.0,
        "type": "forex",
        "scoreGroup": "forex_majors",
        "h4Candles": _sample_candles(),
        "h4": {"snap": {"adxPct": 50, "adxLabel": "normal", "atrPct": 40, "atrLabel": "normal"}},
        "spread": 0.5,
        "trendState": "TRENDING",
    }
    text = build_msg(signal, None, "intraday", {"intraday": "H4+H1"})
    assert "Engine A scoring periods: trend=26 momentum=60 long=200 rsi=18" in text
    assert "EMA26" in text
    assert "EMA60" in text
    assert "RSI18" in text
    assert "EMA50" not in text
    assert "RSI14" not in text.split("Engine A scoring periods")[0] + text.split("RSI18")[0]


def test_crypto_raw_header_uses_crypto_periods():
    build_msg = _load_build_signal_message()
    signal = {
        "pair": "BTCUSDT",
        "direction": "LONG",
        "price": 50000.0,
        "sl": 49000.0,
        "tp1": 52000.0,
        "tp2": 54000.0,
        "rr1": 2.0,
        "rr2": 4.0,
        "confluenceScore": 2.0,
        "maxScore": 3.0,
        "type": "crypto",
        "scoreGroup": "crypto_btc",
        "h4Candles": _sample_candles(),
        "h4": {"snap": {"adxPct": 50, "adxLabel": "normal"}},
        "spread": 0.4,
        "trendState": "TRENDING",
    }
    text = build_msg(signal, None, "intraday", {"intraday": "H4+H1"})
    assert "trend=18 momentum=40" in text
    assert "EMA18" in text
    assert "EMA40" in text
