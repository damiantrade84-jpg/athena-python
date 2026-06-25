"""Diagnostics persistence for native engine_b_scan_gate_funnel."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from config import CONFIG
from athena_app.diagnostics.engine_b_gate_funnel_persist import (
    flatten_scan_to_funnel_rows,
    maybe_persist_engine_b_scan_gate_funnel,
    regenerate_funnel_artifacts_from_scan_file,
    save_engine_b_scan_gate_funnel,
    scheduled_engine_b_scan_gate_funnel_meta,
    summarize_funnel_rows,
)


def test_funnel_default_output_dir_anchors_to_repo_not_cwd(monkeypatch):
    import athena_app.diagnostics.engine_b_gate_funnel_persist as m

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_B_SCAN_GATE_FUNNEL_OUTPUT_DIR",
        "logs/engine_b_gate_funnel",
    )
    expected = (m._repository_root() / "logs" / "engine_b_gate_funnel").resolve()
    monkeypatch.chdir(tempfile.gettempdir())
    assert m.funnel_persist_output_dir() == expected


def test_scan_touch_always_writes_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setitem(CONFIG, "ENGINE_B_SCAN_GATE_FUNNEL_OUTPUT_DIR", str(Path(td) / "eb_sub"))
        from athena_app.diagnostics.engine_b_gate_funnel_persist import (
            write_engine_b_funnel_scan_touch_file,
        )

        out = write_engine_b_funnel_scan_touch_file({"scannedAt": "x", "success": True})
        assert out["wrote_touch_file"] is True
        assert out["touch_path"]
        tp = Path(str(out["touch_path"]))
        assert tp.name == "scan_funnel_touch.json"
        assert tp.exists()


def test_flatten_empty_or_none_safe():
    assert flatten_scan_to_funnel_rows({}) == []
    assert flatten_scan_to_funnel_rows(None) == []


def test_flatten_trade_without_funnel_minimal_row():
    scan = {
        "signals": [{"pair": "ALPHA", "type": "crypto"}],
        "watchlist": [],
        "skipped": [],
    }
    rows = flatten_scan_to_funnel_rows(scan)
    assert len(rows) == 1
    assert rows[0]["has_funnel"] is False
    assert rows[0]["reason"] == "no_engine_b_scan_gate_funnel"


def test_summary_crypto_forex_sections_and_counters():
    scan = {
        "tradeSignals": [
            {
                "pair": "BTCUSDT",
                "type": "crypto",
                "engine_b_scan_gate_funnel": {
                    "symbol": "BTCUSDT",
                    "asset_type": "crypto",
                    "sig_a_present": True,
                    "engine_b_evaluated": True,
                    "structure_executed": True,
                    "structure_verdict": "CLEAR",
                    "engine_b_error": "bybit_atr_unavailable",
                    "rr_ok": True,
                    "space_gate_ok": True,
                    "failed_gate_names": [],
                    "execution_level_reject_reason": None,
                    "final_tier": "trade",
                },
            },
        ],
        "watchlist": [
            {
                "pair": "ETHUSDT",
                "type": "crypto",
                "engine_b_scan_gate_funnel": {
                    "symbol": "ETHUSDT",
                    "asset_type": "crypto",
                    "sig_a_present": True,
                    "engine_b_evaluated": True,
                    "structure_executed": True,
                    "structure_verdict": "CLEAR",
                    "execution_level_reject_reason": "structural_tp_below_min_rr",
                    "rr_ok": False,
                    "space_gate_ok": False,
                    "failed_gate_names": ["space"],
                    "engine_b_error": None,
                    "final_tier": "watchlist",
                    "engine_b_confidence_passed": False,
                },
            }
        ],
        "skipped": [
            {
                "pair": "NODATA",
                "reason": "No data",
                "tier": "skip",
                "diagnostics": [{"code": "no_data"}],
            }
        ],
    }
    rows = flatten_scan_to_funnel_rows(
        scan, pair_types_by_display={"NODATA": "crypto"}
    )
    assert len(rows) == 3
    summ = summarize_funnel_rows(rows)
    assert "crypto_section" in summ and "forex_section" in summ
    crypto = summ["crypto_section"]
    assert crypto["A_crypto_skipped_sig_a_missing"] >= 1
    assert crypto["D_bybit_atr_unavailable_or_skip"] >= 1
    assert crypto["E_rr_failed"] >= 1
    assert crypto["F_space_gate_failed"] >= 1
    assert crypto["G_watchlist_only_rows"] >= 1
    assert crypto["H_trade_rows"] >= 1
    assert crypto["C_structure_executed_true"] >= 1
    by_crypto = summ["by_asset_type"].get("crypto", {})
    assert by_crypto.get("structural_tp_below_min_rr", 0) >= 1
    fb = summ["top_blockers"]["failed_gate_names"]
    assert isinstance(fb, list)
    err = summ["top_blockers"]["engine_b_error"]
    assert isinstance(err, list)
    assert any("space" == x[0] for x in fb)
    assert any("bybit_atr_unavailable" in str(x[0]) for x in err)


def test_save_writes_artifacts():
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        scan = {"tradeSignals": [], "watchlist": [], "skipped": []}
        meta = save_engine_b_scan_gate_funnel(scan, output_dir=str(tmp_path))
        assert meta["engine_b_scan_gate_funnel_saved"] is True
        assert (tmp_path / "latest_full_scan.json").exists()
        stamped = sorted(tmp_path.glob("full_scan_*.json"))
        assert len(stamped) == 1
        assert (tmp_path / "latest_funnel_rows.jsonl").exists()
        assert (tmp_path / "latest_funnel_summary.json").exists()


def test_save_write_failure_non_fatal(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)

        def boom(self, *_a, **_k):  # type: ignore[no-untyped-def]
            raise OSError("boom")

        monkeypatch.setattr(Path, "write_text", boom)
        meta = save_engine_b_scan_gate_funnel(
            {"skipped": [{"pair": "X", "reason": "No data"}], "signals": [], "watchlist": []},
            output_dir=str(tmp_path),
            pair_types_by_display={"X": "crypto"},
        )
        assert meta["engine_b_scan_gate_funnel_saved"] is False
        assert meta.get("engine_b_scan_gate_funnel_saved_error")


def test_maybe_persist_when_funnel_disabled(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_B_SCAN_GATE_FUNNEL_ENABLED", False)
    out = maybe_persist_engine_b_scan_gate_funnel({"signals": []}, pair_types_by_display=None)
    assert out.get("engine_b_scan_gate_funnel_persist_skipped") == "ENGINE_B_SCAN_GATE_FUNNEL_DISABLED"


def test_scheduled_meta_exposes_summary_path_without_claiming_saved(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setitem(CONFIG, "ENGINE_B_SCAN_GATE_FUNNEL_ENABLED", True)
        monkeypatch.setitem(CONFIG, "ENGINE_B_SCAN_GATE_FUNNEL_PERSIST_ENABLED", True)
        monkeypatch.setitem(CONFIG, "ENGINE_B_SCAN_GATE_FUNNEL_OUTPUT_DIR", str(Path(td)))

        out = scheduled_engine_b_scan_gate_funnel_meta()

        assert out["engine_b_scan_gate_funnel_saved"] is False
        assert out["engine_b_scan_gate_funnel_persist_status"] == "scheduled"
        assert out["engine_b_scan_gate_funnel_summary_path"]
        assert out["engine_b_scan_gate_funnel_output_dir"] == str(Path(td)).replace("\\", "/")


def test_regenerate_from_file_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        scan_text = '{"tradeSignals":[{"pair":"P","type":"forex","engine_b_scan_gate_funnel":{"symbol":"P","asset_type":"forex","sig_a_present":true,"final_tier":"trade"}}],"watchlist":[],"skipped":[]}'
        inp = tmp_path / "in.json"
        inp.write_text(scan_text, encoding="utf-8")
        regenerate_funnel_artifacts_from_scan_file(inp, output_dir=tmp_path)
        assert (tmp_path / "latest_funnel_rows.jsonl").read_text(encoding="utf-8").strip()
        assert "forex_section" in (tmp_path / "latest_funnel_summary.json").read_text(encoding="utf-8")


def test_handle_scan_request_merges_persist_keys(monkeypatch):
    from athena_app.services.scan_backtest_service import handle_scan_request

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)

        monkeypatch.setitem(CONFIG, "ENGINE_B_SCAN_GATE_FUNNEL_ENABLED", True)
        monkeypatch.setitem(CONFIG, "ENGINE_B_SCAN_GATE_FUNNEL_PERSIST_ENABLED", True)
        monkeypatch.setitem(CONFIG, "ENGINE_B_SCAN_GATE_FUNNEL_OUTPUT_DIR", str(tmp_path))

        def _fake_run(style="auto", asset_class=None):  # type: ignore[no-untyped-def]
            from athena_app.diagnostics.engine_b_gate_funnel_persist import (
                maybe_persist_engine_b_scan_gate_funnel,
            )

            base = {
                "success": True,
                "signals": [],
                "tradeSignals": [],
                "watchlist": [],
                "errors": [],
                "skipped": [],
                "scanFunnel": {},
                "btcBias": "neutral",
                "totalPairs": 0,
                "activePairs": 0,
                "scannedAt": "",
                "styleRequested": style,
                "style": style,
                "testMode": False,
                "scanMaxWorkersUsed": 1,
                "scanQuantileEnabled": False,
                "scanQuantileFloors": {},
                "scanQuantileMinSamples": 0,
                "payloadVersion": "2.0",
                "contract": {},
            }
            base.update(
                maybe_persist_engine_b_scan_gate_funnel(
                    base,
                    pair_types_by_display={"FOO": "crypto"},
                )
            )
            return base

        out = handle_scan_request({}, run_full_scan=_fake_run)
        assert "engine_b_scan_gate_funnel_saved" in out


@pytest.fixture(autouse=True)
def cleanup_env(monkeypatch):
    monkeypatch.delenv("ENGINE_B_SCAN_GATE_FUNNEL_PERSIST", raising=False)
