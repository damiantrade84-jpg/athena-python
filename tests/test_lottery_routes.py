from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from flask import Flask

from athena_app.api.routes_lottery import register_lottery_routes
from lottery_service import ensure_lottery_schema


ARTIFACT_ROOT = Path(__file__).resolve().parent / "_artifacts" / "route_lottery"


def _db_path() -> Path:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    return ARTIFACT_ROOT / f"{uuid4().hex}.db"


def _seed_lotto(path: Path) -> None:
    seeded_rows = [
        ("2026-01-01", 1, 2, 3, 10, 20, 30, 5),
        ("2026-01-08", 1, 2, 4, 11, 21, 31, 6),
        ("2026-01-15", 1, 2, 5, 12, 22, 32, 7),
        ("2026-01-22", 1, 2, 6, 13, 23, 33, 8),
        ("2026-01-29", 1, 2, 7, 14, 24, 34, 9),
        ("2026-02-05", 1, 3, 4, 15, 25, 35, 10),
        ("2026-02-12", 1, 3, 5, 16, 26, 36, 11),
        ("2026-02-19", 1, 3, 6, 17, 27, 37, 12),
        ("2026-02-26", 1, 3, 7, 18, 28, 38, 13),
        ("2026-03-05", 1, 2, 3, 19, 29, 39, 14),
    ]
    with sqlite3.connect(path, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        ensure_lottery_schema(con)
        con.executemany(
            """
            INSERT INTO lottery_draws
                (game, draw_date, n1, n2, n3, n4, n5, n6, bonus, source_file, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("lotto", draw_date, n1, n2, n3, n4, n5, n6, bonus, "seed.csv", f"{draw_date}T00:00:00")
                for draw_date, n1, n2, n3, n4, n5, n6, bonus in seeded_rows
            ],
        )
        con.commit()


def _client(db_path: Path):
    app = Flask(__name__)
    register_lottery_routes(
        app,
        SimpleNamespace(
            CONFIG={"AI_API_KEY": "", "XAI_API_KEY": "", "OPENAI_API_KEY": ""},
            AUDIT_DB=str(db_path),
            log=logging.getLogger(__name__),
        ),
    )
    return app.test_client(), app


def test_lottery_routes_register_react_consumed_methods():
    client, app = _client(_db_path())
    methods_by_path = {rule.rule: rule.methods for rule in app.url_map.iter_rules()}

    for path in [
        "/api/lottery/dashboard",
        "/api/lottery/frequency",
        "/api/lottery/draws",
        "/api/lottery/pairs",
        "/api/lottery/triplets",
        "/api/lottery/distributions",
        "/api/lottery/sum-range",
        "/api/lottery/positional",
        "/api/lottery/rolling-frequency",
        "/api/lottery/pair-lift",
        "/api/lottery/anomalous-draws",
        "/api/lottery/bonus-intelligence",
    ]:
        assert "GET" in methods_by_path[path]

    for path in [
        "/api/lottery/import",
        "/api/lottery/add-draw",
        "/api/lottery/generate",
        "/api/lottery/wheel",
        "/api/lottery/score-ticket",
        "/api/lottery/simulate",
        "/api/lottery/compare-modes",
        "/api/lottery/ai-analysis",
    ]:
        assert "POST" in methods_by_path[path]
    assert client is not None


def test_lottery_read_routes_return_react_shapes():
    db_path = _db_path()
    _seed_lotto(db_path)
    client, _app = _client(db_path)
    base = "?game=lotto&include_bonus=true&limit=20"

    dashboard = client.get(f"/api/lottery/dashboard{base}").get_json()
    assert dashboard["total_draws"] == 10
    assert dashboard["rules"]["main_count"] == 6
    assert dashboard["hot_numbers"]
    assert dashboard["recommended_sum_range"]["min"] < dashboard["recommended_sum_range"]["max"]

    frequency = client.get(f"/api/lottery/frequency{base}").get_json()
    assert len(frequency["number_frequency"]) == 58
    assert frequency["overdue"]
    assert frequency["bonus_frequency"]

    assert len(client.get(f"/api/lottery/draws{base}").get_json()["history"]) == 10
    assert client.get(f"/api/lottery/pairs{base}").get_json()["pairs"]
    assert client.get(f"/api/lottery/triplets{base}").get_json()["triplets"]

    distributions = client.get(f"/api/lottery/distributions{base}").get_json()
    assert distributions["sum_distribution"]["summary"]["min_sum"] is not None
    assert distributions["odd_even_distribution"]["patterns"]
    assert distributions["low_high_distribution"]["patterns"]

    rolling = client.get(f"/api/lottery/rolling-frequency{base}&window=5").get_json()
    assert rolling["window_used"] == 5
    assert len(rolling["numbers"]) == 58

    pair_lift = client.get(f"/api/lottery/pair-lift{base}").get_json()
    assert pair_lift["pairs"]

    anomalies = client.get(f"/api/lottery/anomalous-draws{base}&z_threshold=1.5").get_json()
    assert "anomalous_draws" in anomalies

    bonus = client.get(f"/api/lottery/bonus-intelligence{base}&window=5").get_json()
    assert bonus["has_bonus"] is True
    assert bonus["rolling"]


def test_lottery_tool_routes_return_react_shapes():
    db_path = _db_path()
    _seed_lotto(db_path)
    client, _app = _client(db_path)

    generated = client.post(
        "/api/lottery/generate",
        json={
            "game": "lotto",
            "mode": "balanced_mix",
            "ticket_count": 2,
            "include_bonus": True,
            "filters": {"max_consecutive_run": 4},
        },
    )
    assert generated.status_code == 200
    generated_json = generated.get_json()
    assert generated_json["ticket_count"] == 2
    assert len(generated_json["tickets"]) == 2

    scored = client.post(
        "/api/lottery/score-ticket",
        json={
            "game": "lotto",
            "ticket": {"numbers": [1, 2, 3, 10, 20, 30], "bonus": 5},
            "include_bonus": True,
        },
    )
    assert scored.status_code == 200
    assert "labels" in scored.get_json()

    wheel = client.post(
        "/api/lottery/wheel",
        json={"game": "lotto", "chosen_numbers": [1, 2, 3, 4, 5, 6, 7, 8], "guarantee_if": 3},
    )
    assert wheel.status_code == 200
    assert wheel.get_json()["coverage_verified"] is True

    simulation = client.post(
        "/api/lottery/simulate",
        json={
            "game": "lotto",
            "mode": "pure_random",
            "tickets_per_draw": 2,
            "include_bonus": True,
            "ticket_cost": 5,
            "payout_table": {"3": 10, "4": 75, "5": 500, "6": 20000},
            "seed": 7,
        },
    )
    assert simulation.status_code == 200
    simulation_json = simulation.get_json()
    assert simulation_json["draws_simulated"] == 10
    assert simulation_json["tickets_generated"] == 18

    comparison = client.post(
        "/api/lottery/compare-modes",
        json={
            "game": "lotto",
            "modes": ["pure_random", "hot_bias"],
            "tickets_per_draw": 1,
            "include_bonus": True,
            "seed": 7,
        },
    )
    assert comparison.status_code == 200
    assert [row["mode"] for row in comparison.get_json()["modes"]] == ["pure_random", "hot_bias"]


def test_lottery_import_and_add_draw_routes_return_react_shapes():
    db_path = _db_path()
    client, _app = _client(db_path)

    imported = client.post(
        "/api/lottery/import",
        json={
            "game": "daily_lotto",
            "mode": "append",
            "csv_text": "draw_date,n1,n2,n3,n4,n5\n2026-01-01,1,2,3,4,5\n",
        },
    )
    assert imported.status_code == 200
    assert imported.get_json()["imported_rows"] == 1

    added = client.post(
        "/api/lottery/add-draw",
        json={"game": "daily_lotto", "draw_date": "2026-01-02", "numbers": [6, 7, 8, 9, 10]},
    )
    assert added.status_code == 200
    added_json = added.get_json()
    assert added_json["inserted"] is True
    assert added_json["numbers"] == [6, 7, 8, 9, 10]


def _lottery_ai_test_runtime(**overrides):
    runtime = {
        "provider": "grok",
        "api_key": "test-key",
        "fallback_used": False,
        "model": "grok-4.3",
        "max_tokens": 4000,
        "reasoning_effort": "low",
        "timeout_sec": 30.0,
        "provider_label": "xAI",
    }
    runtime.update(overrides)
    return runtime


def _patch_lottery_ai_client(monkeypatch, fake_client, runtime=None):
    monkeypatch.setattr(
        "athena_app.api.routes_lottery._lottery_ai_runtime",
        lambda _config: _lottery_ai_test_runtime(**(runtime or {})),
    )
    create_calls = []

    def _create_ai_client(_config, api_key=None, provider=None, **_kwargs):
        create_calls.append({"api_key": api_key, "provider": provider})
        return fake_client

    monkeypatch.setattr(
        "athena_app.api.routes_lottery.create_ai_client",
        _create_ai_client,
    )
    return create_calls


def test_lottery_ai_route_fails_closed_without_configured_key(monkeypatch):
    db_path = _db_path()
    client, _app = _client(db_path)
    monkeypatch.setattr(
        "athena_app.api.routes_lottery._lottery_ai_runtime",
        lambda _config: _lottery_ai_test_runtime(api_key=""),
    )

    resp = client.post("/api/lottery/ai-analysis", json={"game": "lotto"})

    assert resp.status_code == 500
    assert resp.get_json()["error"] == "AI API key not configured"


def test_lottery_ai_route_rejects_empty_model_response(monkeypatch):
    db_path = _db_path()
    _seed_lotto(db_path)
    client, _app = _client(db_path)

    class _FakeCompletions:
        @staticmethod
        def create(**_kwargs):
            message = SimpleNamespace(content="")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions())
    )
    _patch_lottery_ai_client(monkeypatch, fake_client)

    resp = client.post("/api/lottery/ai-analysis", json={"game": "lotto"})

    assert resp.status_code == 400
    body = resp.get_json()
    assert "empty response" in body["error"]
    assert body["provider"] == "grok"
    assert body["model"] == "grok-4.3"


def test_lottery_ai_route_rejects_schema_empty_model_response(monkeypatch):
    db_path = _db_path()
    _seed_lotto(db_path)
    client, _app = _client(db_path)

    class _FakeCompletions:
        @staticmethod
        def create(**_kwargs):
            message = SimpleNamespace(content="{}")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions())
    )
    _patch_lottery_ai_client(monkeypatch, fake_client)

    resp = client.post("/api/lottery/ai-analysis", json={"game": "lotto"})

    assert resp.status_code == 400
    body = resp.get_json()
    assert "required field" in body["error"]
    assert "recommended_pool" in body["error"]
    assert body["provider"] == "grok"


def test_lottery_ai_route_requests_json_schema_response(monkeypatch):
    db_path = _db_path()
    _seed_lotto(db_path)
    client, _app = _client(db_path)
    captured = {}

    class _FakeCompletions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(
                content=(
                    '{"recommended_pool":[1,2,3,4,5,6,7,8,9,10],'
                    '"avoid_numbers":[11,12,13],"generator_mode":"balanced_mix",'
                    '"bonus_picks":[1,2],"sum_filter":{"min":90,"max":180},'
                    '"entropy_assessment":{"fairness":"normal","interpretation":"ok","impact_on_selection":"ok"},'
                    '"confidence":"Low","reasoning":{"pool_reasoning":"a","avoid_reasoning":"b",'
                    '"mode_reasoning":"c","bonus_reasoning":"d","entropy_reasoning":"e","confidence_reasoning":"f"}}'
                )
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions())
    )
    _patch_lottery_ai_client(monkeypatch, fake_client)

    resp = client.post("/api/lottery/ai-analysis", json={"game": "lotto"})

    assert resp.status_code == 200
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["name"] == "lottery_ai_analysis"
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert captured["max_tokens"] == 4000
    assert captured["timeout"] == 30.0


def test_lottery_ai_route_uses_grok_provider_by_default(monkeypatch):
    db_path = _db_path()
    _seed_lotto(db_path)
    client, _app = _client(db_path)

    class _FakeCompletions:
        @staticmethod
        def create(**_kwargs):
            message = SimpleNamespace(content="{}")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions())
    )
    create_calls = _patch_lottery_ai_client(monkeypatch, fake_client)

    client.post("/api/lottery/ai-analysis", json={"game": "lotto"})

    assert create_calls == [{"api_key": "test-key", "provider": "grok"}]


def test_lottery_ai_route_openai_uses_lottery_token_and_reasoning_settings(monkeypatch):
    db_path = _db_path()
    _seed_lotto(db_path)
    client, _app = _client(db_path)
    captured = {}

    class _FakeCompletions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(
                content=(
                    '{"recommended_pool":[1,2,3,4,5,6,7,8,9,10],'
                    '"avoid_numbers":[11,12,13],"generator_mode":"balanced_mix",'
                    '"bonus_picks":[1,2],"sum_filter":{"min":90,"max":180},'
                    '"entropy_assessment":{"fairness":"normal","interpretation":"ok","impact_on_selection":"ok"},'
                    '"confidence":"Low","reasoning":{"pool_reasoning":"a","avoid_reasoning":"b",'
                    '"mode_reasoning":"c","bonus_reasoning":"d","entropy_reasoning":"e","confidence_reasoning":"f"}}'
                )
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions())
    )
    _patch_lottery_ai_client(
        monkeypatch,
        fake_client,
        runtime={
            "provider": "openai",
            "provider_label": "OpenAI",
            "model": "gpt-5.5",
            "max_tokens": 4000,
            "reasoning_effort": "low",
        },
    )

    resp = client.post("/api/lottery/ai-analysis", json={"game": "lotto"})

    assert resp.status_code == 200
    assert captured["max_tokens"] == 4000
    assert captured["reasoning"] == {"effort": "low"}
    assert captured["response_format"]["type"] == "json_schema"
