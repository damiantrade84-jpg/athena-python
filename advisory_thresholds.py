"""Advisory threshold recommendations for dashboard review and approval.

This module is intentionally conservative:
- Recommendations are derived from the latest backtest results already stored in audit.db.
- Approvals and rejections are tracked in SQLite for auditability.
- Live application of an approved recommendation is handled by the caller.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from config import CONFIG

_ENGINE_A_BY_ASSET = {
    "crypto": "factor_scoring",
    "commodity": "factor_scoring",
    "stock": "factor_scoring",
    "index": "factor_scoring",
    "forex": "forex_scoring",
}

_ASSET_LABELS = {
    "crypto": "Crypto",
    "commodity": "Commodity",
    "stock": "Stock",
    "index": "Index",
    "forex": "Forex",
}

_BT_STEP = {"forex": 0.03}
_BT_LIMITS = {
    "forex": (0.20, 0.95),
    "crypto": (0.30, 2.00),
    "commodity": (0.30, 2.00),
    "stock": (0.30, 2.00),
    "index": (0.30, 2.00),
}
_LIVE_STEP = 0.05
_LIVE_LIMITS = {
    "forex": (0.30, 1.00),
    "crypto": (0.60, 2.50),
    "commodity": (0.60, 2.50),
    "stock": (0.60, 2.50),
    "index": (0.60, 2.50),
}
_ENGINE_A_TRADE_FLOOR = {
    "forex": 12.0,
    "crypto": 18.0,
    "commodity": 12.0,
    "stock": 12.0,
    "index": 12.0,
}
_ENGINE_B_STYLE_FLOOR = {
    "scalp": 80.0,
    "intraday": 24.0,
    "swing": 12.0,
}
_ENGINE_B_STYLE_LIMITS = {
    "scalp": (2.0, 6.0),
    "intraday": (3.0, 6.0),
    "swing": (3.0, 6.0),
}


def _default_db_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_advisory_store(db_path: str | None = None) -> None:
    path = db_path or _default_db_path()
    with sqlite3.connect(path, timeout=15.0) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS advisory_threshold_actions (
                rec_id       TEXT PRIMARY KEY,
                action       TEXT NOT NULL,
                note         TEXT,
                payload_json TEXT NOT NULL,
                created_at   TEXT NOT NULL
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_adv_threshold_action ON advisory_threshold_actions (action, created_at DESC)"
        )
        con.commit()


def _round_to_step(value: float, step: float) -> float:
    if step <= 0:
        return round(value, 4)
    return round(round(value / step) * step, 4)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _confidence_from_count(count: int) -> str:
    if count >= 15:
        return "high"
    if count >= 8:
        return "medium"
    return "low"


def _parse_style(notes: str | None) -> str | None:
    text = str(notes or "")
    match = re.search(r"style=([^;]+)", text)
    return match.group(1).strip().lower() if match else None


def _latest_backtest_rows(db_path: str | None = None) -> list[dict[str, Any]]:
    path = db_path or _default_db_path()
    with sqlite3.connect(path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            WITH ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (PARTITION BY pair, engine ORDER BY run_date DESC) AS rn
                FROM backtest_results
            )
            SELECT *
            FROM ranked
            WHERE rn = 1
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _current_policy_snapshot() -> dict[str, Any]:
    ne = CONFIG.get("NAKED_ENGINE") or {}
    styles = ne.get("style_profiles") or {}
    return {
        "engine_a_backtest": dict(CONFIG.get("BT_MIN") or {}),
        "engine_a_live": dict(CONFIG.get("MIN_CONFLUENCE_CLASS") or {}),
        "engine_b_styles": {
            key: {
                "min_score": (dict(styles.get(key) or {})).get("min_score"),
                "min_rr": (dict(styles.get(key) or {})).get("min_rr"),
            }
            for key in ("scalp", "intraday", "swing")
        },
    }


def _recommendation_id(scope_type: str, scope_key: str, current_value: float, proposed_value: float) -> str:
    raw = f"{scope_type}|{scope_key}|{round(current_value, 4)}|{round(proposed_value, 4)}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _build_engine_a_recommendations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    current_bt = dict(CONFIG.get("BT_MIN") or {})
    current_live = dict(CONFIG.get("MIN_CONFLUENCE_CLASS") or {})

    for asset_type, engine_name in _ENGINE_A_BY_ASSET.items():
        bucket = [row for row in rows if row.get("engine") == engine_name and row.get("asset_type") == asset_type]
        if len(bucket) < 4:
            continue

        pair_count = len(bucket)
        avg_trades = round(sum(float(row.get("trades") or 0.0) for row in bucket) / pair_count, 2)
        avg_sqn = round(sum(float(row.get("sqn") or 0.0) for row in bucket) / pair_count, 2)
        avg_wr = round(sum(float(row.get("win_rate") or 0.0) for row in bucket) / pair_count, 2)

        cur_bt = float(current_bt.get(asset_type, 0.0) or 0.0)
        bt_step = _BT_STEP.get(asset_type, 0.05)
        bt_low, bt_high = _BT_LIMITS.get(asset_type, (0.30, 2.00))
        proposed_bt = cur_bt
        direction = None
        reasons: list[str] = []

        if avg_sqn <= -0.25 and pair_count >= 5:
            proposed_bt = _clamp(_round_to_step(cur_bt + bt_step, bt_step), bt_low, bt_high)
            direction = "tighten"
            reasons.append(
                f"Latest Engine A cohort averages SQN {avg_sqn:.2f} across {pair_count} {asset_type} pairs."
            )
        elif avg_trades < _ENGINE_A_TRADE_FLOOR.get(asset_type, 12.0) and avg_sqn >= 0.25 and pair_count >= 4:
            proposed_bt = _clamp(_round_to_step(cur_bt - bt_step, bt_step), bt_low, bt_high)
            direction = "loosen"
            reasons.append(
                f"Latest Engine A cohort averages only {avg_trades:.1f} trades per pair with SQN {avg_sqn:.2f}."
            )

        if direction and abs(proposed_bt - cur_bt) >= 0.0001:
            scope_key = asset_type
            out.append(
                {
                    "id": _recommendation_id("engine_a_bt_class", scope_key, cur_bt, proposed_bt),
                    "scope_type": "engine_a_bt_class",
                    "scope_key": scope_key,
                    "engine": "engine_a",
                    "environment": "backtest",
                    "title": f"Engine A Backtest | {_ASSET_LABELS.get(asset_type, asset_type.title())}",
                    "subtitle": "Class-level BT_MIN update",
                    "current_value": round(cur_bt, 4),
                    "proposed_value": round(proposed_bt, 4),
                    "delta": round(proposed_bt - cur_bt, 4),
                    "direction": direction,
                    "confidence": _confidence_from_count(pair_count),
                    "metrics": {
                        "pairs": pair_count,
                        "avg_trades": avg_trades,
                        "avg_sqn": avg_sqn,
                        "avg_win_rate": avg_wr,
                    },
                    "reasons": reasons,
                }
            )

            cur_live = float(current_live.get(asset_type, 0.0) or 0.0)
            live_low, live_high = _LIVE_LIMITS.get(asset_type, (0.60, 2.50))
            ratio = (cur_live / cur_bt) if cur_bt > 0 else (1.65 if asset_type != "forex" else 1.60)
            proposed_live = _clamp(_round_to_step(proposed_bt * ratio, _LIVE_STEP), live_low, live_high)
            if abs(proposed_live - cur_live) >= 0.0001:
                out.append(
                    {
                        "id": _recommendation_id("engine_a_live_class", scope_key, cur_live, proposed_live),
                        "scope_type": "engine_a_live_class",
                        "scope_key": scope_key,
                        "engine": "engine_a",
                        "environment": "live",
                        "title": f"Engine A Live | {_ASSET_LABELS.get(asset_type, asset_type.title())}",
                        "subtitle": "Class-level MIN_CONFLUENCE_CLASS update",
                        "current_value": round(cur_live, 4),
                        "proposed_value": round(proposed_live, 4),
                        "delta": round(proposed_live - cur_live, 4),
                        "direction": direction,
                        "confidence": _confidence_from_count(pair_count),
                        "metrics": {
                            "pairs": pair_count,
                            "avg_trades": avg_trades,
                            "avg_sqn": avg_sqn,
                            "avg_win_rate": avg_wr,
                        },
                        "reasons": reasons
                        + [
                            f"Live threshold is scaled from the current live/backtest ratio ({ratio:.2f}x)."
                        ],
                    }
                )

    return out


def _build_engine_b_recommendations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    styles = ((CONFIG.get("NAKED_ENGINE") or {}).get("style_profiles") or {})
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("engine") != "naked_engine":
            continue
        style = _parse_style(row.get("notes"))
        if not style:
            continue
        buckets.setdefault(style, []).append(row)

    for style in ("scalp", "intraday", "swing"):
        bucket = buckets.get(style) or []
        if len(bucket) < 3:
            continue
        pair_count = len(bucket)
        avg_trades = round(sum(float(row.get("trades") or 0.0) for row in bucket) / pair_count, 2)
        avg_sqn = round(sum(float(row.get("sqn") or 0.0) for row in bucket) / pair_count, 2)
        avg_wr = round(sum(float(row.get("win_rate") or 0.0) for row in bucket) / pair_count, 2)
        cur_min = float((dict(styles.get(style) or {})).get("min_score", 0.0) or 0.0)
        min_low, min_high = _ENGINE_B_STYLE_LIMITS.get(style, (2.0, 6.0))
        proposed = cur_min
        direction = None
        reasons: list[str] = []

        if avg_sqn <= -0.20 and avg_trades >= _ENGINE_B_STYLE_FLOOR.get(style, 12.0) and pair_count >= 5:
            proposed = _clamp(cur_min + 1.0, min_low, min_high)
            direction = "tighten"
            reasons.append(
                f"Latest Engine B {style} cohort averages SQN {avg_sqn:.2f} across {pair_count} pairs."
            )
        elif avg_sqn >= 0.75 and avg_trades < _ENGINE_B_STYLE_FLOOR.get(style, 12.0) * 0.75 and pair_count >= 5:
            proposed = _clamp(cur_min - 1.0, min_low, min_high)
            direction = "loosen"
            reasons.append(
                f"Latest Engine B {style} cohort keeps quality but only averages {avg_trades:.1f} trades per pair."
            )

        if direction and abs(proposed - cur_min) >= 0.0001:
            out.append(
                {
                    "id": _recommendation_id("engine_b_style", style, cur_min, proposed),
                    "scope_type": "engine_b_style",
                    "scope_key": style,
                    "engine": "engine_b",
                    "environment": "live+backtest",
                    "title": f"Engine B | {style.title()}",
                    "subtitle": "NAKED_ENGINE.style_profiles min_score update",
                    "current_value": round(cur_min, 4),
                    "proposed_value": round(proposed, 4),
                    "delta": round(proposed - cur_min, 4),
                    "direction": direction,
                    "confidence": _confidence_from_count(pair_count),
                    "metrics": {
                        "pairs": pair_count,
                        "avg_trades": avg_trades,
                        "avg_sqn": avg_sqn,
                        "avg_win_rate": avg_wr,
                    },
                    "reasons": reasons
                    + [
                        "Engine B score gates are whole checklist points, so approvals move in integer steps."
                    ],
                }
            )

    return out


def build_threshold_recommendations(db_path: str | None = None) -> list[dict[str, Any]]:
    rows = _latest_backtest_rows(db_path=db_path)
    recommendations = _build_engine_a_recommendations(rows)
    recommendations.extend(_build_engine_b_recommendations(rows))
    recommendations.sort(key=lambda rec: (rec.get("environment"), rec.get("title")))
    return recommendations


def _load_actions(db_path: str | None = None) -> list[dict[str, Any]]:
    path = db_path or _default_db_path()
    ensure_advisory_store(path)
    with sqlite3.connect(path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT rec_id, action, note, payload_json, created_at
            FROM advisory_threshold_actions
            ORDER BY created_at DESC
            """
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.get("payload_json") or "{}")
        except json.JSONDecodeError:
            item["payload"] = {}
        out.append(item)
    return out


def get_recommendation_snapshot(db_path: str | None = None) -> dict[str, Any]:
    path = db_path or _default_db_path()
    ensure_advisory_store(path)
    pending = build_threshold_recommendations(db_path=path)
    actions = _load_actions(db_path=path)
    action_map = {item["rec_id"]: item for item in actions}

    pending_out = [item for item in pending if item["id"] not in action_map]
    approved = []
    rejected = []
    for item in actions:
        payload = dict(item.get("payload") or {})
        payload["action_at"] = item.get("created_at")
        payload["action_note"] = item.get("note")
        payload["status"] = item.get("action")
        if item.get("action") == "approved":
            approved.append(payload)
        elif item.get("action") == "rejected":
            rejected.append(payload)

    return {
        "summary": {
            "pending": len(pending_out),
            "approved": len(approved),
            "rejected": len(rejected),
            "last_recomputed": _utc_now(),
        },
        "current_policies": _current_policy_snapshot(),
        "pending": pending_out,
        "approved": approved,
        "rejected": rejected,
    }


def find_pending_recommendation(rec_id: str, db_path: str | None = None) -> dict[str, Any] | None:
    for recommendation in build_threshold_recommendations(db_path=db_path):
        if recommendation.get("id") == rec_id:
            return recommendation
    return None


def record_action(
    rec_id: str,
    action: str,
    payload: dict[str, Any],
    note: str | None = None,
    db_path: str | None = None,
) -> None:
    path = db_path or _default_db_path()
    ensure_advisory_store(path)
    with sqlite3.connect(path, timeout=15.0) as con:
        con.execute(
            """
            INSERT INTO advisory_threshold_actions (rec_id, action, note, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(rec_id) DO UPDATE SET
                action = excluded.action,
                note = excluded.note,
                payload_json = excluded.payload_json,
                created_at = excluded.created_at
            """,
            (
                rec_id,
                str(action),
                (str(note).strip() if note is not None else None),
                json.dumps(payload or {}, sort_keys=True),
                _utc_now(),
            ),
        )
        con.commit()
