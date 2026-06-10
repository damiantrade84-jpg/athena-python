import sqlite3


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_exit_mode_in_schema_and_migration():
    athena_src = _read("athena.py")
    # CREATE TABLE column AND the idempotent migration list entry.
    assert "exit_mode" in athena_src
    assert '("exit_mode", "TEXT")' in athena_src  # migration tuple


def test_exit_mode_in_monitor_select():
    mon_src = _read("timed_exit_monitor.py")
    # The open-rows SELECT must pull exit_mode (appended after grade).
    assert "grade, exit_mode" in mon_src
    assert "FROM   audit_log" in mon_src


def test_exit_mode_threaded_into_live_position_row():
    mon_src = _read("timed_exit_monitor.py")
    assert '"exit_mode": audit.get("exit_mode")' in mon_src


def test_exit_mode_wired_into_all_three_engine_a_paths():
    exec_src = _read("execution.py")
    at_src = _read("auto_trader.py")
    # helper called in quick_execute + execute (execution.py) and auto_trader
    assert exec_src.count("apply_engine_a_exit_mode(") == 2
    assert "apply_engine_a_exit_mode(" in at_src
    # exit_mode present in each success-INSERT column list
    assert "max_score,score_pct,exit_mode" in exec_src                       # quick_execute
    assert "signal_price_ref,slippage_bps,max_score,score_pct,exit_mode" in exec_src  # execute
    assert "fee_cost, exit_mode)" in at_src                                  # auto_trader


def test_alter_adds_exit_mode_when_missing():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts TEXT)")
    cols = {r[1] for r in con.execute("PRAGMA table_info(audit_log)")}
    assert "exit_mode" not in cols
    con.execute("ALTER TABLE audit_log ADD COLUMN exit_mode TEXT")
    cols = {r[1] for r in con.execute("PRAGMA table_info(audit_log)")}
    assert "exit_mode" in cols
