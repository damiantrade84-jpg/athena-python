"""Persist and summarize native `engine_b_scan_gate_funnel` scan payloads (diagnostics-only).

Does not mutate trading logic, tiers, thresholds, or execution behavior beyond writing files.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import CONFIG

log = logging.getLogger(__name__)
_sentinel_log = logging.getLogger("sentinel")


def _repository_root() -> Path:
    """Athena checkout root (directory that contains ``athena_app``)."""
    return Path(__file__).resolve().parents[2]


def _anchor_relative_output_path(path_fragment: str | Path) -> Path:
    """Resolve a configured path: absolute paths untouched; relative → under repo root."""
    p = Path(os.path.expandvars(str(path_fragment).strip())).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (_repository_root() / p).resolve()


def engine_b_gate_funnel_persist_enabled() -> bool:
    """Honor env override: 0 = off, 1 = on; otherwise CONFIG flag (default-on)."""
    ev = os.environ.get("ENGINE_B_SCAN_GATE_FUNNEL_PERSIST")
    if ev is not None:
        ev = ev.strip()
        if ev == "0":
            return False
        if ev == "1":
            return True
    return bool(CONFIG.get("ENGINE_B_SCAN_GATE_FUNNEL_PERSIST_ENABLED", True))


def funnel_persist_output_dir() -> Path:
    """Configured default output directory (relative paths → repository root)."""
    raw = CONFIG.get("ENGINE_B_SCAN_GATE_FUNNEL_OUTPUT_DIR") or "logs/engine_b_gate_funnel"
    return _anchor_relative_output_path(raw)


def resolve_funnel_output_directory(output_dir: str | Path | None) -> Path:
    """Explicit output dir for writes; ``None`` uses :func:`funnel_persist_output_dir`."""
    if output_dir is None:
        return funnel_persist_output_dir()
    return _anchor_relative_output_path(output_dir)


def write_engine_b_funnel_scan_touch_file(scan_payload: dict[str, Any]) -> dict[str, Any]:
    """Write ``scan_funnel_touch.json``; creates output dir even when full persist is skipped."""
    outcome: dict[str, Any] = {
        "wrote_touch_file": False,
        "touch_path": None,
        "touch_error": None,
        "cwd": os.getcwd(),
    }
    try:
        od = funnel_persist_output_dir()
        od.mkdir(parents=True, exist_ok=True)
        body = {
            "scannedAt": scan_payload.get("scannedAt"),
            "success": scan_payload.get("success"),
            "payloadVersion": scan_payload.get("payloadVersion"),
            "repository_root": str(_repository_root()),
            "resolved_output_dir": str(od),
            "ENGINE_B_SCAN_GATE_FUNNEL_ENABLED": bool(
                CONFIG.get("ENGINE_B_SCAN_GATE_FUNNEL_ENABLED", True)
            ),
            "ENGINE_B_SCAN_GATE_FUNNEL_PERSIST_ENABLED": bool(
                CONFIG.get("ENGINE_B_SCAN_GATE_FUNNEL_PERSIST_ENABLED", True)
            ),
            "env_ENGINE_B_SCAN_GATE_FUNNEL_PERSIST": os.environ.get(
                "ENGINE_B_SCAN_GATE_FUNNEL_PERSIST"
            ),
            "engine_b_scan_gate_funnel_saved": scan_payload.get(
                "engine_b_scan_gate_funnel_saved"
            ),
            "engine_b_scan_gate_funnel_summary_path": scan_payload.get(
                "engine_b_scan_gate_funnel_summary_path"
            ),
            "engine_b_scan_gate_funnel_persist_skipped": scan_payload.get(
                "engine_b_scan_gate_funnel_persist_skipped"
            ),
            "engine_b_scan_gate_funnel_saved_error": scan_payload.get(
                "engine_b_scan_gate_funnel_saved_error"
            ),
            "persist_output_dir": scan_payload.get("engine_b_scan_gate_funnel_output_dir"),
        }
        tp = od / "scan_funnel_touch.json"
        tp.write_text(_json_dumps(body), encoding="utf-8")
        outcome["wrote_touch_file"] = True
        outcome["touch_path"] = str(tp)
        _sentinel_log.info("[ENGINE_B_FUNNEL_TOUCH] wrote %s", tp)
    except OSError as exc:
        outcome["touch_error"] = str(exc)
        _sentinel_log.warning("[ENGINE_B_FUNNEL_TOUCH] failed: %s", exc)
    except Exception as exc:  # pragma: no cover
        outcome["touch_error"] = str(exc)
        _sentinel_log.warning("[ENGINE_B_FUNNEL_TOUCH] unexpected: %s", exc)
    return outcome


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _norm_sym(x: Any) -> str | None:
    if x is None:
        return None
    s = str(x).strip()
    return s or None


def _norm_asset_type(x: Any) -> str:
    if x is None:
        return "unknown"
    s = str(x).strip().lower()
    return s or "unknown"


def _bool_is_false(val: Any) -> bool:
    if val is False:
        return True
    if isinstance(val, str) and val.strip().lower() == "false":
        return True
    return False


def _failed_gate_has(row: dict[str, Any], name: str) -> bool:
    fg = row.get("failed_gate_names")
    if not isinstance(fg, list):
        return False
    return name.lower() in {str(x).strip().lower() for x in fg if x is not None}


def flatten_scan_to_funnel_rows(
    scan: dict[str, Any] | None,
    *,
    pair_types_by_display: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Project trade / watchlist / skipped scan outputs into flattened funnel-ish rows."""
    pair_types_by_display = pair_types_by_display or {}
    rows: list[dict[str, Any]] = []
    scan = scan if isinstance(scan, dict) else {}

    def augment_from_sig(sig: dict[str, Any], list_source: str) -> dict[str, Any]:
        sym = (
            _norm_sym(sig.get("pair"))
            or _norm_sym(sig.get("display"))
            or _norm_sym(sig.get("symbol"))
        )
        at_raw = sig.get("type") or pair_types_by_display.get(sym or "")
        fd = sig.get("engine_b_scan_gate_funnel")
        if isinstance(fd, dict):
            out = dict(fd)
            out.setdefault("symbol", fd.get("symbol") or sym)
            out.setdefault("asset_type", fd.get("asset_type") or at_raw)
            out["list_source"] = list_source
            out["has_funnel"] = True
            # Mirror confidence for summarizer even if funnel predates attach field.
            if out.get("engine_b_confidence_passed") is None:
                out["engine_b_confidence_passed"] = sig.get(
                    "engine_b_confidence_passed"
                )
            return out
        return {
            "symbol": sym,
            "asset_type": _norm_asset_type(at_raw),
            "has_funnel": False,
            "reason": "no_engine_b_scan_gate_funnel",
            "list_source": list_source,
        }

    signals = scan.get("tradeSignals")
    if not isinstance(signals, list):
        signals = scan.get("signals") or []
    if not isinstance(signals, list):
        signals = []
    watchlist = scan.get("watchlist") or []
    skipped = scan.get("skipped") or []

    for sig in signals:
        if isinstance(sig, dict):
            rows.append(augment_from_sig(sig, "trade_signals"))
    if isinstance(watchlist, list):
        for sig in watchlist:
            if isinstance(sig, dict):
                rows.append(augment_from_sig(sig, "watchlist"))
    if isinstance(skipped, list):
        for sk in skipped:
            if not isinstance(sk, dict):
                continue
            sym = _norm_sym(sk.get("pair")) or _norm_sym(sk.get("symbol"))
            at_raw = sk.get("type") or pair_types_by_display.get(sym or "")
            fd = sk.get("engine_b_scan_gate_funnel")
            if isinstance(fd, dict):
                out = dict(fd)
                out.setdefault("symbol", fd.get("symbol") or sym)
                out.setdefault("asset_type", fd.get("asset_type") or at_raw)
                out["list_source"] = "skipped"
                out["has_funnel"] = True
                rows.append(out)
            else:
                rows.append(
                    {
                        "symbol": sym,
                        "asset_type": _norm_asset_type(at_raw),
                        "has_funnel": False,
                        "reason": "no_engine_b_scan_gate_funnel",
                        "list_source": "skipped",
                        "skip_reason_text": sk.get("reason"),
                    }
                )

    errs = scan.get("errors") or []
    if isinstance(errs, list):
        for e in errs:
            if not isinstance(e, dict):
                continue
            sym = _norm_sym(e.get("pair"))
            rows.append(
                {
                    "symbol": sym,
                    "asset_type": _norm_asset_type(pair_types_by_display.get(sym or "")),
                    "has_funnel": False,
                    "reason": "scan_error",
                    "list_source": "errors",
                    "error_text": str(e.get("error") or ""),
                }
            )
    return rows


def summarize_funnel_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build histogram dict for latest_funnel_summary.json."""

    def row_asset(r: dict[str, Any]) -> str:
        return _norm_asset_type(r.get("asset_type"))

    gate_counter: Counter[str] = Counter()
    err_counter: Counter[str] = Counter()

    for r in rows:
        fg = r.get("failed_gate_names")
        if isinstance(fg, list):
            for name in fg:
                if name is None:
                    continue
                gate_counter[str(name)] += 1
        ee = r.get("engine_b_error")
        if ee is not None and str(ee).strip():
            err_counter[str(ee).strip()] += 1

    overall = {
        "total_rows": len(rows),
        "rows_with_funnel": sum(1 for r in rows if r.get("has_funnel")),
        "rows_without_funnel": sum(1 for r in rows if not r.get("has_funnel")),
        "trade_rows": sum(1 for r in rows if r.get("list_source") == "trade_signals"),
        "watchlist_rows": sum(1 for r in rows if r.get("list_source") == "watchlist"),
        "skipped_rows": sum(1 for r in rows if r.get("list_source") == "skipped"),
        "error_rows": sum(1 for r in rows if r.get("list_source") == "errors"),
        "sig_a_present_true": sum(
            1 for r in rows if r.get("has_funnel") and r.get("sig_a_present") is True
        ),
        "sig_a_present_false": sum(
            1 for r in rows if r.get("has_funnel") and r.get("sig_a_present") is False
        ),
        "engine_b_evaluated_true": sum(
            1 for r in rows if r.get("has_funnel") and r.get("engine_b_evaluated") is True
        ),
        "engine_b_evaluated_false": sum(
            1 for r in rows if r.get("has_funnel") and r.get("engine_b_evaluated") is False
        ),
        "structure_executed_true": sum(
            1 for r in rows if r.get("has_funnel") and r.get("structure_executed") is True
        ),
        "structure_executed_false": sum(
            1 for r in rows if r.get("has_funnel") and r.get("structure_executed") is False
        ),
    }

    def asset_bucket_rows(asset: str) -> list[dict[str, Any]]:
        return [r for r in rows if row_asset(r) == asset]

    by_at: dict[str, Any] = {}
    seen_assets = sorted({row_asset(r) for r in rows})
    for at in seen_assets:
        sub = asset_bucket_rows(at)
        by_at[at] = {
            "total": len(sub),
            "trade": sum(1 for r in sub if r.get("list_source") == "trade_signals"),
            "watchlist": sum(1 for r in sub if r.get("list_source") == "watchlist"),
            "skipped": sum(1 for r in sub if r.get("list_source") == "skipped"),
            "sig_a_missing": sum(
                1
                for r in sub
                if (not r.get("has_funnel"))
                and r.get("list_source") == "skipped"
                and (
                    (r.get("skip_reason_text") or "") == "No data"
                    or r.get("reason") == "no_engine_b_scan_gate_funnel"
                )
            ),
            "b_not_evaluated": sum(
                1 for r in sub if r.get("has_funnel") and r.get("engine_b_evaluated") is False
            ),
            "bybit_atr_unavailable": sum(
                1
                for r in sub
                if r.get("engine_b_error") == "bybit_atr_unavailable"
                or r.get("engine_b_skip_stage") == "crypto_bybit_atr_unavailable"
            ),
            "structure_not_executed": sum(
                1 for r in sub if r.get("has_funnel") and r.get("structure_executed") is not True
            ),
            "structure_failed": sum(
                1
                for r in sub
                if r.get("has_funnel")
                and r.get("structure_executed") is True
                and (
                    (r.get("structure_verdict") or "") != "CLEAR"
                    or _bool_is_false(r.get("structure_ok"))
                )
            ),
            "confidence_failed": sum(
                1
                for r in sub
                if r.get("has_funnel") and _bool_is_false(r.get("engine_b_confidence_passed"))
            ),
            "entry_failed": sum(
                1 for r in sub if r.get("has_funnel") and _bool_is_false(r.get("entry_ok"))
            ),
            "location_failed": sum(
                1 for r in sub if r.get("has_funnel") and _bool_is_false(r.get("location_ok"))
            ),
            "room_failed": sum(
                1 for r in sub if r.get("has_funnel") and _bool_is_false(r.get("room_ok"))
            ),
            "space_gate_failed": sum(
                1
                for r in sub
                if r.get("has_funnel")
                and (
                    _bool_is_false(r.get("space_gate_ok"))
                    or _failed_gate_has(r, "space")
                )
            ),
            "rr_failed": sum(
                1
                for r in sub
                if r.get("has_funnel")
                and (
                    _bool_is_false(r.get("rr_ok"))
                    or r.get("execution_level_reject_reason") == "structural_tp_below_min_rr"
                )
            ),
            "structural_tp_below_min_rr": sum(
                1
                for r in sub
                if r.get("execution_level_reject_reason") == "structural_tp_below_min_rr"
            ),
            "final_pass": sum(
                1
                for r in sub
                if r.get("has_funnel")
                and r.get("engine_b_confidence_passed") is True
                and r.get("structure_verdict") == "CLEAR"
            ),
        }

    def crypto_question_block(asset: str) -> dict[str, int]:
        scoped = asset_bucket_rows(asset)
        return {
            "A_crypto_skipped_sig_a_missing": sum(
                1
                for r in scoped
                if not r.get("has_funnel")
                and r.get("list_source") == "skipped"
                and (
                    (r.get("skip_reason_text") or "") == "No data"
                    or r.get("reason") == "no_engine_b_scan_gate_funnel"
                )
            ),
            "B_reached_engine_b_evaluation": sum(
                1 for r in scoped if r.get("has_funnel") and r.get("engine_b_evaluated") is True
            ),
            "C_structure_executed_true": sum(
                1 for r in scoped if r.get("has_funnel") and r.get("structure_executed") is True
            ),
            "D_bybit_atr_unavailable_or_skip": sum(
                1
                for r in scoped
                if r.get("engine_b_error") == "bybit_atr_unavailable"
                or r.get("engine_b_skip_stage") == "crypto_bybit_atr_unavailable"
            ),
            "E_rr_failed": sum(
                1
                for r in scoped
                if r.get("has_funnel")
                and (
                    _bool_is_false(r.get("rr_ok"))
                    or r.get("execution_level_reject_reason")
                    == "structural_tp_below_min_rr"
                )
            ),
            "F_space_gate_failed": sum(
                1
                for r in scoped
                if r.get("has_funnel")
                and (
                    _bool_is_false(r.get("space_gate_ok"))
                    or _failed_gate_has(r, "space")
                )
            ),
            "G_watchlist_only_rows": sum(
                1 for r in scoped if r.get("list_source") == "watchlist"
            ),
            "H_trade_rows": sum(1 for r in scoped if r.get("list_source") == "trade_signals"),
        }

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall": overall,
        "by_asset_type": by_at,
        "crypto_section": crypto_question_block("crypto"),
        "forex_section": crypto_question_block("forex"),
        "top_blockers": {
            "failed_gate_names": gate_counter.most_common(40),
            "engine_b_error": err_counter.most_common(40),
        },
    }
    return summary


def write_funnel_rows_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


class _PosixRel:
    """Keep summary paths workspace-relative when under the repo root."""

    @staticmethod
    def repo_relative(path: Path) -> Path:
        try:
            return path.resolve().relative_to(_repository_root())
        except Exception:
            return path.resolve()


def save_engine_b_scan_gate_funnel(
    scan_payload_or_results: dict[str, Any] | None,
    *,
    pair_types_by_display: dict[str, str] | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Write latest_full_scan snapshot, funnel rows JSONL, and summary JSON.

    Returns metadata dict suitable for merging into HTTP scan payloads.
    Fail-closed file IO: warns and returns saved=false without raising.
    """
    od = resolve_funnel_output_directory(output_dir)

    meta = {
        "engine_b_scan_gate_funnel_saved": False,
        "engine_b_scan_gate_funnel_summary_path": (
            str(_PosixRel.repo_relative(od / "latest_funnel_summary.json")).replace("\\", "/")
        ),
        "engine_b_scan_gate_funnel_output_dir": str(od).replace("\\", "/"),
        "engine_b_scan_gate_funnel_saved_error": None,
    }

    try:
        log.info(
            "[ENGINE_B_FUNNEL_PERSIST] writing funnel artifacts -> %s (cwd=%s)",
            od,
            os.getcwd(),
        )
        _sentinel_log.info(
            "[ENGINE_B_FUNNEL_PERSIST] writing funnel artifacts -> %s (cwd=%s)",
            od,
            os.getcwd(),
        )
        od.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        payload = scan_payload_or_results if isinstance(scan_payload_or_results, dict) else {}

        rows = flatten_scan_to_funnel_rows(payload, pair_types_by_display=pair_types_by_display)
        summary_blob = summarize_funnel_rows(rows)

        latest_scan = od / "latest_full_scan.json"
        stamped = od / f"full_scan_{stamp}.json"

        latest_scan.write_text(_json_dumps(payload), encoding="utf-8")
        stamped.write_text(_json_dumps(payload), encoding="utf-8")

        write_funnel_rows_jsonl(od / "latest_funnel_rows.jsonl", rows)
        (od / "latest_funnel_summary.json").write_text(
            _json_dumps(summary_blob), encoding="utf-8"
        )

        meta["engine_b_scan_gate_funnel_saved"] = True
        rp = _PosixRel.repo_relative(od / "latest_funnel_summary.json")
        meta["engine_b_scan_gate_funnel_summary_path"] = str(rp).replace("\\", "/")
    except OSError as exc:
        log.warning("[ENGINE_B_FUNNEL_PERSIST] write failed (non-fatal): %s", exc)
        _sentinel_log.warning("[ENGINE_B_FUNNEL_PERSIST] write failed (non-fatal): %s", exc)
        meta["engine_b_scan_gate_funnel_saved"] = False
        meta["engine_b_scan_gate_funnel_saved_error"] = str(exc)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("[ENGINE_B_FUNNEL_PERSIST] unexpected failure (non-fatal): %s", exc)
        _sentinel_log.warning(
            "[ENGINE_B_FUNNEL_PERSIST] unexpected failure (non-fatal): %s", exc
        )
        meta["engine_b_scan_gate_funnel_saved"] = False
        meta["engine_b_scan_gate_funnel_saved_error"] = str(exc)

    return meta


def maybe_persist_engine_b_scan_gate_funnel(
    scan_result: dict[str, Any],
    *,
    pair_types_by_display: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Entry point from scanner: respects funnel enable + persist flags + env."""
    if not bool(CONFIG.get("ENGINE_B_SCAN_GATE_FUNNEL_ENABLED", True)):
        meta = {
            "engine_b_scan_gate_funnel_saved": False,
            "engine_b_scan_gate_funnel_summary_path": None,
            "engine_b_scan_gate_funnel_persist_skipped": "ENGINE_B_SCAN_GATE_FUNNEL_DISABLED",
        }
        _sentinel_log.info(
            "[ENGINE_B_FUNNEL_PERSIST] skipped: funnel disabled (%s)",
            meta["engine_b_scan_gate_funnel_persist_skipped"],
        )
        return meta
    if not engine_b_gate_funnel_persist_enabled():
        meta = {
            "engine_b_scan_gate_funnel_saved": False,
            "engine_b_scan_gate_funnel_summary_path": None,
            "engine_b_scan_gate_funnel_persist_skipped": "ENGINE_B_SCAN_GATE_FUNNEL_PERSIST_DISABLED",
        }
        _sentinel_log.info(
            "[ENGINE_B_FUNNEL_PERSIST] skipped: persist disabled (%s env=%r)",
            meta["engine_b_scan_gate_funnel_persist_skipped"],
            os.environ.get("ENGINE_B_SCAN_GATE_FUNNEL_PERSIST"),
        )
        return meta
    return save_engine_b_scan_gate_funnel(
        scan_result,
        pair_types_by_display=pair_types_by_display,
    )


def scheduled_engine_b_scan_gate_funnel_meta() -> dict[str, Any]:
    """Metadata for scan responses when full funnel artifact writes run later."""
    if not bool(CONFIG.get("ENGINE_B_SCAN_GATE_FUNNEL_ENABLED", True)):
        return {
            "engine_b_scan_gate_funnel_saved": False,
            "engine_b_scan_gate_funnel_summary_path": None,
            "engine_b_scan_gate_funnel_persist_skipped": "ENGINE_B_SCAN_GATE_FUNNEL_DISABLED",
        }
    if not engine_b_gate_funnel_persist_enabled():
        return {
            "engine_b_scan_gate_funnel_saved": False,
            "engine_b_scan_gate_funnel_summary_path": None,
            "engine_b_scan_gate_funnel_persist_skipped": "ENGINE_B_SCAN_GATE_FUNNEL_PERSIST_DISABLED",
        }

    od = funnel_persist_output_dir()
    return {
        "engine_b_scan_gate_funnel_saved": False,
        "engine_b_scan_gate_funnel_summary_path": str(
            _PosixRel.repo_relative(od / "latest_funnel_summary.json")
        ).replace("\\", "/"),
        "engine_b_scan_gate_funnel_output_dir": str(od).replace("\\", "/"),
        "engine_b_scan_gate_funnel_saved_error": None,
        "engine_b_scan_gate_funnel_persist_status": "scheduled",
    }


def regenerate_funnel_artifacts_from_scan_file(
    input_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    pair_types_by_display: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    """Rebuild ``latest_funnel_rows.jsonl`` + ``latest_funnel_summary.json`` only."""
    inp = Path(input_path)
    raw = inp.read_text(encoding="utf-8")
    payload = json.loads(raw) if raw.strip() else {}
    if not isinstance(payload, dict):
        payload = {}

    od = resolve_funnel_output_directory(output_dir)
    od.mkdir(parents=True, exist_ok=True)
    rows = flatten_scan_to_funnel_rows(payload, pair_types_by_display=pair_types_by_display)
    row_path = od / "latest_funnel_rows.jsonl"
    summ_path = od / "latest_funnel_summary.json"
    write_funnel_rows_jsonl(row_path, rows)
    summ_path.write_text(_json_dumps(summarize_funnel_rows(rows)), encoding="utf-8")
    return row_path, summ_path
