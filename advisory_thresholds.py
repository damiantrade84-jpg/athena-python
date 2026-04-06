"""Advisory threshold recommendations for dashboard review and approval.

This module is intentionally conservative:
- Recommendations combine the latest backtest cohorts with closed live trade outcomes already stored in audit.db.
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
    "forex": (0.80, 1.90),
    "crypto": (0.30, 2.00),
    "commodity": (0.30, 2.00),
    "stock": (0.30, 2.00),
    "index": (0.30, 2.00),
}
_LIVE_STEP = 0.05
_LIVE_LIMITS = {
    "forex": (1.00, 2.00),
    "crypto": (0.60, 2.50),
    "commodity": (0.60, 2.50),
    "stock": (0.60, 2.50),
    "index": (0.60, 2.50),
}
_ENGINE_A_TRADE_FLOOR = {
    "forex": 25.0,
    "crypto": 30.0,
    "commodity": 25.0,
    "stock": 25.0,
    "index": 25.0,
}
_ENGINE_B_STYLE_FLOOR = {
    "scalp": 80.0,
    "intraday": 30.0,
    "swing": 25.0,
}
_ENGINE_B_STYLE_LIMITS = {
    "scalp": (2.0, 6.0),
    "intraday": (3.0, 6.0),
    "swing": (3.0, 6.0),
}
_LIVE_ENGINE_A_MIN_TRADES = 8
_LIVE_ENGINE_A_LOOSEN_MIN_TRADES = 8
_LIVE_ENGINE_B_MIN_TRADES = 8
_LIVE_ENGINE_B_LOOSEN_MIN_TRADES = 8


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


def _confidence_from_count(count: int, avg_trades: float = 0.0) -> str:
    total = count * avg_trades if avg_trades > 0 else count * 10
    if count >= 10 and total >= 300:
        return "high"
    if count >= 6 and total >= 100:
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


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw in (None, ""):
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _infer_asset_type_from_pair(pair: str | None) -> str:
    p = str(pair or "").upper()
    if not p:
        return "unknown"
    if "USDT" in p:
        return "crypto"
    if any(tok in p for tok in ("XAU", "XAG", "WTI", "BRENT", "NATGAS", "OIL", "GLD")):
        return "commodity"
    if any(tok in p for tok in ("SPY", "QQQ", "S&P", "NASDAQ", "NAS", "DAX", "FTSE", "NIKKEI", "HSI")):
        return "index"
    if any(tok in p for tok in ("EUR", "GBP", "USD", "JPY", "AUD", "NZD", "CHF", "CAD", "ZAR", "MXN", "SGD")):
        return "forex"
    return "stock"


def _infer_live_engine(row: dict[str, Any]) -> str | None:
    engine = str(row.get("engine") or "").strip().lower()
    if engine in ("engine_a", "engine_b", "engine_c", "scalp", "external"):
        return engine

    style = str(row.get("style") or "").strip().lower()
    grade = str(row.get("grade") or "").strip().upper()
    if style == "scalp" or grade == "SCALP":
        return "scalp"
    if grade == "WEBHOOK":
        return "external"

    factors = _load_json_dict(row.get("factors_json"))
    scores = factors.get("scores") if isinstance(factors.get("scores"), dict) else {}
    if any(str(key).startswith("Naked_") for key in scores.keys()):
        return "engine_b"
    if scores:
        return "engine_a"

    max_score = _safe_float(row.get("max_score"))
    if max_score is not None:
        return "engine_a"

    if style in ("intraday", "swing") and row.get("pair") and row.get("score") is not None:
        return "engine_b"

    if row.get("asset_class") and row.get("score") is not None:
        return "engine_a"
    return None


def _closed_live_trade_rows(db_path: str | None = None) -> list[dict[str, Any]]:
    path = db_path or _default_db_path()
    with sqlite3.connect(path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT *
            FROM audit_log
            WHERE exit_price IS NOT NULL
              AND pnl IS NOT NULL
            ORDER BY COALESCE(exit_time, ts) DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _sqn_from_r_values(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    if len(clean) < 2:
        return None
    mean = sum(clean) / len(clean)
    variance = sum((v - mean) ** 2 for v in clean) / (len(clean) - 1)
    if variance <= 0:
        return None
    return round(mean / (variance**0.5) * (len(clean) ** 0.5), 2)


def _summarize_live_bucket(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pair_count = len({str(row.get("pair") or "") for row in rows if row.get("pair")})
    trade_count = len(rows)
    wins = sum(1 for row in rows if (_safe_float(row.get("pnl")) or 0.0) > 0)
    r_vals = [_safe_float(row.get("r_multiple")) for row in rows]
    r_vals = [value for value in r_vals if value is not None]
    avg_r = round(sum(r_vals) / len(r_vals), 2) if r_vals else None
    sqn = _sqn_from_r_values(r_vals)
    slippage = [abs(_safe_float(row.get("slippage_bps")) or 0.0) for row in rows if row.get("slippage_bps") is not None]
    return {
        "pairs": pair_count,
        "trades": trade_count,
        "avg_trades": round(trade_count / pair_count, 2) if pair_count else float(trade_count),
        "avg_sqn": sqn,
        "avg_r": avg_r,
        "avg_win_rate": round(wins / trade_count * 100, 1) if trade_count else 0.0,
        "avg_slippage_bps": round(sum(slippage) / len(slippage), 2) if slippage else None,
    }


def _live_engine_a_signal(summary: dict[str, Any]) -> str | None:
    trades = int(summary.get("trades") or 0)
    sqn = _safe_float(summary.get("avg_sqn"))
    avg_r = _safe_float(summary.get("avg_r"))
    if trades < 8:
        return None
    if sqn is not None and sqn <= -0.50:
        return "tighten"
    if sqn is not None and sqn >= 0.75 and avg_r is not None and avg_r >= 0.10:
        return "loosen"
    return None


def _live_engine_b_signal(summary: dict[str, Any]) -> str | None:
    trades = int(summary.get("trades") or 0)
    sqn = _safe_float(summary.get("avg_sqn"))
    avg_r = _safe_float(summary.get("avg_r"))
    if trades < 8:
        return None
    if sqn is not None and sqn <= -0.40:
        return "tighten"
    if sqn is not None and sqn >= 0.75 and avg_r is not None and avg_r >= 0.10:
        return "loosen"
    return None


def _summarize_live_engine_a(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if _infer_live_engine(row) != "engine_a":
            continue
        asset = str(row.get("asset_class") or _infer_asset_type_from_pair(row.get("pair"))).strip().lower()
        if asset not in _ASSET_LABELS:
            continue
        buckets.setdefault(asset, []).append(row)
    return {asset: _summarize_live_bucket(bucket) for asset, bucket in buckets.items()}


def _summarize_live_engine_b(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if _infer_live_engine(row) != "engine_b":
            continue
        style = str(row.get("style") or "").strip().lower()
        if style not in ("scalp", "intraday", "swing"):
            continue
        buckets.setdefault(style, []).append(row)
    return {style: _summarize_live_bucket(bucket) for style, bucket in buckets.items()}


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

        if avg_sqn <= -0.50 and pair_count >= 5:
            proposed_bt = _clamp(_round_to_step(cur_bt + bt_step, bt_step), bt_low, bt_high)
            direction = "tighten"
            reasons.append(
                f"Latest Engine A cohort averages SQN {avg_sqn:.2f} across {pair_count} {asset_type} pairs."
            )
        elif avg_trades < _ENGINE_A_TRADE_FLOOR.get(asset_type, 25.0) and avg_sqn >= 0.25 and pair_count >= 4:
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
                    "confidence": _confidence_from_count(pair_count, avg_trades),
                    "metrics": {
                        "pairs": pair_count,
                        "avg_trades": avg_trades,
                        "avg_sqn": avg_sqn,
                        "avg_win_rate": avg_wr,
                    },
                    "reasons": reasons,
                    "requires_human_approval": True,
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
                        "confidence": _confidence_from_count(pair_count, avg_trades),
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
                        "requires_human_approval": True,
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

        if avg_sqn <= -0.20 and avg_trades >= _ENGINE_B_STYLE_FLOOR.get(style, 25.0) and pair_count >= 5:
            proposed = _clamp(cur_min + 1.0, min_low, min_high)
            direction = "tighten"
            reasons.append(
                f"Latest Engine B {style} cohort averages SQN {avg_sqn:.2f} across {pair_count} pairs."
            )
        elif avg_sqn >= 0.75 and avg_trades < _ENGINE_B_STYLE_FLOOR.get(style, 25.0) * 0.75 and pair_count >= 5:
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
                    "confidence": _confidence_from_count(pair_count, avg_trades),
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
                    "requires_human_approval": True,
                }
            )

    return out


def _confidence_rank(value: str) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(str(value or "").lower(), 0)


def _confidence_from_rank(rank: int) -> str:
    return {0: "low", 1: "medium", 2: "high"}.get(max(0, min(2, rank)), "low")


def _format_live_reason(summary: dict[str, Any], label: str) -> str:
    return (
        f"Live {label} closed trades: {int(summary.get('trades') or 0)} trades, "
        f"SQN {summary.get('avg_sqn') if summary.get('avg_sqn') is not None else 'n/a'}, "
        f"avg R {summary.get('avg_r') if summary.get('avg_r') is not None else 'n/a'}, "
        f"win rate {(summary.get('avg_win_rate') or 0):.1f}%."
    )


def _merge_live_evidence(
    recommendations: list[dict[str, Any]],
    live_a: dict[str, dict[str, Any]],
    live_b: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in recommendations:
        merged = dict(rec)
        reasons = list(merged.get("reasons") or [])
        confidence_rank = _confidence_rank(merged.get("confidence"))
        live_evidence_conflict = False
        if merged.get("scope_type") == "engine_a_live_class":
            summary = live_a.get(str(merged.get("scope_key") or ""))
            if summary:
                reasons.append(_format_live_reason(summary, "Engine A"))
                live_dir = _live_engine_a_signal(summary)
                if live_dir == merged.get("direction"):
                    reasons.append("Live closed trade outcomes support the same threshold direction.")
                    confidence_rank += 1
                elif live_dir and live_dir != merged.get("direction"):
                    reasons.append("Live closed trade outcomes currently oppose this threshold direction.")
                    confidence_rank -= 1
                    live_evidence_conflict = True
                merged["metrics"] = {
                    **dict(merged.get("metrics") or {}),
                    "live_pairs": summary.get("pairs"),
                    "live_trades": summary.get("trades"),
                }
        elif merged.get("scope_type") == "engine_b_style":
            summary = live_b.get(str(merged.get("scope_key") or ""))
            if summary:
                reasons.append(_format_live_reason(summary, f"Engine B {merged.get('scope_key')}"))
                live_dir = _live_engine_b_signal(summary)
                if live_dir == merged.get("direction"):
                    reasons.append("Live closed trade outcomes support the same checklist gate direction.")
                    confidence_rank += 1
                elif live_dir and live_dir != merged.get("direction"):
                    reasons.append("Live closed trade outcomes currently oppose this checklist gate direction.")
                    confidence_rank -= 1
                    live_evidence_conflict = True
                merged["metrics"] = {
                    **dict(merged.get("metrics") or {}),
                    "live_pairs": summary.get("pairs"),
                    "live_trades": summary.get("trades"),
                }
        merged["reasons"] = reasons
        merged["confidence"] = _confidence_from_rank(confidence_rank)
        merged["live_evidence_conflict"] = live_evidence_conflict
        merged["requires_human_approval"] = True
        out.append(merged)
    return out


def _build_live_engine_a_recommendations(live_a: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    current_live = dict(CONFIG.get("MIN_CONFLUENCE_CLASS") or {})
    for asset_type, summary in live_a.items():
        cur_live = float(current_live.get(asset_type, 0.0) or 0.0)
        live_low, live_high = _LIVE_LIMITS.get(asset_type, (0.60, 2.50))
        direction = _live_engine_a_signal(summary)
        if not direction:
            continue
        if direction == "tighten":
            proposed = _clamp(_round_to_step(cur_live + _LIVE_STEP, _LIVE_STEP), live_low, live_high)
        else:
            proposed = _clamp(_round_to_step(cur_live - _LIVE_STEP, _LIVE_STEP), live_low, live_high)
        if abs(proposed - cur_live) < 0.0001:
            continue
        out.append(
            {
                "id": _recommendation_id("engine_a_live_class", asset_type, cur_live, proposed),
                "scope_type": "engine_a_live_class",
                "scope_key": asset_type,
                "engine": "engine_a",
                "environment": "live",
                "title": f"Engine A Live | {_ASSET_LABELS.get(asset_type, asset_type.title())}",
                "subtitle": "Class-level MIN_CONFLUENCE_CLASS update from live outcomes",
                "current_value": round(cur_live, 4),
                "proposed_value": round(proposed, 4),
                "delta": round(proposed - cur_live, 4),
                "direction": direction,
                "confidence": _confidence_from_count(
                    int(summary.get("pairs") or 0),
                    float(summary.get("avg_trades") or 0.0),
                ),
                "metrics": dict(summary),
                "reasons": [
                    _format_live_reason(summary, "Engine A"),
                    "This recommendation is driven by closed live trade outcomes from the Performance ledger.",
                ],
                "requires_human_approval": True,
            }
        )
    return out


def _build_live_engine_b_recommendations(live_b: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    styles = ((CONFIG.get("NAKED_ENGINE") or {}).get("style_profiles") or {})
    for style, summary in live_b.items():
        cur_min = float((dict(styles.get(style) or {})).get("min_score", 0.0) or 0.0)
        min_low, min_high = _ENGINE_B_STYLE_LIMITS.get(style, (2.0, 6.0))
        direction = _live_engine_b_signal(summary)
        if not direction:
            continue
        proposed = _clamp(cur_min + 1.0, min_low, min_high) if direction == "tighten" else _clamp(cur_min - 1.0, min_low, min_high)
        if abs(proposed - cur_min) < 0.0001:
            continue
        out.append(
            {
                "id": _recommendation_id("engine_b_style", style, cur_min, proposed),
                "scope_type": "engine_b_style",
                "scope_key": style,
                "engine": "engine_b",
                "environment": "live+backtest",
                "title": f"Engine B | {style.title()}",
                "subtitle": "NAKED_ENGINE.style_profiles min_score update from live outcomes",
                "current_value": round(cur_min, 4),
                "proposed_value": round(proposed, 4),
                "delta": round(proposed - cur_min, 4),
                "direction": direction,
                "confidence": _confidence_from_count(
                    int(summary.get("pairs") or 0),
                    float(summary.get("avg_trades") or 0.0),
                ),
                "metrics": dict(summary),
                "reasons": [
                    _format_live_reason(summary, f"Engine B {style}"),
                    "This recommendation is driven by closed live trade outcomes from the Performance ledger.",
                    "Engine B score gates remain whole checklist points.",
                ],
                "requires_human_approval": True,
            }
        )
    return out


def build_threshold_recommendations(db_path: str | None = None) -> list[dict[str, Any]]:
    rows = _latest_backtest_rows(db_path=db_path)
    live_rows = _closed_live_trade_rows(db_path=db_path)
    live_a = _summarize_live_engine_a(live_rows)
    live_b = _summarize_live_engine_b(live_rows)

    recommendations = _build_engine_a_recommendations(rows)
    recommendations.extend(_build_engine_b_recommendations(rows))
    recommendations = _merge_live_evidence(recommendations, live_a, live_b)

    existing_scopes = {
        (str(rec.get("scope_type") or ""), str(rec.get("scope_key") or "")) for rec in recommendations
    }
    for rec in _build_live_engine_a_recommendations(live_a):
        key = (str(rec.get("scope_type") or ""), str(rec.get("scope_key") or ""))
        if key not in existing_scopes:
            recommendations.append(rec)
    for rec in _build_live_engine_b_recommendations(live_b):
        key = (str(rec.get("scope_type") or ""), str(rec.get("scope_key") or ""))
        if key not in existing_scopes:
            recommendations.append(rec)

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
            "requires_human_approval_to_apply": True,
            "apply_path_pattern": "/api/advisory-thresholds/{rec_id}/approve",
            "disclaimer": "Threshold changes apply only after explicit POST approval. GET endpoints are read-only.",
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
