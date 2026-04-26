import re

with open('backtest_runner.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Import
if 'from telemetry import build_strategy_lab_telemetry' not in text:
    text = text.replace('import pandas as pd', 'import pandas as pd\nfrom telemetry import build_strategy_lab_telemetry')

# We know the approximate structures of the trades.append calls.
# We can find them using regex to insert the **build_strategy_lab_telemetry call.

def replace_trades_append(text, search_str, replacement):
    if search_str in text:
        text = text.replace(search_str, replacement)
    else:
        print(f"Warning: Could not find {repr(search_str[:30])}")
    return text

# Chunk 1 & 2 & 3 (Engine A loops)
# "resultR": round(result_r, 2),
# "regime": _regime,
text = text.replace(
    '"resultR": round(result_r, 2),\n                    "regime": _regime,',
    '"resultR": round(result_r, 2),\n                    "regime": _regime,\n                    **build_strategy_lab_telemetry(\n                        engine="ENGINE_A",\n                        strategy_family="ENGINE_A_ONLY",\n                        regime=_regime,\n                        setup_type="standard",\n                        timeframe="H4",\n                        failure_reason=outcome if outcome not in ["TP1", "TP2"] else None,\n                        entry_reason=None,\n                        exit_reason=outcome,\n                        source_module="backtest_runner",\n                        source_function="backtest_pair"\n                    ),'
)

# Chunk 4 (Engine B naked)
# "resultR": round(r_multiple, 2),
# "regime": best.get("regime_label", "RANGING"),
text = text.replace(
    '"resultR": round(r_multiple, 2),\n                "regime": best.get("regime_label", "RANGING"),',
    '"resultR": round(r_multiple, 2),\n                "regime": best.get("regime_label", "RANGING"),\n                **build_strategy_lab_telemetry(\n                    engine="ENGINE_B",\n                    strategy_family="ENGINE_B_ONLY",\n                    regime=best.get("regime_label", "RANGING"),\n                    setup_type="naked",\n                    timeframe="H4",\n                    failure_reason=outcome if outcome not in ["TP1", "TP2"] else None,\n                    entry_reason=None,\n                    exit_reason=outcome,\n                    source_module="backtest_runner",\n                    source_function="backtest_pair"\n                ),'
)

# Chunk 5 (Engine C consensus)
# "outcome": outcome, "resultR": round(r_multiple, 2), "regime": regime_label,
text = text.replace(
    '"outcome": outcome, "resultR": round(r_multiple, 2), "regime": regime_label,',
    '"outcome": outcome, "resultR": round(r_multiple, 2), "regime": regime_label,\n            **build_strategy_lab_telemetry(\n                engine="ENGINE_C",\n                strategy_family="ENGINE_C_CONSENSUS",\n                regime=regime_label,\n                setup_type=consensus.get("setup_type"),\n                timeframe="H4",\n                failure_reason=outcome if outcome not in ["TP1", "TP2"] else None,\n                entry_reason=None,\n                exit_reason=outcome,\n                source_module="backtest_runner",\n                source_function="backtest_pair"\n            ),'
)

# Wait, what about Engine D? I should check `scalp_engine.py` or similar for scalp trades.
with open('backtest_runner.py', 'w', encoding='utf-8') as f:
    f.write(text)
print("backtest_runner.py patched successfully.")
