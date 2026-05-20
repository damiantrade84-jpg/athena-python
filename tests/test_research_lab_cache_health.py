from __future__ import annotations

import importlib.util
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_path():
    d = Path(tempfile.mkdtemp(prefix="athena_cache_health_test_"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_parquet_cache_health_reports_unavailable_when_no_engine(tmp_path, monkeypatch):
    from athena_research import data_loader

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name in {"pyarrow", "fastparquet"}:
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(data_loader.importlib.util, "find_spec", fake_find_spec)

    health = data_loader.parquet_cache_health(tmp_path)

    assert health["status"] == "cache_unavailable"
    assert health["parquet_engine"] == ""
    assert "pyarrow_or_fastparquet_missing" in health["reason"]


def test_parquet_cache_health_reports_miss_when_engine_exists_but_cache_empty(tmp_path, monkeypatch):
    from athena_research import data_loader

    class _Spec:
        pass

    def fake_find_spec(name, *args, **kwargs):
        if name == "pyarrow":
            return _Spec()
        return None

    monkeypatch.setattr(data_loader.importlib.util, "find_spec", fake_find_spec)

    health = data_loader.parquet_cache_health(tmp_path)

    assert health["status"] == "cache_miss"
    assert health["parquet_engine"] == "pyarrow"
    assert health["cache_files"] == 0
