from __future__ import annotations

import json
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from athena_research.commodity_data_audit.freeze import fetch_chunks_with_resume, freeze_one_series
from athena_research.commodity_data_audit.freeze_quality import audit_freeze_bars, exclude_forming_bar
from athena_research.commodity_data_audit.freeze_registry import (
    PHASE_1_MT5_TERMINAL_ALIASES,
    REJECTED_RESEARCH_LOADER_ALIASES,
    resolve_phase1_mt5_symbol,
)
from athena_research.commodity_data_audit.freeze_store import (
    FreezeStoreError,
    iter_utc_chunks,
    merge_bars_by_time,
    write_json_immutable,
    write_manifest_if_absent,
)
from athena_research.commodity_data_audit.spread_audit import (
    SpreadFieldStatus,
    classify_spread_field,
)
from athena_research.commodity_data_audit.metadata_policy import (
    ALLOWED_RAW_BAR_KEYS,
    find_forbidden_persistence_keys,
)
from athena_research.commodity_data_audit.mt5_reader import Mt5ReadError


def _scratch_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / f"_tmp_freeze_{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _bar(ts: int, close: float = 1.0, spread: int = 10) -> dict:
    return {
        "time": ts,
        "open": close,
        "high": close + 0.1,
        "low": close - 0.1,
        "close": close,
        "tick_volume": 100,
        "spread": spread,
        "real_volume": 0,
    }


def test_phase1_alias_parity_rejects_research_loader_aliases():
    assert resolve_phase1_mt5_symbol("WTI Oil") == "SpotCrude"
    assert resolve_phase1_mt5_symbol("Brent Oil") == "SpotBrent"
    assert "USOIL" in REJECTED_RESEARCH_LOADER_ALIASES["WTI Oil"]
    assert PHASE_1_MT5_TERMINAL_ALIASES["WTI Oil"] == "SpotCrude"
    assert PHASE_1_MT5_TERMINAL_ALIASES["Brent Oil"] == "SpotBrent"


def test_iter_utc_chunks_cover_range_without_gaps():
    start = datetime(2014, 1, 1, tzinfo=timezone.utc)
    end = datetime(2014, 3, 1, tzinfo=timezone.utc)
    chunks = iter_utc_chunks(start, end, chunk_days=30)
    assert chunks[0][0] == start
    assert chunks[-1][1] == end
    for (_s, e), (n, _ne) in zip(chunks, chunks[1:]):
        assert e == n


def test_merge_bars_by_time_deduplicates_boundary_bar():
    t0 = int(datetime(2014, 1, 1, tzinfo=timezone.utc).timestamp())
    t1 = int(datetime(2014, 2, 1, tzinfo=timezone.utc).timestamp())
    left = [_bar(t0, close=1.0), _bar(t1, close=2.0)]
    right = [_bar(t1, close=2.5), _bar(t1 + 3600, close=3.0)]
    merged = merge_bars_by_time([left, right])
    assert [bar["time"] for bar in merged] == [t0, t1, t1 + 3600]
    assert merged[1]["close"] == 2.5


def test_fetch_chunks_with_resume_skips_completed_chunks():
    tmp_path = _scratch_dir("resume")
    try:
        start = datetime(2014, 1, 1, tzinfo=timezone.utc)
        end = datetime(2014, 2, 1, tzinfo=timezone.utc)
        calls: list[tuple[datetime, datetime]] = []

        def _copy(_terminal: str, _tf: str, s: datetime, e: datetime) -> list[dict]:
            calls.append((s, e))
            ts = int(s.timestamp())
            return [_bar(ts, close=float(len(calls)))]

        merged1, _ = fetch_chunks_with_resume(
            canonical_symbol="XAU/USD",
            terminal_symbol="XAUUSD",
            timeframe="H4",
            requested_start=start,
            requested_end=end,
            chunk_days=15,
            copy_rates=_copy,
            raw_root=tmp_path,
            resume=True,
        )
        assert len(calls) >= 2
        calls.clear()
        merged2, _ = fetch_chunks_with_resume(
            canonical_symbol="XAU/USD",
            terminal_symbol="XAUUSD",
            timeframe="H4",
            requested_start=start,
            requested_end=end,
            chunk_days=15,
            copy_rates=_copy,
            raw_root=tmp_path,
            resume=True,
        )
        assert calls == []
        assert merged1 == merged2
    finally:
        _cleanup(tmp_path)


def test_exclude_forming_bar_uses_timeframe_close_semantics():
    as_of = datetime(2024, 1, 1, 5, 0, tzinfo=timezone.utc)
    bars = [
        _bar(int(datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp()), close=1.0),
        _bar(int(datetime(2024, 1, 1, 4, 0, tzinfo=timezone.utc).timestamp()), close=2.0),
    ]
    confirmed = exclude_forming_bar(bars, timeframe="H4", as_of=as_of)
    assert len(confirmed) == 1
    assert confirmed[0]["close"] == 1.0


def test_write_json_immutable_refuses_conflicting_overwrite():
    tmp_path = _scratch_dir("immutable")
    try:
        path = tmp_path / "raw.json"
        write_json_immutable(path, [{"time": 1}])
        write_json_immutable(path, [{"time": 1}])
        with pytest.raises(FreezeStoreError):
            write_json_immutable(path, [{"time": 2}])
    finally:
        _cleanup(tmp_path)


def test_manifest_is_deterministic_for_same_payload():
    tmp_path = _scratch_dir("manifest")
    try:
        manifest = {
            "schema": "commodity_freeze_manifest_v1",
            "canonical_symbol": "XAU/USD",
            "row_count": 1,
        }
        path = tmp_path / "x.manifest.json"
        first = write_manifest_if_absent(path, manifest)
        second = write_manifest_if_absent(path, manifest)
        assert first == second
        assert len(first) == 64
    finally:
        _cleanup(tmp_path)


def test_spread_field_classification():
    usable = classify_spread_field([_bar(1, spread=10), _bar(2, spread=12)], point_size=0.01)
    assert usable.status == SpreadFieldStatus.OBSERVED_USABLE

    zero_only = classify_spread_field([_bar(1, spread=0), _bar(2, spread=0)], point_size=0.01)
    assert zero_only.missing_spread_count == 2
    assert zero_only.observed_usable_count == 0

    missing = classify_spread_field([{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1}], point_size=0.01)
    assert missing.status == SpreadFieldStatus.UNAVAILABLE


def test_audit_freeze_bars_flags_future_and_duplicate_issues():
    now = datetime(2024, 1, 10, tzinfo=timezone.utc)
    t0 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    t1 = int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp())
    future = int(datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp())
    bars = [_bar(t0), _bar(t1), _bar(t1), _bar(future)]
    report = audit_freeze_bars(
        bars,
        timeframe="D1",
        requested_start=datetime(2014, 1, 1, tzinfo=timezone.utc),
        requested_end=now,
        min_bars=2,
        as_of=now,
    )
    assert "duplicate_timestamps" in report.issues
    assert "future_bars" in report.issues


def test_freeze_one_series_writes_manifest_and_normalized():
    tmp_path = _scratch_dir("series")
    try:
        start = datetime(2014, 1, 1, tzinfo=timezone.utc)
        end = datetime(2014, 2, 1, tzinfo=timezone.utc)
        as_of = datetime(2014, 2, 1, 12, 0, tzinfo=timezone.utc)
        t0 = int(start.timestamp())
        t1 = int((start + timedelta(days=1)).timestamp())

        def _copy(_terminal: str, _tf: str, _s: datetime, _e: datetime) -> list[dict]:
            return [_bar(t0, close=1.0, spread=5), _bar(t1, close=1.1, spread=6)]

        result = freeze_one_series(
            canonical_symbol="XAU/USD",
            timeframe="D1",
            requested_start=start,
            requested_end=end,
            chunk_days=30,
            copy_rates=_copy,
            symbol_metadata={"point": 0.01, "digits": 2},
            broker={"terminal_company": "Pepperstone Group", "account_server": "Pepperstone-Demo"},
            raw_root=tmp_path / "raw",
            normalized_root=tmp_path / "norm",
            resume=False,
            as_of=as_of,
        )
        assert result.terminal_symbol == "XAUUSD"
        assert result.bar_count == 2
        manifest = json.loads((tmp_path / "norm" / "XAU_USD" / "D1.manifest.json").read_text(encoding="utf-8"))
        assert manifest["terminal_symbol"] == "XAUUSD"
        assert manifest["broker"]["server"] == "Pepperstone-Demo"
        assert manifest["broker"]["company"] == "Pepperstone Group"
        assert manifest["spread_audit"]["status"] == SpreadFieldStatus.OBSERVED_USABLE.value
    finally:
        _cleanup(tmp_path)


def test_cli_exits_nonzero_on_incomplete_acquisition(monkeypatch):
    import tools.freeze_commodity_phase1 as cli

    class _FakeResult:
        ok = False
        bar_count = 0
        manifest_path = "x"
        issues = ("insufficient_history",)

    monkeypatch.setattr(cli, "connect_mt5_readonly", lambda **_: object())
    monkeypatch.setattr(cli, "shutdown_mt5", lambda _mt5: None)
    monkeypatch.setattr(cli, "broker_identity", lambda _mt5: {"account_server": "demo"})
    monkeypatch.setattr(cli, "fetch_symbol_metadata", lambda _mt5, _sym: {"point": 0.01})
    monkeypatch.setattr(cli, "default_copy_rates", lambda _mt5: (lambda *a, **k: []))
    monkeypatch.setattr(cli, "freeze_one_series", lambda **_: _FakeResult())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "freeze_commodity_phase1.py",
            "--symbols",
            "XAU/USD",
            "--timeframes",
            "D1",
        ],
    )

    assert cli.main() == cli.EXIT_INCOMPLETE


def test_cli_exits_nonzero_when_mt5_unavailable(monkeypatch):
    import tools.freeze_commodity_phase1 as cli
    from athena_research.commodity_data_audit.mt5_reader import Mt5ReadError

    monkeypatch.setattr(
        cli,
        "connect_mt5_readonly",
        lambda **_kwargs: (_ for _ in ()).throw(Mt5ReadError("MT5 initialize failed")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["freeze_commodity_phase1.py", "--symbols", "XAU/USD", "--timeframes", "D1"],
    )
    assert cli.main() == cli.EXIT_MT5_UNAVAILABLE


def test_cli_respects_symbol_timeframe_and_date_arguments(monkeypatch):
    import tools.freeze_commodity_phase1 as cli

    captured: list[dict] = []

    class _FakeResult:
        ok = True
        bar_count = 10
        manifest_path = "manifest"
        issues = tuple()

    def _freeze_one_series(**kwargs):
        captured.append(kwargs)
        return _FakeResult()

    monkeypatch.setattr(cli, "connect_mt5_readonly", lambda **_: object())
    monkeypatch.setattr(cli, "shutdown_mt5", lambda _mt5: None)
    monkeypatch.setattr(cli, "broker_identity", lambda _mt5: {})
    monkeypatch.setattr(cli, "fetch_symbol_metadata", lambda _mt5, _sym: {"point": 0.01})
    monkeypatch.setattr(cli, "default_copy_rates", lambda _mt5: (lambda *a, **k: []))
    monkeypatch.setattr(cli, "freeze_one_series", _freeze_one_series)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "freeze_commodity_phase1.py",
            "--symbols",
            "WTI Oil,Brent Oil",
            "--timeframes",
            "H4,D1",
            "--start-date",
            "2014-01-01",
            "--end-date",
            "2015-01-01",
        ],
    )

    assert cli.main() == cli.EXIT_OK
    assert captured[0]["canonical_symbol"] == "WTI Oil"
    assert captured[0]["timeframe"] == "H4"
    assert captured[0]["requested_start"] == datetime(2014, 1, 1, tzinfo=timezone.utc)
    assert captured[0]["requested_end"] == datetime(2015, 1, 2, tzinfo=timezone.utc)
    assert len(captured) == 4


def test_persisted_raw_and_manifests_exclude_sensitive_mt5_metadata():
    tmp_path = _scratch_dir("metadata_policy")
    try:
        start = datetime(2014, 1, 1, tzinfo=timezone.utc)
        end = datetime(2014, 2, 1, tzinfo=timezone.utc)
        as_of = datetime(2014, 2, 1, 12, 0, tzinfo=timezone.utc)
        t0 = int(start.timestamp())

        def _copy(_terminal: str, _tf: str, _s: datetime, _e: datetime) -> list[dict]:
            return [_bar(t0, close=1.0, spread=5)]

        raw_root = tmp_path / "raw"
        normalized_root = tmp_path / "norm"
        freeze_one_series(
            canonical_symbol="XAU/USD",
            timeframe="D1",
            requested_start=start,
            requested_end=end,
            chunk_days=30,
            copy_rates=_copy,
            symbol_metadata={
                "point": 0.01,
                "digits": 2,
                "terminal_path": "C:\\MetaTrader\\terminal64.exe",
                "login": 12345678,
                "data_path": "C:\\Users\\secret\\AppData",
                "commondata_path": "C:\\Users\\secret\\Common",
                "balance": 10000.0,
                "currency_margin": "USD",
            },
            broker={
                "terminal_company": "Pepperstone Group Limited",
                "account_server": "Pepperstone-MT5-Live",
                "terminal_build": 5120,
                "terminal_name": "MetaTrader 5",
                "terminal_path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe",
                "account_login": 987654321,
                "account_name": "Account Holder",
                "balance": 50000.0,
                "equity": 48000.0,
                "margin": 1200.0,
                "account_currency": "USD",
                "MT5_PATH": "C:\\secret\\terminal64.exe",
                "password": "must-not-persist",
            },
            raw_root=raw_root,
            normalized_root=normalized_root,
            resume=False,
            as_of=as_of,
        )

        json_files = sorted(raw_root.rglob("*.json")) + sorted(normalized_root.rglob("*.json"))
        assert json_files, "expected persisted raw/manifest JSON artifacts"

        violations: list[str] = []
        for path in json_files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            violations.extend(
                f"{path.relative_to(tmp_path)}:{key}" for key in find_forbidden_persistence_keys(payload)
            )
            if path.name.endswith(".manifest.json"):
                continue
            if isinstance(payload, list):
                for bar in payload:
                    if isinstance(bar, dict):
                        assert set(bar).issubset(ALLOWED_RAW_BAR_KEYS)

        assert violations == []

        manifest = json.loads(
            (normalized_root / "XAU_USD" / "D1.manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["broker"] == {
            "company": "Pepperstone Group Limited",
            "server": "Pepperstone-MT5-Live",
            "terminal_build": 5120,
            "terminal_name": "MetaTrader 5",
        }
        assert manifest["symbol_metadata"]["currency_margin"] == "USD"
        assert "acquired_at" in manifest
        assert "terminal_path" not in json.dumps(manifest)
        assert "account_login" not in json.dumps(manifest)
    finally:
        _cleanup(tmp_path)


def test_freeze_one_series_propagates_mt5_read_error():
    tmp_path = _scratch_dir("mt5_read_error")
    start = datetime(2014, 1, 1, tzinfo=timezone.utc)
    end = datetime(2014, 2, 1, tzinfo=timezone.utc)

    def _fail(*_args, **_kwargs):
        raise Mt5ReadError("copy_rates_range partial failure")

    try:
        with pytest.raises(Mt5ReadError):
            freeze_one_series(
                canonical_symbol="XAU/USD",
                timeframe="D1",
                requested_start=start,
                requested_end=end,
                chunk_days=30,
                copy_rates=_fail,
                symbol_metadata={"point": 0.01},
                broker={},
                raw_root=tmp_path / "raw",
                normalized_root=tmp_path / "norm",
                resume=False,
            )
    finally:
        _cleanup(tmp_path)


def test_freeze_tool_does_not_import_execution_modules():
    import tools.freeze_commodity_phase1 as cli

    forbidden = ("config", "athena", "execution", "mt5_executor", "auto_trader", "bybit_executor")
    loaded = [name for name in forbidden if name in sys.modules]
    assert loaded == [], f"production modules loaded: {loaded}"
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "mt5_executor" not in source
    assert "order_send" not in source
    assert "import config" not in source
    assert "ATHENA_REAL_ORDERS_CONFIRM" not in source


def test_freeze_cli_subprocess_avoids_production_config_with_unsafe_env():
    import subprocess

    repo = Path(__file__).resolve().parents[1]
    scratch = _scratch_dir("subprocess_isolation")
    try:
        unsafe_config = scratch / "unsafe_config.yaml"
        unsafe_config.write_text(
            "EXECUTOR_MODE: live\nREAL_ORDERS_ENABLED: true\n",
            encoding="utf-8",
        )
        probe = scratch / "probe_imports.py"
        probe.write_text(
            "\n".join(
                [
                    "import importlib.abc",
                    "import os",
                    "import sys",
                    f"ROOT = {str(repo)!r}",
                    "if ROOT not in sys.path:",
                    "    sys.path.insert(0, ROOT)",
                    "FORBIDDEN_ROOTS = frozenset({",
                    '    "config",',
                    '    "athena",',
                    '    "execution",',
                    '    "mt5_executor",',
                    '    "auto_trader",',
                    '    "bybit_executor",',
                    '    "risk_engine",',
                    '    "guardian",',
                    "})",
                    "class _BlockProductionImports(importlib.abc.MetaPathFinder):",
                    "    def find_spec(self, fullname, path, target=None):",
                    "        root = fullname.partition('.')[0]",
                    "        if root in FORBIDDEN_ROOTS:",
                    "            raise ImportError(f'blocked production import: {fullname}')",
                    "        return None",
                    "sys.meta_path.insert(0, _BlockProductionImports())",
                    f"os.environ['ATHENA_CONFIG_PATH'] = {str(unsafe_config)!r}",
                    "os.environ['ATHENA_REAL_ORDERS_CONFIRM'] = 'I_UNDERSTAND_REAL_ORDER_RISK'",
                    "os.environ['EXECUTOR_MODE'] = 'live'",
                    "import tools.freeze_commodity_phase1 as cli",
                    "assert 'config' not in sys.modules",
                    "assert 'mt5_executor' not in sys.modules",
                    "assert 'execution' not in sys.modules",
                    "sys.argv = [",
                    "    'freeze_commodity_phase1.py',",
                    "    '--symbols',",
                    "    'NOT_A_REAL_SYMBOL',",
                    "    '--timeframes',",
                    "    'D1',",
                    "]",
                    "code = cli.main()",
                    "assert code == cli.EXIT_INCOMPLETE",
                    "print('IMPORT_PROBE_OK')",
                ]
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(probe)],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "IMPORT_PROBE_OK" in proc.stdout
    finally:
        _cleanup(scratch)
