"""Alert-only suggested trade watch monitor — no execution paths."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai_review.suggested_trade_plan import (
    allocate_watch_expiry_seconds,
    is_watchable_plan,
    plan_playbook_warnings,
    resolve_watch_reason,
    sanitize_suggested_trade_plan,
)

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

_runner_lock = threading.Lock()
_runner_thread: threading.Thread | None = None
_runner_started = False
_runner_last_tick_at: str | None = None
_runner_last_error: str | None = None


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
    playbook_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def monitor_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    base = {
        "ENABLED": True,
        "ALERT_ONLY": True,
        "RUNNER_ENABLED": True,
        "POLL_SECONDS": 5,
        "DEFAULT_EXPIRY_SECONDS": 1800,
        "EXPIRY_BARS": 4,
        "EXPIRY_MIN_SECONDS": 900,
        "EXPIRY_MAX_SECONDS": 259200,
        "EXPIRY_MAX_SECONDS_BY_STYLE": {
            "scalp": 7200,
            "intraday": 28800,
            "swing": 259200,
        },
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
    signal = payload.get("signal") if isinstance(payload.get("signal"), dict) else {}
    summary = payload.get("aiReviewSummary") or payload.get("ai_review_summary") or {}
    if not isinstance(summary, dict):
        summary = {}
    review = payload.get("aiReview") or payload.get("ai_review") or {}
    if not isinstance(review, dict):
        review = {}
    reason = resolve_watch_reason(plan=plan, summary=summary, review=review)
    if reason:
        plan["reason"] = reason
        summary = {**summary, "finalReason": reason}

    expires_sec = plan.get("expiresInSeconds")
    if not expires_sec:
        style = str(
            signal.get("horizon")
            or signal.get("style")
            or plan.get("style")
            or ""
        )
        expires_sec = allocate_watch_expiry_seconds(plan, style=style, cfg=mcfg)
        plan["expiresInSeconds"] = expires_sec

    return {
        "symbol": symbol,
        "display": str(payload.get("display") or symbol),
        "signal": signal,
        "suggested_trade_plan": plan,
        "ai_review_summary": summary,
        "source": source,
        "created_at": str(payload.get("createdAt") or payload.get("created_at") or _utc_now_iso()),
        "expires_sec": int(expires_sec),
        "notes": reason or None,
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
        notes=validated.get("notes") or plan.get("reason"),
        playbook_warnings=plan_playbook_warnings(plan),
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


def clear_terminal_watches(
    *,
    active_path: Path | None = None,
    events_path: Path | None = None,
) -> dict[str, Any]:
    """Remove terminal watches (EXPIRED/CANCELLED/INVALIDATED) from active.json."""
    terminal = ("EXPIRED", "CANCELLED", "INVALIDATED")
    watches = load_active_watches(active_path)
    kept = [w for w in watches if str(w.get("status")) not in terminal]
    removed_ids = [str(w.get("watch_id")) for w in watches if str(w.get("status")) in terminal]
    if removed_ids:
        save_active_watches(kept, active_path)
        append_event(events_path or DEFAULT_EVENTS_PATH, {
            "type": "watches_cleared",
            "watch_ids": removed_ids,
            "at": _utc_now_iso(),
        })
    return {"removed": len(removed_ids), "removed_ids": removed_ids, "remaining": len(kept)}


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


def evaluate_invalidation(
    watch: dict[str, Any],
    *,
    latest_close: float | None = None,
    live_price: float | None = None,
) -> tuple[bool, str | None]:
    """Check the plan's own invalidateAbove/invalidateBelow anchors.

    Deterministic: uses only levels the AI plan stamped at flag time. Close
    takes precedence over live price, mirroring evaluate_trigger.
    """
    plan = watch.get("suggested_trade_plan")
    if not isinstance(plan, dict):
        plan = watch
    inv_above = plan.get("invalidateAbove") if "invalidateAbove" in plan else plan.get("invalidate_above")
    inv_below = plan.get("invalidateBelow") if "invalidateBelow" in plan else plan.get("invalidate_below")

    price = latest_close if latest_close is not None else live_price
    if price is None:
        return False, None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return False, None

    if inv_below is not None:
        try:
            below = float(inv_below)
        except (TypeError, ValueError):
            below = None
        if below is not None and price < below:
            return True, f"price below invalidation {below}"
    if inv_above is not None:
        try:
            above = float(inv_above)
        except (TypeError, ValueError):
            above = None
        if above is not None and price > above:
            return True, f"price above invalidation {above}"
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

        invalidated, inv_note = evaluate_invalidation(
            watch,
            latest_close=latest_close,
            live_price=live_price,
        )
        if invalidated:
            watch["status"] = "INVALIDATED"
            if inv_note:
                watch["notes"] = inv_note
            updated += 1
            append_event(events_path or DEFAULT_EVENTS_PATH, {
                "type": "watch_invalidated",
                "watch_id": watch.get("watch_id"),
                "symbol": symbol,
                "note": inv_note,
                "at": _utc_now_iso(),
            })
            continue

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


def count_watches_by_status(watches: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "active": 0,
        "ready": 0,
        "expired": 0,
        "cancelled": 0,
        "invalidated": 0,
    }
    for watch in watches:
        status = str(watch.get("status") or "").upper()
        if status in ("FLAGGED", "WATCHING", "LEVEL_REACHED"):
            counts["active"] += 1
        elif status == "READY_FOR_REVIEW":
            counts["ready"] += 1
        elif status == "EXPIRED":
            counts["expired"] += 1
        elif status == "CANCELLED":
            counts["cancelled"] += 1
        elif status == "INVALIDATED":
            counts["invalidated"] += 1
    return counts


def mark_runner_tick(*, error: str | None = None) -> None:
    global _runner_last_tick_at, _runner_last_error
    with _runner_lock:
        _runner_last_tick_at = _utc_now_iso()
        _runner_last_error = error


def get_runner_status(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    mcfg = monitor_config(cfg)
    poll_seconds = int(mcfg.get("POLL_SECONDS", 5) or 5)
    monitor_enabled = bool(mcfg.get("ENABLED", True))
    runner_enabled = bool(mcfg.get("RUNNER_ENABLED", True))

    with _runner_lock:
        last_tick = _runner_last_tick_at
        last_error = _runner_last_error
        thread_alive = _runner_thread is not None and _runner_thread.is_alive()

    last_tick_dt = _parse_ts(last_tick)
    last_tick_age: float | None = None
    if last_tick_dt is not None:
        last_tick_age = max(0.0, (datetime.now(timezone.utc) - last_tick_dt).total_seconds())

    if not monitor_enabled:
        mode = "manual_only"
        active = False
    elif not runner_enabled:
        mode = "frontend_poll"
        active = False
    elif thread_alive:
        mode = "background_thread"
        active = True
    else:
        mode = "frontend_poll"
        active = False

    next_poll: int | None = None
    if last_tick_age is not None:
        next_poll = max(0, poll_seconds - int(last_tick_age))

    return {
        "enabled": monitor_enabled,
        "alertOnly": True,
        "mode": mode,
        "active": active,
        "lastTickAt": last_tick,
        "lastTickAgeSeconds": last_tick_age,
        "nextPollSeconds": next_poll,
        "pollSeconds": poll_seconds,
        "error": last_error,
    }


def evaluate_all_watches_once(
    *,
    cfg: dict[str, Any] | None = None,
    active_path: Path | None = None,
    events_path: Path | None = None,
    fetch_candles_fn: Callable[..., Any] | None = None,
    live_prices: dict[str, Any] | None = None,
    live_prices_lock: Any | None = None,
) -> dict[str, Any]:
    try:
        result = evaluate_watches(
            cfg=cfg,
            active_path=active_path,
            events_path=events_path,
            fetch_candles_fn=fetch_candles_fn,
            live_prices=live_prices,
            live_prices_lock=live_prices_lock,
        )
        mark_runner_tick()
        return result
    except Exception as exc:
        mark_runner_tick(error=str(exc))
        raise


def _runner_loop(
    cfg: dict[str, Any] | None,
    fetch_candles_fn: Callable[..., Any] | None,
    live_prices: dict[str, Any] | None,
    live_prices_lock: Any | None,
    active_path: Path,
    events_path: Path,
) -> None:
    while True:
        mcfg = monitor_config(cfg)
        if not mcfg.get("ENABLED", True) or not mcfg.get("RUNNER_ENABLED", True):
            break
        poll_seconds = max(1, int(mcfg.get("POLL_SECONDS", 5) or 5))
        try:
            evaluate_all_watches_once(
                cfg=cfg,
                active_path=active_path,
                events_path=events_path,
                fetch_candles_fn=fetch_candles_fn,
                live_prices=live_prices,
                live_prices_lock=live_prices_lock,
            )
        except Exception as exc:
            mark_runner_tick(error=str(exc))
        time.sleep(poll_seconds)


def start_suggested_trade_runner_once(
    *,
    cfg: dict[str, Any] | None = None,
    fetch_candles_fn: Callable[..., Any] | None = None,
    live_prices: dict[str, Any] | None = None,
    live_prices_lock: Any | None = None,
    active_path: Path | None = None,
    events_path: Path | None = None,
) -> bool:
    global _runner_thread, _runner_started
    mcfg = monitor_config(cfg)
    if not mcfg.get("ENABLED", True) or not mcfg.get("RUNNER_ENABLED", True):
        return False

    with _runner_lock:
        if _runner_started and _runner_thread is not None and _runner_thread.is_alive():
            return True
        _runner_started = True
        _runner_thread = threading.Thread(
            target=_runner_loop,
            args=(
                cfg,
                fetch_candles_fn,
                live_prices,
                live_prices_lock,
                active_path or DEFAULT_ACTIVE_PATH,
                events_path or DEFAULT_EVENTS_PATH,
            ),
            name="suggested-trade-monitor",
            daemon=True,
        )
        _runner_thread.start()
    return True
