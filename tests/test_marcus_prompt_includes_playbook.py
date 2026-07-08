"""Marcus text review must inject trade playbooks like the chart path."""

import json

from marcus_review import build_marcus_playbook_block, resolve_marcus_engine_source


def test_engine_a_signal_includes_engine_a_playbook_keys():
    block = build_marcus_playbook_block(
        {"pair": "EURUSD", "engine_source": "engine_a", "type": "forex"}
    )
    assert "ATHENA TRADE PLAYBOOKS" in block
    payload = json.loads(block.split("==\n", 1)[-1].strip())
    assert len(payload) >= 1
    assert payload[0].get("engine") == "A"
    assert "mustRejectIf" in payload[0]
    assert "entryModels" in payload[0]


def test_engine_b_signal_includes_both_playbooks():
    block = build_marcus_playbook_block(
        {
            "pair": "BTCUSDT",
            "is_naked": True,
            "engine_source": "engine_b",
            "naked_data": {"score": 4, "structural_verdict": "CLEAR"},
        }
    )
    payload = json.loads(block.split("==\n", 1)[-1].strip())
    engines = {item.get("engine") for item in payload}
    assert "A" in engines
    assert "B" in engines
    b_pb = next(item for item in payload if item.get("engine") == "B")
    assert "mustRejectIf" in b_pb


def test_dual_row_with_naked_data_gets_engine_b_playbook():
    block = build_marcus_playbook_block(
        {
            "pair": "XAUUSD",
            "engine_source": "engine_a",
            "naked_data": {"score": 3},
        }
    )
    payload = json.loads(block.split("==\n", 1)[-1].strip())
    engines = {item.get("engine") for item in payload}
    assert engines == {"A", "B"}


def test_resolve_engine_c_source():
    sig = {"conviction": 0.8, "decision_state": "execute", "engine_c": {"tier": "HIGH"}}
    assert resolve_marcus_engine_source(sig) == "engine_c"
