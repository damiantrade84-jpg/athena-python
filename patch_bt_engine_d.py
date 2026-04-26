import re

with open('backtest_runner.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Chunk 6 (Engine D scalp)
search_str = '"regime": "SCALP",'
replace_str = '"regime": "SCALP",\n            **build_strategy_lab_telemetry(\n                engine="ENGINE_D",\n                strategy_family="ENGINE_D_SCALP",\n                regime="breakout" if "breakout" in (setup_type or "").lower() else "unknown",\n                setup_type=setup_type,\n                timeframe=exit_tf,\n                failure_reason=exit_reason if exit_reason not in ["TP1", "TP2", "TP_PARTIAL"] else None,\n                entry_reason=None,\n                exit_reason=exit_reason,\n                source_module="backtest_runner",\n                source_function="backtest_pair"\n            ),'
text = text.replace(search_str, replace_str)

with open('backtest_runner.py', 'w', encoding='utf-8') as f:
    f.write(text)
print("Patched Engine D in backtest_runner.py.")
