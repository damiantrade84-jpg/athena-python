"""Non-blocking scan completion hooks.

Engine scan responses should publish the deterministic scan payload first.
Advisory enrichment and standalone ASE work can continue after that without
holding the UI request open.
"""

from __future__ import annotations

import copy
import logging
import threading
from typing import Any, Callable

log = logging.getLogger(__name__)

_ase_lock = threading.Lock()
_ase_running = False
_conductor_lock = threading.Lock()


def schedule_ase_post_scan_hook(
    *,
    runner: Callable[[], dict[str, Any]] | None = None,
    logger: logging.Logger | Any | None = None,
) -> dict[str, Any]:
    """Schedule the standalone ASE post-scan hook without blocking scan return."""
    global _ase_running
    hook_log = logger or log
    with _ase_lock:
        if _ase_running:
            return {"success": None, "status": "already_running"}
        _ase_running = True

    def _target() -> None:
        global _ase_running
        try:
            run = runner
            if run is None:
                from athena_ase.runtime.scan import run_ase_full_scan_and_execute

                run = run_ase_full_scan_and_execute
            payload = run()
            if isinstance(payload, dict):
                hook_log.info(
                    "[ASE] post-scan hook completed signalCount=%s timeStopsClosed=%s",
                    payload.get("signalCount"),
                    len(payload.get("timeStopsClosed") or []),
                )
            else:
                hook_log.info("[ASE] post-scan hook completed")
        except Exception as exc:
            hook_log.warning("[ASE] post-scan hook failed (non-fatal): %s", exc)
        finally:
            with _ase_lock:
                _ase_running = False

    thread = threading.Thread(target=_target, daemon=True, name="ASEPostScanHook")
    thread.start()
    return {"success": None, "status": "scheduled"}


def schedule_engine_b_funnel_persist_hook(
    scan_payload: dict[str, Any],
    *,
    pair_types_by_display: dict[str, str] | None = None,
    runner: Callable[..., dict[str, Any]] | None = None,
    logger: logging.Logger | Any | None = None,
) -> dict[str, Any]:
    """Schedule Engine B funnel artifact persistence outside the scan response."""
    hook_log = logger or log
    payload_snapshot = copy.deepcopy(scan_payload if isinstance(scan_payload, dict) else {})
    pair_type_snapshot = dict(pair_types_by_display or {})

    def _target() -> None:
        try:
            run = runner
            if run is None:
                from athena_app.diagnostics.engine_b_gate_funnel_persist import (
                    maybe_persist_engine_b_scan_gate_funnel,
                )

                run = maybe_persist_engine_b_scan_gate_funnel
            meta = run(
                payload_snapshot,
                pair_types_by_display=pair_type_snapshot,
            )
            if isinstance(meta, dict):
                hook_log.info(
                    "[ENGINE_B_FUNNEL_PERSIST] background status saved=%s summary=%s error=%s",
                    meta.get("engine_b_scan_gate_funnel_saved"),
                    meta.get("engine_b_scan_gate_funnel_summary_path"),
                    meta.get("engine_b_scan_gate_funnel_saved_error"),
                )
        except Exception as exc:
            hook_log.warning(
                "[ENGINE_B_FUNNEL_PERSIST] background write failed (non-fatal): %s",
                exc,
            )

    thread = threading.Thread(target=_target, daemon=True, name="EngineBFunnelPersist")
    thread.start()
    return {"success": None, "status": "scheduled"}


def publish_scan_result_and_schedule_conductor(
    scan_result: dict[str, Any],
    *,
    publish_initial: Callable[[dict[str, Any]], None],
    publish_enriched: Callable[[dict[str, Any], dict[str, Any]], None],
    audit_db: str,
    conductor_loader: Callable[[], Any] | None = None,
    logger: logging.Logger | Any | None = None,
    max_signals: int = 30,
) -> dict[str, Any]:
    """Publish the scan result immediately, then enrich conductor context later."""
    publish_initial(scan_result)
    signals = scan_result.get("signals") if isinstance(scan_result, dict) else None
    if not isinstance(signals, list) or not signals:
        return {"success": None, "status": "skipped", "reason": "no_signals"}

    snapshot = copy.deepcopy(scan_result)
    return _schedule_conductor_enrichment(
        snapshot,
        base_result=scan_result,
        publish_enriched=publish_enriched,
        audit_db=audit_db,
        conductor_loader=conductor_loader,
        logger=logger,
        max_signals=max_signals,
    )


def _schedule_conductor_enrichment(
    scan_result: dict[str, Any],
    *,
    base_result: dict[str, Any],
    publish_enriched: Callable[[dict[str, Any], dict[str, Any]], None],
    audit_db: str,
    conductor_loader: Callable[[], Any] | None,
    logger: logging.Logger | Any | None,
    max_signals: int,
) -> dict[str, Any]:
    hook_log = logger or log

    def _target() -> None:
        try:
            with _conductor_lock:
                enriched = _build_conductor_enriched_scan(
                    scan_result,
                    audit_db=audit_db,
                    conductor_loader=conductor_loader,
                    max_signals=max_signals,
                )
            publish_enriched(enriched, base_result)
        except Exception as exc:
            hook_log.warning("[CONDUCTOR] scan-side orchestration failed: %s", exc)

    thread = threading.Thread(target=_target, daemon=True, name="ScanConductorEnrichment")
    thread.start()
    return {"success": None, "status": "scheduled"}


def _build_conductor_enriched_scan(
    scan_result: dict[str, Any],
    *,
    audit_db: str,
    conductor_loader: Callable[[], Any] | None,
    max_signals: int,
) -> dict[str, Any]:
    conductor = conductor_loader() if conductor_loader is not None else _load_conductor()
    signals = scan_result.get("signals") if isinstance(scan_result, dict) else []
    if not isinstance(signals, list):
        return scan_result

    conductor.reset_scan_results("engine_a")
    conductor_plan = None
    for sig in signals[:max(0, max_signals)]:
        if not isinstance(sig, dict):
            continue
        volume_divergence, stop_run = conductor.extract_conductor_microstructure(sig)
        plan = conductor.conductor_orchestrate(
            sig,
            sig.get("regime", "UNKNOWN"),
            audit_db,
            volume_divergence=volume_divergence,
            stop_run=stop_run,
        )
        if conductor_plan is None:
            conductor_plan = plan

    if conductor_plan:
        scan_result["conductor"] = conductor_plan.get("routing", {})
        scan_result["conductor_context"] = conductor_plan.get("context", {})
    return scan_result


def _load_conductor() -> Any:
    import conductor

    return conductor
