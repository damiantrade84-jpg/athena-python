"""Alert-only suggested trade watch monitor — no execution paths."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai_review.suggested_trade_plan import is_watchable_plan, sanitize_suggested_trade_plan

WATCH_STATUSES = frozenset({
    "FLAGGED",
    "WATCHING",
    "LEVEL_REACHED",
    "READY_FOR_REVIEW",
    "EXPIRED",
    "CANCELLED",
    "INVALIDATED",
})

DEFAULT_ACTIVE_PATH = Path("logs/suggested_trades/active.json")
DEFAULT_EVENTS_PATH = Path("logs/suggested_trades/events.jsonl")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class SuggestedTrade:
    watch_id: str
    symbol: str
    display: str
    direction: str
    action: str
    trigger_type: str
    status: str
    source: str
    created_at: str
    suggested_trade_plan: dict[str, Any]
    signal: dict[str, Any] = field(default_factory=dict)
    ai_review_summary: dict[str, Any] = field(default_factory=dict)
    level: float | None = None
    zone_low: float | None = None
    zone_high: float | None = None
    entry_tf: str | None = None
    context_tf: str | None = None
    execution_tf: str | None = None
    expires_at: str | None = None
    reached_at: str | None = None
    cancelled_at: str | None = None
    last_evaluated_at: str | None = None
    last_price: float | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def monitor_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    base = {
        "ENABLED": True,
        "ALERT_ONLY": True,
        "POLL_SECONDS": 5,
        "DEFAULT_EXPIRY_SECONDS": 1800,
        "MAX_ACTIVE_WATCHES": 25,
    }
    raw = (cfg or {}).get("SUGGESTED_TRADE_MONITOR") or {}
    if isinstance(raw, dict):
        base.update(raw)
    base["ALERT_ONLY"] = True  # hard lock — never allow execution from monitor
    return base


def validate_flag_payload(payload: dict[str, Any], cfg: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(payload, dict):
        return None, "payload must be object"
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not symbol:
        return None, "symbol required"

    plan_raw = payload.get("suggestedTradePlan") or payload.get("suggested_trade_plan")
    if not isinstance(plan_raw, dict):
        return None, "suggestedTradePlan required"

    source = str(payload.get("source") or plan_raw.get("source") or "ai_chart_review")
    plan = sanitize_suggested_trade_plan(plan_raw, source=source, symbol=symbol)
    if not plan:
        return None, "invalid suggestedTradePlan"

    action = str(plan.get("action") or "").upper()
    if action in ("ENTRY_NOW", "NO_TRADE"):
        return None, f"action {action} cannot be watched"
    if not is_watchable_plan(plan):
        return None, "plan is not watchable (malformed or not armable)"

    mcfg = monitor_config(cfg)
    expires_sec = plan.get("expiresInSeconds")
    if not expires_sec:
        expires_sec = mcfg["DEFAULT_EXPIRY_SECONDS"]
        plan["expiresInSeconds"] = expires_sec

    return {
        "symbol": symbol,
        "display": str(payload.get("display") or symbol),
        "signal": payload.get("signal") if isinstance(payload.get("signal"), dict) else {},
        "suggested_trade_plan": plan,
        "ai_review_summary": payload.get("aiReviewSummary") or payload.get("ai_review_summary") or {},
        "source": source,
        "created_at": str(payload.get("createdAt") or payload.get("created_at") or _utc_now_iso()),
        "expires_sec": int(expires_sec),
    }, None


def build_watch_from_flag(validated: dict[str, Any]) -> SuggestedTrade:
    plan = validated["suggested_trade_plan"]
    created = validated["created_at"]
    expires_sec = validated["expires_sec"]
    created_dt = _parse_ts(created) or datetime.now(timezone.utc)
    from datetime import timedelta
    expires_at = (created_dt + timedelta(seconds=expires_sec)).replace(microsecond=0).isoformat()

    level = plan.get("level")
    zone_low = plan.get("zoneLow")
    zone_high = plan.get("zoneHigh")

    return SuggestedTrade(
        watch_id=str(uuid.uuid4()),
        symbol=validated["symbol"],
        display=validated["display"],
        direction=str(plan.get("direction") or "LONG"),
        action=str(plan.get("action") or "WAIT_FOR_LEVEL"),
        trigger_type=str(plan.get("triggerType") or "ACCEPTANCE_ABOVE"),
        status="WATCHING",
        source=str(validated.get("source") or "ai_chart_review"),
        created_at=created,
        suggested_trade_plan=plan,
        signal=validated.get("signal") or {},
        ai_review_summary=validated.get("ai_review_summary") or {},
        level=float(level) if level is not None else None,
        zone_low=float(zone_low) if zone_low is not None else None,
        zone_high=float(zone_high) if zone_high is not None else None,
        entry_tf=plan.get("entryTf"),
        context_tf=plan.get("contextTf"),
        execution_tf=plan.get("executionTf"),
        expires_at=expires_at,
    )


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, default=str) + "\n")


def load_active_watches(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or DEFAULT_ACTIVE_PATH
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(raw, dict) and isinstance(raw.get("watches"), list):
        return [w for w in raw["watches"] if isinstance(w, dict)]
    if isinstance(raw, list):
        return [w for w in raw if isinstance(w, dict)]
    return []


def save_active_watches(watches: list[dict[str, Any]], path: Path | None = None) -> None:
    p = path or DEFAULT_ACTIVE_PATH
    atomic_write_json(p, {"watches": watches, "updated_at": _utc_now_iso()})


def add_watch(
    validated: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    active_path: Path | None = None,
    events_path: Path | None = None,
) -> tuple[SuggestedTrade | None, str | None]:
    mcfg = monitor_config(cfg)
    if not mcfg.get("ENABLED", True):
        return None, "suggested trade monitor disabled"

    watches = load_active_watches(active_path)
    active = [w for w in watches if str(w.get("status")) in ("FLAGGED", "WATCHING", "LEVEL_REACHED", "READY_FOR_REVIEW")]
    if len(active) >= int(mcfg.get("MAX_ACTIVE_WATCHES", 25)):
        return None, "max active watches reached"

    watch = build_watch_from_flag(validated)
    watches.append(watch.to_dict())
    save_active_watches(watches, active_path)
    append_event(events_path or DEFAULT_EVENTS_PATH, {
        "type": "watch_flagged",
        "watch_id": watch.watch_id,
        "symbol": watch.symbol,
        "status": watch.status,
        "at": _utc_now_iso(),
    })
    return watch, None


def cancel_watch(
    watch_id: str,
    *,
    active_path: Path | None = None,
    events_path: Path | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    watches = load_active_watches(active_path)
    found = None
    for w in watches:
        if str(w.get("watch_id")) == watch_id:
            found = w
            break
    if not found:
        return None, "watch not found"
    if str(found.get("status")) in ("CANCELLED", "EXPIRED"):
        return found, None
    found["status"] = "CANCELLED"
    found["cancelled_at"] = _utc_now_iso()
    save_active_watches(watches, active_path)
    append_event(events_path or DEFAULT_EVENTS_PATH, {
        "type": "watch_cancelled",
        "watch_id": watch_id,
        "at": _utc_now_iso(),
    })
    return found, None


def _latest_close(candles: list[dict[str, Any]] | None) -> float | None:
    if not candles:
        return None
    last = candles[-1]
    for key in ("c", "close", "Close"):
        val = last.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def _candle_touches_zone(candle: dict[str, Any], zone_low: float, zone_high: float) -> bool:
    try:
        low = float(candle.get("l") or candle.get("low") or candle.get("Low"))
        high = float(candle.get("h") or candle.get("high") or candle.get("High"))
    except (TypeError, ValueError):
        return False
    return high >= zone_low and low <= zone_high


def evaluate_trigger(
    watch: dict[str, Any],
    *,
    latest_close: float | None = None,
    latest_candle: dict[str, Any] | None = None,
    live_price: float | None = None,
) -> tuple[bool, str | None]:
    trigger = str(watch.get("trigger_type") or watch.get("triggerType") or "").upper()
    level = watch.get("level")
    zone_low = watch.get("zone_low") if "zone_low" in watch else watch.get("zoneLow")
    zone_high = watch.get("zone_high") if "zone_high" in watch else watch.get("zoneHigh")

    price = latest_close if latest_close is not None else live_price

    if trigger == "ACCEPTANCE_ABOVE":
        if price is None or level is None:
            return False, None
        if price > float(level):
            return True, "close above level"
        return False, None
    if trigger == "ACCEPTANCE_BELOW":
        if price is None or level is None:
            return False, None
        if price < float(level):
            return True, "close below level"
        return False, None

    if trigger in ("PULLBACK_TO_ZONE", "REJECTION_FROM_ZONE", "SWEEP_RECLAIM"):
        if zone_low is None or zone_high is None:
            return False, None
        zl, zh = float(zone_low), float(zone_high)
        if latest_candle and _candle_touches_zone(latest_candle, zl, zh):
            return True, "price entered zone"
        if price is not None and zl <= price <= zh:
            return True, "live price in zone"
        return False, None

    return False, None


def evaluate_watches(
    *,
    cfg: dict[str, Any] | None = None,
    active_path: Path | None = None,
    events_path: Path | None = None,
    fetch_candles_fn: Callable[..., Any] | None = None,
    live_prices: dict[str, Any] | None = None,
    live_prices_lock: Any | None = None,
) -> dict[str, Any]:
    """Deterministic alert-only evaluation. Never calls execution."""
    mcfg = monitor_config(cfg)
    if not mcfg.get("ENABLED", True):
        return {"evaluated": 0, "updated": 0, "reason": "disabled"}

    watches = load_active_watches(active_path)
    now = datetime.now(timezone.utc)
    updated = 0
    evaluated = 0

    for watch in watches:
        status = str(watch.get("status") or "")
        if status in ("CANCELLED", "EXPIRED", "INVALIDATED"):
            continue
        if status in ("LEVEL_REACHED", "READY_FOR_REVIEW"):
            continue

        evaluated += 1
        watch["last_evaluated_at"] = _utc_now_iso()

        expires_at = _parse_ts(watch.get("expires_at"))
        if expires_at and now >= expires_at:
            watch["status"] = "EXPIRED"
            updated += 1
            append_event(events_path or DEFAULT_EVENTS_PATH, {
                "type": "watch_expired",
                "watch_id": watch.get("watch_id"),
                "at": _utc_now_iso(),
            })
            continue

        symbol = str(watch.get("symbol") or "")
        entry_tf = str(watch.get("entry_tf") or watch.get("execution_tf") or "M5").upper()
        latest_close = None
        latest_candle = None

        if fetch_candles_fn and symbol:
            try:
                result = fetch_candles_fn(symbol, entry_tf, limit=5)
                candles = None
                if isinstance(result, dict):
                    candles = result.get("candles")
                elif isinstance(result, (list, tuple)):
                    candles = result[0] if result else None
                if isinstance(candles, list) and candles:
                    latest_candle = candles[-1] if isinstance(candles[-1], dict) else None
                    latest_close = _latest_close(candles)
            except Exception:
                pass

        live_price = None
        if live_prices is not None:
            if live_prices_lock is not None:
                with live_prices_lock:
                    entry = live_prices.get(symbol) or live_prices.get(symbol.upper())
            else:
                entry = live_prices.get(symbol) or live_prices.get(symbol.upper())
            if isinstance(entry, dict):
                live_price = entry.get("price")
            elif isinstance(entry, (int, float)):
                live_price = float(entry)

        if live_price is not None:
            watch["last_price"] = live_price
        elif latest_close is not None:
            watch["last_price"] = latest_close

        reached, note = evaluate_trigger(
            watch,
            latest_close=latest_close,
            latest_candle=latest_candle,
            live_price=live_price,
        )
        if reached:
            watch["status"] = "READY_FOR_REVIEW"
            watch["reached_at"] = _utc_now_iso()
            if note:
                watch["notes"] = note
            updated += 1
            append_event(events_path or DEFAULT_EVENTS_PATH, {
                "type": "level_reached",
                "watch_id": watch.get("watch_id"),
                "symbol": symbol,
                "note": note,
                "at": _utc_now_iso(),
            })

    if updated:
        save_active_watches(watches, active_path)

    return {"evaluated": evaluated, "updated": updated, "alert_only": True}
