from __future__ import annotations

import threading
from types import SimpleNamespace


class _Log:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass


def test_ase_post_scan_hook_is_scheduled_without_blocking_response():
    from athena_app.services.scan_completion_hooks import schedule_ase_post_scan_hook

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_ase_runner():
        started.set()
        release.wait(timeout=2.0)
        finished.set()
        return {"success": True, "signalCount": 1}

    try:
        status = schedule_ase_post_scan_hook(runner=slow_ase_runner, logger=_Log())

        assert status["status"] == "scheduled"
        assert started.wait(timeout=1.0)
        assert not finished.is_set()
    finally:
        release.set()

    assert finished.wait(timeout=1.0)


def test_engine_b_funnel_persist_hook_is_scheduled_without_blocking_response():
    from athena_app.services.scan_completion_hooks import (
        schedule_engine_b_funnel_persist_hook,
    )

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_persist_runner(_scan_payload: dict, *, pair_types_by_display: dict[str, str]):
        assert pair_types_by_display == {"EUR/USD": "forex"}
        started.set()
        release.wait(timeout=2.0)
        finished.set()
        return {
            "engine_b_scan_gate_funnel_saved": True,
            "engine_b_scan_gate_funnel_summary_path": "logs/engine_b_gate_funnel/latest_funnel_summary.json",
        }

    try:
        status = schedule_engine_b_funnel_persist_hook(
            {"success": True, "signals": [{"pair": "EUR/USD"}]},
            pair_types_by_display={"EUR/USD": "forex"},
            runner=slow_persist_runner,
            logger=_Log(),
        )

        assert status["status"] == "scheduled"
        assert started.wait(timeout=1.0)
        assert not finished.is_set()
    finally:
        release.set()

    assert finished.wait(timeout=1.0)


def test_scan_result_is_published_before_slow_conductor_enrichment_finishes():
    from athena_app.services.scan_completion_hooks import (
        publish_scan_result_and_schedule_conductor,
    )

    started = threading.Event()
    release = threading.Event()
    enriched_published = threading.Event()
    published: list[tuple[str, dict]] = []
    scan_result = {
        "success": True,
        "scannedAt": "2026-06-25T10:00:00+00:00",
        "signals": [{"display": "EUR/USD", "regime": "TRENDING"}],
    }

    def publish_initial(result: dict):
        published.append(("initial", result))

    def publish_enriched(enriched: dict, _base: dict):
        published.append(("enriched", enriched))
        enriched_published.set()

    def reset_scan_results(_scan_type: str):
        pass

    def extract_conductor_microstructure(_signal: dict):
        return None, None

    def conductor_orchestrate(*_args, **_kwargs):
        started.set()
        release.wait(timeout=2.0)
        return {"routing": {"run_marcus": True}, "context": {"source": "test"}}

    conductor_module = SimpleNamespace(
        reset_scan_results=reset_scan_results,
        extract_conductor_microstructure=extract_conductor_microstructure,
        conductor_orchestrate=conductor_orchestrate,
    )

    try:
        status = publish_scan_result_and_schedule_conductor(
            scan_result,
            publish_initial=publish_initial,
            publish_enriched=publish_enriched,
            audit_db=":memory:",
            conductor_loader=lambda: conductor_module,
            logger=_Log(),
        )

        assert status["status"] == "scheduled"
        assert published == [("initial", scan_result)]
        assert started.wait(timeout=1.0)
        assert published == [("initial", scan_result)]
    finally:
        release.set()

    assert enriched_published.wait(timeout=1.0)
    assert len(published) == 2
    assert published[1][0] == "enriched"
    assert published[1][1]["conductor"] == {"run_marcus": True}
    assert published[1][1]["conductor_context"] == {"source": "test"}


def test_conductor_enrichment_jobs_queue_instead_of_dropping_newer_scan():
    from athena_app.services.scan_completion_hooks import (
        publish_scan_result_and_schedule_conductor,
    )

    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_published = threading.Event()
    enriched_displays: list[str] = []

    def publish_initial(_result: dict):
        pass

    def publish_enriched(enriched: dict, _base: dict):
        display = enriched["signals"][0]["display"]
        enriched_displays.append(display)
        if display == "SECOND":
            second_published.set()

    def reset_scan_results(_scan_type: str):
        pass

    def extract_conductor_microstructure(_signal: dict):
        return None, None

    def conductor_orchestrate(signal: dict, *_args, **_kwargs):
        display = signal["display"]
        if display == "FIRST":
            first_started.set()
            release_first.wait(timeout=2.0)
        if display == "SECOND":
            second_started.set()
        return {"routing": {"display": display}, "context": {"display": display}}

    conductor_module = SimpleNamespace(
        reset_scan_results=reset_scan_results,
        extract_conductor_microstructure=extract_conductor_microstructure,
        conductor_orchestrate=conductor_orchestrate,
    )

    try:
        first_status = publish_scan_result_and_schedule_conductor(
            {
                "success": True,
                "scannedAt": "2026-06-25T10:00:00+00:00",
                "signals": [{"display": "FIRST", "regime": "TRENDING"}],
            },
            publish_initial=publish_initial,
            publish_enriched=publish_enriched,
            audit_db=":memory:",
            conductor_loader=lambda: conductor_module,
            logger=_Log(),
        )
        assert first_status["status"] == "scheduled"
        assert first_started.wait(timeout=1.0)

        second_status = publish_scan_result_and_schedule_conductor(
            {
                "success": True,
                "scannedAt": "2026-06-25T10:01:00+00:00",
                "signals": [{"display": "SECOND", "regime": "TRENDING"}],
            },
            publish_initial=publish_initial,
            publish_enriched=publish_enriched,
            audit_db=":memory:",
            conductor_loader=lambda: conductor_module,
            logger=_Log(),
        )
        assert second_status["status"] == "scheduled"
        assert not second_started.is_set()
    finally:
        release_first.set()

    assert second_started.wait(timeout=1.0)
    assert second_published.wait(timeout=1.0)
    assert enriched_displays[-1] == "SECOND"
