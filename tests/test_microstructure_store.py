import importlib
import logging
import os
from pathlib import Path
import sys
import time
import uuid


def _load_store_with_temp_db(db_path, monkeypatch, request):
    module_name = "athena.microstructure.microstructure_store"
    previous_module = sys.modules.get(module_name)
    package = sys.modules.get("athena.microstructure")
    had_attr = package is not None and hasattr(package, "microstructure_store")
    previous_attr = getattr(package, "microstructure_store", None) if had_attr else None

    sys.modules.pop(module_name, None)
    if package is not None and hasattr(package, "microstructure_store"):
        delattr(package, "microstructure_store")
    monkeypatch.setenv("MICROSTRUCTURE_DB_PATH", str(db_path))
    store = importlib.import_module(module_name)

    def cleanup():
        sys.modules.pop(module_name, None)
        if previous_module is not None:
            sys.modules[module_name] = previous_module
        if package is not None:
            if had_attr:
                setattr(package, "microstructure_store", previous_attr)
            elif hasattr(package, "microstructure_store"):
                delattr(package, "microstructure_store")

    request.addfinalizer(cleanup)
    return store


def _test_db_path(request) -> Path:
    base = Path("tests") / ".tmp"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"microstructure_store_{os.getpid()}_{uuid.uuid4().hex}.db"
    path.touch()

    def cleanup():
        for candidate in (
            path,
            Path(str(path) + "-wal"),
            Path(str(path) + "-shm"),
        ):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass

    request.addfinalizer(cleanup)
    return path


def test_store_metrics_runs_maintenance_after_write_commit(monkeypatch, request, caplog):
    store = _load_store_with_temp_db(_test_db_path(request), monkeypatch, request)
    monkeypatch.setattr(store, "_SQLITE_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(store, "_MAINTENANCE_INTERVAL_SEC", 0)
    monkeypatch.setattr(store, "_last_maintenance_ts", 0.0)
    caplog.set_level(logging.WARNING, logger="sentinel")

    store.store_metrics(
        {
            "timestamp": time.time(),
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "order_book_imbalance": 0.12,
            "liquidity_wall_detection": -0.05,
            "orderflow_delta": 0.34,
            "liquidity_pressure": 0.23,
        }
    )

    assert "[MicroStore] Maintenance warning" not in caplog.text
    assert len(store.query_latest("BTCUSDT", "binance", 5)) == 1
