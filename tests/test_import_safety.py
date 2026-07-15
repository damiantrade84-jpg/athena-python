import importlib.util
import sqlite3
import threading


def test_athena_module_import_does_not_start_runtime_or_write_db(monkeypatch):
    def fail_thread(self):
        raise AssertionError("thread started during import")

    def fail_connect(*args, **kwargs):
        raise AssertionError("database access during import")

    monkeypatch.setattr(threading.Thread, "start", fail_thread)
    monkeypatch.setattr(sqlite3, "connect", fail_connect)
    spec = importlib.util.spec_from_file_location("athena_import_safety_probe", "athena.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except AssertionError:
        raise
    finally:
        monkeypatch.undo()


def test_research_harness_import_is_safe():
    import scripts.research.forex_fresh_backtest  # noqa: F401
