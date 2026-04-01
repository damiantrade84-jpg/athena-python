"""Lightweight Signal Stability Index (SSI) monitor for Athena.

Tracks rolling per-engine stability signals with graceful degradation.
"""

from __future__ import annotations

import json
import logging
import math
import os
import queue
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("athena.stability")

_ENGINES = ("engine_a", "engine_b", "engine_c", "scalp")
_CURRENT_WINDOW = 50
_BASELINE_WINDOW = 200
_MIN_CURRENT = 5
_MIN_BASELINE = 10

_audit_queue: queue.Queue = queue.Queue()
_audit_pending = 0
_audit_pending_lock = threading.Lock()
_audit_worker_started = False
_audit_worker_lock = threading.Lock()

_AUDIT_BATCH_MAX = 64

_INSERT_STABILITY_EVENT_SQL = """
                INSERT INTO stability_events
                (ts, engine, source, event_type, score_norm, passed, expected_prob,
                 realized_win, execution_ok, slippage_bps, expectancy_r, feature_json, meta_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """


def _default_db_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_engine_name(engine: str | None) -> str | None:
    raw = str(engine or "").strip().lower()
    aliases = {
        "a": "engine_a",
        "engine_a": "engine_a",
        "enginea": "engine_a",
        "factor": "engine_a",
        "factor_scoring": "engine_a",
        "forex_scoring": "engine_a",
        "b": "engine_b",
        "engine_b": "engine_b",
        "engineb": "engine_b",
        "naked": "engine_b",
        "naked_engine": "engine_b",
        "c": "engine_c",
        "engine_c": "engine_c",
        "enginec": "engine_c",
        "consensus": "engine_c",
        "scalp": "scalp",
        "engine_d": "scalp",
    }
    return aliases.get(raw)


def _clamp01(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_score(score: float | None = None, max_score: float | None = None) -> float | None:
    if score is None:
        return None
    score_f = _safe_float(score)
    if score_f is None:
        return None
    max_f = _safe_float(max_score)
    if max_f and max_f > 0:
        return _clamp01(score_f / max_f)
    if 0.0 <= score_f <= 1.0:
        return score_f
    return _clamp01(score_f)


def _normalize_expected_prob(value: float | None) -> float | None:
    val = _safe_float(value)
    if val is None:
        return None
    if val > 1.0:
        val /= 100.0
    return _clamp01(val)


def _sanitize_feature_map(feature_map: dict | None) -> dict:
    out = {}
    if not isinstance(feature_map, dict):
        return out
    for key, raw in feature_map.items():
        if raw is None:
            continue
        name = str(key).strip()
        if not name:
            continue
        if isinstance(raw, bool):
            out[name] = 1.0 if raw else 0.0
            continue
        val = _safe_float(raw)
        if val is None:
            continue
        # Preserve 0-1 values; softly squash wider ranges into -1..1.
        if abs(val) <= 1.0:
            out[name] = val
        else:
            out[name] = math.tanh(val / 3.0)
    return out


def init_stability_store(db_path: str | None = None) -> None:
    path = db_path or _default_db_path()
    try:
        with sqlite3.connect(path, timeout=15.0) as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS stability_events (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts              TEXT NOT NULL,
                    engine          TEXT NOT NULL,
                    source          TEXT,
                    event_type      TEXT,
                    score_norm      REAL,
                    passed          INTEGER,
                    expected_prob   REAL,
                    realized_win    REAL,
                    execution_ok    INTEGER,
                    slippage_bps    REAL,
                    expectancy_r    REAL,
                    feature_json    TEXT,
                    meta_json       TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS stability_summaries (
                    engine          TEXT PRIMARY KEY,
                    updated_at      TEXT NOT NULL,
                    baseline_json   TEXT,
                    current_json    TEXT
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_stability_engine_ts ON stability_events (engine, id DESC)"
            )
            con.commit()
    except Exception as exc:
        log.debug(f"[SSI] init failed: {exc}")


def _audit_worker_loop() -> None:
    """Daemon thread: batch INSERT stability_events and refresh summaries per engine."""
    global _audit_pending
    while True:
        try:
            first = _audit_queue.get()
        except Exception:
            continue
        batch = [first]
        try:
            while len(batch) < _AUDIT_BATCH_MAX:
                batch.append(_audit_queue.get_nowait())
        except queue.Empty:
            pass

        n_batch = len(batch)
        try:
            by_path: dict[str, list[dict[str, Any]]] = {}
            for row in batch:
                if not isinstance(row, dict) or "vals" not in row or "db_path" not in row:
                    log.debug("[SSI] audit worker: skipping bad payload")
                    continue
                path = row["db_path"]
                by_path.setdefault(path, []).append(row)

            for path, rows in by_path.items():
                if not rows:
                    continue
                try:
                    init_stability_store(path)
                    tuples = [r["vals"] for r in rows]
                    engines = {r["engine"] for r in rows}
                    with sqlite3.connect(path, timeout=15.0) as con:
                        try:
                            con.executemany(_INSERT_STABILITY_EVENT_SQL, tuples)
                        except Exception as exc:
                            log.warning(f"[SSI] batch insert failed, row-by-row: {exc}")
                            for t in tuples:
                                try:
                                    con.execute(_INSERT_STABILITY_EVENT_SQL, t)
                                except Exception as row_exc:
                                    log.debug(f"[SSI] row insert failed: {row_exc}")
                        con.commit()
                    for eng in engines:
                        _refresh_engine_summary(path, eng)
                except Exception as exc:
                    log.debug(f"[SSI] audit worker batch failed for {path}: {exc}")
        finally:
            with _audit_pending_lock:
                _audit_pending -= n_batch


def join_stability_audit_queue(timeout: float | None = None) -> None:
    """Block until queued stability events are persisted (for tests / deterministic reads)."""
    deadline = float("inf") if timeout is None else time.time() + timeout
    while True:
        with _audit_pending_lock:
            pending = _audit_pending
        if pending == 0 and _audit_queue.empty():
            time.sleep(0.03)
            with _audit_pending_lock:
                pending2 = _audit_pending
            if pending2 == 0 and _audit_queue.empty():
                return
        if time.time() >= deadline:
            with _audit_pending_lock:
                pend = _audit_pending
            raise TimeoutError(
                f"stability audit queue not drained within {timeout}s ({pend} pending)"
            )
        time.sleep(0.02)


def _ensure_audit_worker() -> None:
    global _audit_worker_started
    if _audit_worker_started:
        return
    with _audit_worker_lock:
        if _audit_worker_started:
            return
        _t = threading.Thread(
            target=_audit_worker_loop,
            daemon=True,
            name="stability-audit-worker",
        )
        _t.start()
        _audit_worker_started = True


def record_signal_event(
    engine: str,
    score: float | None = None,
    max_score: float | None = None,
    passed: bool | None = None,
    expected_prob: float | None = None,
    feature_map: dict | None = None,
    source: str = "runtime",
    event_type: str = "signal",
    db_path: str | None = None,
    meta: dict | None = None,
) -> None:
    _record_event(
        engine=engine,
        score_norm=_normalize_score(score, max_score),
        passed=passed,
        expected_prob=_normalize_expected_prob(expected_prob),
        feature_map=feature_map,
        source=source,
        event_type=event_type,
        db_path=db_path,
        meta=meta,
    )


def record_execution_event(
    engine: str,
    success: bool,
    score: float | None = None,
    max_score: float | None = None,
    expected_prob: float | None = None,
    slippage_bps: float | None = None,
    source: str = "runtime",
    event_type: str = "execution",
    db_path: str | None = None,
    meta: dict | None = None,
) -> None:
    _record_event(
        engine=engine,
        score_norm=_normalize_score(score, max_score),
        expected_prob=_normalize_expected_prob(expected_prob),
        execution_ok=success,
        slippage_bps=_safe_float(slippage_bps),
        source=source,
        event_type=event_type,
        db_path=db_path,
        meta=meta,
    )


def record_outcome_event(
    engine: str,
    won: bool | None = None,
    expected_prob: float | None = None,
    expectancy_r: float | None = None,
    score: float | None = None,
    max_score: float | None = None,
    source: str = "runtime",
    event_type: str = "outcome",
    db_path: str | None = None,
    meta: dict | None = None,
) -> None:
    _record_event(
        engine=engine,
        score_norm=_normalize_score(score, max_score),
        expected_prob=_normalize_expected_prob(expected_prob),
        realized_win=won,
        expectancy_r=_safe_float(expectancy_r),
        source=source,
        event_type=event_type,
        db_path=db_path,
        meta=meta,
    )


def record_backtest_summary(
    engine: str,
    pass_rate: float | None = None,
    expectancy_r: float | None = None,
    score: float | None = None,
    max_score: float | None = None,
    feature_map: dict | None = None,
    db_path: str | None = None,
    meta: dict | None = None,
) -> None:
    passed = None
    if pass_rate is not None:
        pass_rate = _normalize_expected_prob(pass_rate)
    _record_event(
        engine=engine,
        score_norm=_normalize_score(score, max_score),
        passed=passed,
        expected_prob=pass_rate,
        expectancy_r=_safe_float(expectancy_r),
        feature_map=feature_map,
        source="backtest",
        event_type="backtest",
        db_path=db_path,
        meta=meta,
    )


def get_signal_stability_index(
    engine: str | None = None, db_path: str | None = None
) -> dict:
    path = db_path or _default_db_path()
    init_stability_store(path)
    norm_engine = _normalize_engine_name(engine) if engine else None
    if norm_engine:
        return _engine_ssi(path, norm_engine)
    return _system_ssi(path)


def get_engine_stability_snapshot(engine: str, db_path: str | None = None) -> dict:
    """Return the current/baseline summary block for one engine.

    This is a lightweight public helper for higher-level controllers that need
    more than the compact SSI score while still reusing the same rolling windows.
    """
    path = db_path or _default_db_path()
    init_stability_store(path)
    norm_engine = _normalize_engine_name(engine)
    if not norm_engine:
        return {
            "engine": None,
            "available": False,
            "current": {},
            "baseline": {},
            "updatedAt": None,
            "window": {"current_events": 0, "baseline_events": 0},
        }

    _refresh_engine_summary(path, norm_engine)
    ssi = _engine_ssi(path, norm_engine)
    try:
        with sqlite3.connect(path, timeout=15.0) as con:
            row = con.execute(
                "SELECT baseline_json, current_json, updated_at FROM stability_summaries WHERE engine=?",
                (norm_engine,),
            ).fetchone()
    except Exception as exc:
        log.debug(f"[SSI] snapshot read failed for {norm_engine}: {exc}")
        row = None

    baseline = json.loads(row[0] or "{}") if row else {}
    current = json.loads(row[1] or "{}") if row else {}
    updated_at = row[2] if row else None
    return {
        "engine": norm_engine,
        "available": bool(current or baseline),
        "current": current,
        "baseline": baseline,
        "updatedAt": updated_at,
        "ssi": ssi.get("ssi"),
        "band": ssi.get("band"),
        "warnings": ssi.get("warnings", []),
        "window": ssi.get("window", {"current_events": 0, "baseline_events": 0}),
    }


def _record_event(
    engine: str,
    score_norm: float | None = None,
    passed: bool | None = None,
    expected_prob: float | None = None,
    realized_win: bool | None = None,
    execution_ok: bool | None = None,
    slippage_bps: float | None = None,
    expectancy_r: float | None = None,
    feature_map: dict | None = None,
    source: str = "runtime",
    event_type: str = "signal",
    db_path: str | None = None,
    meta: dict | None = None,
) -> None:
    global _audit_pending
    norm_engine = _normalize_engine_name(engine)
    if not norm_engine:
        return
    path = db_path or _default_db_path()
    vals = (
        _utc_now(),
        norm_engine,
        str(source or "runtime"),
        str(event_type or "signal"),
        _clamp01(score_norm),
        None if passed is None else (1 if bool(passed) else 0),
        _normalize_expected_prob(expected_prob),
        None if realized_win is None else (1 if bool(realized_win) else 0),
        None if execution_ok is None else (1 if bool(execution_ok) else 0),
        _safe_float(slippage_bps),
        _safe_float(expectancy_r),
        json.dumps(_sanitize_feature_map(feature_map), sort_keys=True),
        json.dumps(meta or {}, sort_keys=True),
    )
    try:
        _ensure_audit_worker()
        with _audit_pending_lock:
            _audit_pending += 1
        _audit_queue.put(
            {"db_path": path, "engine": norm_engine, "vals": vals},
            block=False,
        )
    except Exception as exc:
        with _audit_pending_lock:
            _audit_pending -= 1
        log.debug(f"[SSI] audit queue put failed for {norm_engine}: {exc}")


def _refresh_engine_summary(db_path: str, engine: str) -> None:
    rows = _fetch_engine_rows(db_path, engine)
    current_rows = rows[:_CURRENT_WINDOW]
    baseline_rows = rows[_CURRENT_WINDOW : _CURRENT_WINDOW + _BASELINE_WINDOW]
    current = _summarize_rows(current_rows)
    baseline = _summarize_rows(baseline_rows)
    try:
        with sqlite3.connect(db_path, timeout=15.0) as con:
            con.execute(
                """
                INSERT INTO stability_summaries (engine, updated_at, baseline_json, current_json)
                VALUES (?,?,?,?)
                ON CONFLICT(engine) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    baseline_json=excluded.baseline_json,
                    current_json=excluded.current_json
                """,
                (
                    engine,
                    _utc_now(),
                    json.dumps(baseline, sort_keys=True),
                    json.dumps(current, sort_keys=True),
                ),
            )
            con.commit()
    except Exception as exc:
        log.debug(f"[SSI] summary refresh failed for {engine}: {exc}")


def _fetch_engine_rows(db_path: str, engine: str) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            """
            SELECT * FROM stability_events
            WHERE engine=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (engine, _CURRENT_WINDOW + _BASELINE_WINDOW),
        )
        return cur.fetchall()


def _summarize_rows(rows: list[sqlite3.Row]) -> dict:
    summary: dict[str, Any] = {
        "count": len(rows),
        "score_mean": None,
        "pass_rate": None,
        "expected_prob_mean": None,
        "realized_rate": None,
        "calibration_gap": None,
        "execution_success_rate": None,
        "adverse_slippage_bps": None,
        "expectancy_mean": None,
        "feature_means": {},
    }
    if not rows:
        return summary

    def _mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 6) if values else None

    scores = [float(r["score_norm"]) for r in rows if r["score_norm"] is not None]
    passes = [float(r["passed"]) for r in rows if r["passed"] is not None]
    expected = [float(r["expected_prob"]) for r in rows if r["expected_prob"] is not None]
    realized = [float(r["realized_win"]) for r in rows if r["realized_win"] is not None]
    execs = [float(r["execution_ok"]) for r in rows if r["execution_ok"] is not None]
    slippage = [
        max(0.0, float(r["slippage_bps"]))
        for r in rows
        if r["slippage_bps"] is not None
    ]
    expectancy = [float(r["expectancy_r"]) for r in rows if r["expectancy_r"] is not None]

    feature_buckets: dict[str, list[float]] = {}
    for row in rows:
        try:
            fmap = json.loads(row["feature_json"] or "{}")
        except Exception:
            fmap = {}
        if not isinstance(fmap, dict):
            continue
        for key, value in fmap.items():
            num = _safe_float(value)
            if num is None:
                continue
            feature_buckets.setdefault(str(key), []).append(num)

    summary["score_mean"] = _mean(scores)
    summary["pass_rate"] = _mean(passes)
    summary["expected_prob_mean"] = _mean(expected)
    summary["realized_rate"] = _mean(realized)
    summary["execution_success_rate"] = _mean(execs)
    summary["adverse_slippage_bps"] = _mean(slippage)
    summary["expectancy_mean"] = _mean(expectancy)
    if summary["expected_prob_mean"] is not None and summary["realized_rate"] is not None:
        summary["calibration_gap"] = round(
            abs(summary["expected_prob_mean"] - summary["realized_rate"]), 6
        )
    summary["feature_means"] = {
        k: round(sum(v) / len(v), 6) for k, v in feature_buckets.items() if v
    }
    return summary


def _component_score(drift: float | None, scale: float) -> float | None:
    if drift is None:
        return None
    scaled = min(1.0, abs(float(drift)) / scale) if scale > 0 else 1.0
    return round(100.0 * (1.0 - scaled), 2)


def _drift(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None:
        return None
    return float(current) - float(baseline)


def _feature_drift(current: dict, baseline: dict) -> float | None:
    if not current or not baseline:
        return None
    keys = sorted(set(current) & set(baseline))
    if not keys:
        return None
    diffs = [abs(float(current[k]) - float(baseline[k])) for k in keys]
    return round(sum(diffs) / len(diffs), 6)


def _build_components(current: dict, baseline: dict) -> tuple[dict, list[str]]:
    components = {}
    warnings = []

    def _add(name: str, score: float | None, current_value: Any, baseline_value: Any, drift_value: Any,
             runtime_only: bool = False, note: str | None = None) -> None:
        available = score is not None
        payload = {
            "score": score,
            "available": available,
            "runtime_only": runtime_only,
            "current": current_value,
            "baseline": baseline_value,
            "drift": drift_value,
        }
        if note:
            payload["note"] = note
        components[name] = payload
        if not available:
            label = f"{name} unavailable"
            if runtime_only:
                label += " (runtime-only: insufficient data)"
            warnings.append(label)
        elif score < 60:
            warnings.append(f"{name} degraded ({score:.1f})")

    score_drift = _drift(current.get("score_mean"), baseline.get("score_mean"))
    _add(
        "score_drift",
        _component_score(score_drift, 0.20),
        current.get("score_mean"),
        baseline.get("score_mean"),
        score_drift,
    )

    pass_drift = _drift(current.get("pass_rate"), baseline.get("pass_rate"))
    _add(
        "pass_rate_drift",
        _component_score(pass_drift, 0.25),
        current.get("pass_rate"),
        baseline.get("pass_rate"),
        pass_drift,
    )

    cur_gap = current.get("calibration_gap")
    base_gap = baseline.get("calibration_gap")
    cal_drift = _drift(cur_gap, base_gap)
    cal_score = _component_score(cal_drift if cal_drift is not None else cur_gap, 0.20)
    _add(
        "calibration_drift",
        cal_score,
        cur_gap,
        base_gap,
        cal_drift,
        runtime_only=True,
    )

    feat_drift = _feature_drift(
        current.get("feature_means") or {}, baseline.get("feature_means") or {}
    )
    _add(
        "feature_drift",
        _component_score(feat_drift, 0.30),
        current.get("feature_means"),
        baseline.get("feature_means"),
        feat_drift,
    )

    exec_rate_drift = _drift(
        current.get("execution_success_rate"), baseline.get("execution_success_rate")
    )
    slip_drift = _drift(
        current.get("adverse_slippage_bps"), baseline.get("adverse_slippage_bps")
    )
    exec_penalty_parts = []
    if exec_rate_drift is not None:
        exec_penalty_parts.append(min(1.0, abs(exec_rate_drift) / 0.20))
    elif current.get("execution_success_rate") is not None:
        exec_penalty_parts.append(min(1.0, (1.0 - current["execution_success_rate"]) / 0.20))
    if slip_drift is not None:
        exec_penalty_parts.append(min(1.0, abs(slip_drift) / 20.0))
    elif current.get("adverse_slippage_bps") is not None:
        exec_penalty_parts.append(min(1.0, current["adverse_slippage_bps"] / 20.0))
    exec_score = None
    if exec_penalty_parts:
        exec_score = round(100.0 * (1.0 - min(1.0, sum(exec_penalty_parts) / len(exec_penalty_parts))), 2)
    _add(
        "execution_drift",
        exec_score,
        {
            "execution_success_rate": current.get("execution_success_rate"),
            "adverse_slippage_bps": current.get("adverse_slippage_bps"),
        },
        {
            "execution_success_rate": baseline.get("execution_success_rate"),
            "adverse_slippage_bps": baseline.get("adverse_slippage_bps"),
        },
        {
            "execution_success_rate": exec_rate_drift,
            "adverse_slippage_bps": slip_drift,
        },
        runtime_only=True,
    )

    exp_drift = _drift(current.get("expectancy_mean"), baseline.get("expectancy_mean"))
    exp_score = _component_score(exp_drift if exp_drift is not None else current.get("expectancy_mean"), 1.0)
    _add(
        "expectancy_drift",
        exp_score,
        current.get("expectancy_mean"),
        baseline.get("expectancy_mean"),
        exp_drift,
    )

    return components, warnings


def _band_for_ssi(ssi: float) -> str:
    if ssi >= 80:
        return "GREEN"
    if ssi >= 60:
        return "YELLOW"
    if ssi >= 40:
        return "ORANGE"
    return "RED"


def _engine_ssi(db_path: str, engine: str) -> dict:
    init_stability_store(db_path)
    try:
        with sqlite3.connect(db_path, timeout=15.0) as con:
            row = con.execute(
                "SELECT baseline_json, current_json, updated_at FROM stability_summaries WHERE engine=?",
                (engine,),
            ).fetchone()
    except Exception as exc:
        log.debug(f"[SSI] read failed for {engine}: {exc}")
        row = None

    if row:
        baseline = json.loads(row[0] or "{}")
        current = json.loads(row[1] or "{}")
        updated_at = row[2]
    else:
        _refresh_engine_summary(db_path, engine)
        try:
            with sqlite3.connect(db_path, timeout=15.0) as con:
                row = con.execute(
                    "SELECT baseline_json, current_json, updated_at FROM stability_summaries WHERE engine=?",
                    (engine,),
                ).fetchone()
            baseline = json.loads(row[0] or "{}") if row else {}
            current = json.loads(row[1] or "{}") if row else {}
            updated_at = row[2] if row else None
        except Exception:
            baseline = {}
            current = {}
            updated_at = None

    components, warnings = _build_components(current, baseline)
    available_scores = [
        comp["score"] for comp in components.values() if comp.get("available") and comp.get("score") is not None
    ]
    ssi = round(sum(available_scores) / len(available_scores), 2) if available_scores else 50.0
    return {
        "engine": engine,
        "ssi": ssi,
        "band": _band_for_ssi(ssi),
        "components": components,
        "warnings": warnings,
        "updatedAt": updated_at,
        "window": {
            "current_events": current.get("count", 0),
            "baseline_events": baseline.get("count", 0),
        },
    }


def _system_ssi(db_path: str) -> dict:
    engine_results = [_engine_ssi(db_path, engine) for engine in _ENGINES]
    component_scores: dict[str, list[float]] = {}
    warnings = []
    for result in engine_results:
        if result["band"] in ("ORANGE", "RED"):
            warnings.append(f"{result['engine']} stability {result['band'].lower()} ({result['ssi']:.1f})")
        for name, comp in result["components"].items():
            if comp.get("available") and comp.get("score") is not None:
                component_scores.setdefault(name, []).append(comp["score"])

    components = {}
    for name, values in component_scores.items():
        score = round(sum(values) / len(values), 2) if values else None
        components[name] = {
            "score": score,
            "available": bool(values),
            "runtime_only": name in {"calibration_drift", "execution_drift"},
        }

    available_engine_ssi = [result["ssi"] for result in engine_results]
    ssi = round(sum(available_engine_ssi) / len(available_engine_ssi), 2) if available_engine_ssi else 50.0
    return {
        "ssi": ssi,
        "band": _band_for_ssi(ssi),
        "components": components,
        "warnings": warnings,
        "engines": {result["engine"]: {"ssi": result["ssi"], "band": result["band"]} for result in engine_results},
    }
