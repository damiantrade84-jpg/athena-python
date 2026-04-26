import os

with open('bt_scalp_dump.py', 'r', encoding='utf-8') as f:
    new_func = f.read()

with open('backtest_runner.py', 'r', encoding='utf-8') as f:
    content = f.read()

start_idx = content.find("def backtest_pair_scalp")
end_idx = content.find("def run_full_backtest", start_idx)

if start_idx != -1 and end_idx != -1:
    new_content = content[:start_idx] + new_func + "\n\n" + content[end_idx:]
    with open('backtest_runner.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Patched backtest_runner.py")
else:
    print("Could not find function boundaries in backtest_runner.py")
