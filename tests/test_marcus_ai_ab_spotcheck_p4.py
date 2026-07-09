"""Phase 4: synthetic A+B prompt/playbook spot-check (no live LLM)."""

from __future__ import annotations

import json
from pathlib import Path

from ai_playbooks import get_engine_a_playbook, get_engine_b_playbook, render_playbook_prompt_block
from ai_review.engine_a_context import build_engine_b_prompt_context
from ai_review.prompt_builder import build_chart_review_prompt
from marcus_review import build_marcus_playbook_block

ROOT = Path(__file__).resolve().parents[1]


def test_b_primary_marcus_playbook_excludes_engine_a():
    block = build_marcus_playbook_block(
        {
            "engine_source": "engine_b",
            "is_naked": True,
            "naked_data": {"score": 4, "structural_verdict": "CLEAR"},
        }
    )
    payload = json.loads(block.split("==\n", 1)[-1].strip())
    assert {p.get("engine") for p in payload} == {"B"}


def test_a_primary_without_b_structure_excludes_engine_b_playbook():
    block = build_marcus_playbook_block(
        {"engine_source": "engine_a", "pair": "EURUSD", "type": "forex"}
    )
    payload = json.loads(block.split("==\n", 1)[-1].strip())
    assert {p.get("engine") for p in payload} == {"A"}


def test_a_chart_without_b_available_excludes_b_playbook():
    ctx = {
        "primary_engine": "A",
        "symbol": "EURUSD",
        "timeframe": "H4",
        "asset_class": "forex",
        "asset_group": "forex_majors",
        "direction": "LONG",
        "passed": True,
        "confluence_score": 1.8,
        "threshold": 1.5,
        "structure_context": {},
        "engine_snapshots": {"engineA": {"score": 1.8}, "engineB": {}},
        "atr": {"atr_h4": 0.001},
        "geometry": {
            "candidate_entry": 1.1,
            "stop_loss": 1.09,
            "take_profit": 1.12,
            "rr": 2.0,
        },
        "mismatch_warnings": [],
        "chart_snapshot": {},
        "diagnostics": {},
    }
    eb = build_engine_b_prompt_context(ctx)
    assert eb["available"] is False
    prompt = build_chart_review_prompt(ctx)
    assert '"engine": "B"' not in prompt and '"engine":"B"' not in prompt


def test_b_context_spotcheck_ob_fvg_gates_present():
    ctx = {
        "structure_context": {
            "structural_verdict": "CLEAR",
            "bos_confirmed": True,
            "ob_at_zone": True,
            "fvg_overlap": True,
            "active_fvgs": [{"midpoint": 100.5}],
            "structure_ok": True,
            "location_ok": True,
            "entry_ok": True,
            "room_ok": True,
            "rr_ok": True,
            "recommended_stop_loss": 99.0,
            "recommended_take_profit": 103.0,
            "rr": 2.0,
        },
        "engine_snapshots": {
            "engineB": {"score": 5.0, "passed": True, "direction": "LONG"}
        },
        "asset_class": "forex",
    }
    eb = build_engine_b_prompt_context(ctx)
    assert eb["available"] is True
    assert eb["obAtZone"] is True
    assert eb["fvgOverlap"] is True
    assert eb["structureOk"] is True
    assert eb["executionSl"] == 99.0


def test_playbooks_and_marcus_v6_step_1b_present():
    text = render_playbook_prompt_block(
        [get_engine_a_playbook(), get_engine_b_playbook()], compact=True
    )
    assert "factorScores.trend" in text
    assert "obAtZone" in text
    marcus = (ROOT / "prompts" / "marcus_v6.md").read_text(encoding="utf-8")
    assert "Step 1B: If Engine source is Engine B naked market structure" in marcus
    athena_src = (ROOT / "athena.py").read_text(encoding="utf-8")
    assert "Engine A V3 components" in athena_src
