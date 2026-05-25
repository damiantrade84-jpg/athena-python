"""Stop-loss diagnostic helpers for open positions (read-only)."""

from __future__ import annotations

from typing import Any, Callable


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_pair_key(pair: str | None) -> str:
    if not pair:
        return ""
    upper = str(pair).upper().replace(" ", "").replace("-", "/")
    if ":" in upper:
        upper = upper.split(":")[0]
    if "/" not in upper and upper.endswith("USDT") and len(upper) > 4:
        upper = f"{upper[:-4]}/USDT"
    return upper


def pairs_match(left: str | None, right: str | None) -> bool:
    a = normalize_pair_key(left)
    b = normalize_pair_key(right)
    if not a or not b:
        return False
    if a == b:
        return True
    return a.split("/")[0] == b.split("/")[0] and ("USDT" in a or "USDT" in b)


def current_r_multiple(
    direction: str,
    entry: float,
    risk_sl: float,
    mark: float,
) -> float | None:
    risk_dist = abs(entry - risk_sl)
    if risk_dist <= 0 or entry <= 0 or mark <= 0:
        return None
    direction = str(direction or "LONG").upper()
    if direction == "SHORT":
        return (entry - mark) / risk_dist
    return (mark - entry) / risk_dist


def pct_remaining_to_sl(direction: str, mark: float, sl: float) -> float | None:
    if mark <= 0 or sl <= 0:
        return None
    direction = str(direction or "LONG").upper()
    if direction == "SHORT":
        if sl <= mark:
            return 0.0
        return (sl - mark) / mark * 100.0
    if mark <= sl:
        return 0.0
    return (mark - sl) / mark * 100.0


def sl_alignment(audit_sl: float, broker_sl: float, entry: float) -> dict[str, Any]:
    audit_sl = _safe_float(audit_sl)
    broker_sl = _safe_float(broker_sl)
    entry = _safe_float(entry)
    if audit_sl <= 0 and broker_sl <= 0:
        return {
            "audit_sl": audit_sl or None,
            "broker_sl": broker_sl or None,
            "delta_abs": None,
            "delta_pct_of_entry": None,
            "match": False,
            "warning": "NO_SL_ON_RECORD",
        }
    ref = broker_sl if broker_sl > 0 else audit_sl
    other = audit_sl if broker_sl > 0 else broker_sl
    delta_abs = abs(ref - other) if ref > 0 and other > 0 else None
    delta_pct = (delta_abs / entry * 100.0) if delta_abs is not None and entry > 0 else None
    match = delta_abs is not None and delta_abs <= max(entry * 0.0005, 1e-8)
    warning = None
    if broker_sl <= 0:
        warning = "BROKER_SL_MISSING"
    elif audit_sl <= 0:
        warning = "AUDIT_SL_MISSING"
    elif not match:
        warning = "AUDIT_BROKER_SL_MISMATCH"
    return {
        "audit_sl": audit_sl or None,
        "broker_sl": broker_sl or None,
        "delta_abs": round(delta_abs, 8) if delta_abs is not None else None,
        "delta_pct_of_entry": round(delta_pct, 4) if delta_pct is not None else None,
        "match": match,
        "warning": warning,
    }


def primary_exit_while_in_loss(
    *,
    current_r: float | None,
    activation_r: float,
    broker_sl: float,
    in_profit: bool,
) -> dict[str, Any]:
    trail_active = (
        current_r is not None
        and activation_r > 0
        and current_r >= activation_r
    )
    if in_profit or trail_active:
        primary = "trail_or_tp"
        summary = (
            "In profit or past trail activation — exit may be trail ratchet, "
            "peak giveback, or broker TP."
        )
    elif broker_sl > 0:
        primary = "broker_sl"
        summary = (
            f"While losing and below trail activation ({activation_r:.2f}R), "
            f"position auto-closes when mark price hits broker stopLoss."
        )
    else:
        primary = "unknown"
        summary = "No broker stopLoss detected — manual intervention required."
    return {
        "primary": primary,
        "trail_active": trail_active,
        "summary": summary,
    }


def build_position_sl_diagnostic(
    *,
    position: dict[str, Any] | None,
    audit: dict[str, Any] | None,
    tcfg: dict[str, Any],
    style: str,
    venue: str,
    recomputed_levels: dict[str, Any] | None = None,
    max_sl_pct: float | None = None,
) -> dict[str, Any]:
    pos = position or {}
    aud = audit or {}
    direction = str(pos.get("direction") or aud.get("direction") or "LONG").upper()
    entry = _safe_float(pos.get("entry") or aud.get("entry_price"))
    audit_sl = _safe_float(aud.get("sl"))
    broker_sl = _safe_float(pos.get("sl"))
    broker_tp = _safe_float(pos.get("tp") or aud.get("tp"))
    mark = _safe_float(
        pos.get("markPrice") or pos.get("lastPrice") or pos.get("mark") or 0
    )
    profit = _safe_float(pos.get("profit") or pos.get("unrealizedPnl"))
    pair = str(pos.get("pair") or pos.get("symbol") or aud.get("pair") or "")
    resolved_style = str(style or aud.get("style") or "swing").lower()
    if resolved_style not in ("scalp", "intraday", "swing"):
        resolved_style = "swing"

    from timed_exit_monitor import _activation_r_for, _risk_sl_for_activation

    activation_r = _activation_r_for(tcfg, resolved_style)
    risk_sl = _risk_sl_for_activation(audit_sl, broker_sl)
    sl_pct_from_entry = (
        abs(entry - risk_sl) / entry * 100.0 if entry > 0 and risk_sl > 0 else None
    )
    cur_r = current_r_multiple(direction, entry, risk_sl, mark) if mark > 0 else None
    pct_to_sl = pct_remaining_to_sl(direction, mark, broker_sl or risk_sl) if mark > 0 else None
    closes_at = broker_sl if broker_sl > 0 else (risk_sl if risk_sl > 0 else None)
    align = sl_alignment(audit_sl, broker_sl, entry)
    exit_info = primary_exit_while_in_loss(
        current_r=cur_r,
        activation_r=activation_r,
        broker_sl=broker_sl or risk_sl,
        in_profit=profit > 0,
    )

    recomputed_block: dict[str, Any] | None = None
    if recomputed_levels:
        expected_sl = _safe_float(recomputed_levels.get("sl"))
        atr = _safe_float(recomputed_levels.get("atr"))
        sl_mult = recomputed_levels.get("sl_mult")
        regime_factor = recomputed_levels.get("regime_factor")
        expected_pct = (
            abs(entry - expected_sl) / entry * 100.0
            if entry > 0 and expected_sl > 0
            else None
        )
        cap = max_sl_pct if max_sl_pct is not None else 0.08
        recomputed_block = {
            "expected_sl": expected_sl or None,
            "atr": atr or None,
            "sl_mult": sl_mult,
            "regime_factor": regime_factor,
            "expected_sl_pct": round(expected_pct, 4) if expected_pct is not None else None,
            "max_sl_pct": cap,
            "within_cap": expected_pct is None or expected_pct <= cap * 100.0 + 1e-6,
            "actual_sl_pct": round(sl_pct_from_entry, 4) if sl_pct_from_entry is not None else None,
        }

    sl_distance_line = None
    if closes_at is not None and mark > 0:
        if pct_to_sl is not None:
            sl_distance_line = (
                f"Closes at SL {closes_at:.6f} · {pct_to_sl:.2f}% below mark · "
                f"current R {cur_r:.2f}" if cur_r is not None else
                f"Closes at SL {closes_at:.6f} · {pct_to_sl:.2f}% below mark"
            )
        else:
            sl_distance_line = f"Closes at SL {closes_at:.6f}"

    return {
        "pair": pair,
        "direction": direction,
        "style": resolved_style,
        "engine": aud.get("engine"),
        "venue": venue,
        "audit": {
            "id": aud.get("id"),
            "ts": aud.get("ts"),
            "ticket": aud.get("ticket"),
            "entry": entry or None,
            "sl": audit_sl or None,
            "tp": _safe_float(aud.get("tp")) or None,
            "risk_amount": aud.get("risk_amount"),
            "risk_pct": aud.get("risk_pct"),
        },
        "broker": {
            "entry": entry or None,
            "sl": broker_sl or None,
            "tp": broker_tp or None,
            "mark": mark or None,
            "profit": profit,
            "ticket": pos.get("ticket"),
        },
        "sl_alignment": align,
        "recomputed": recomputed_block,
        "distance": {
            "risk_sl": risk_sl or None,
            "risk_pct_from_entry": round(sl_pct_from_entry, 4) if sl_pct_from_entry is not None else None,
            "current_r": round(cur_r, 4) if cur_r is not None else None,
            "pct_remaining_to_sl": round(pct_to_sl, 4) if pct_to_sl is not None else None,
            "closes_at_price": closes_at,
            "display_line": sl_distance_line,
        },
        "exit_policy": {
            "trail_activation_r": activation_r,
            "timed_tp_mode": str(tcfg.get("tp_mode", "trailing_atr")),
            "primary_exit_in_loss": exit_info["primary"],
            "trail_active": exit_info["trail_active"],
            "summary": exit_info["summary"],
        },
    }


def enrich_open_trade_with_sl_diagnostic(
    trade_row: dict[str, Any],
    audit: dict[str, Any] | None,
    tcfg: dict[str, Any],
    *,
    recompute_levels_fn: Callable[[dict[str, Any], str], dict[str, Any] | None] | None = None,
    max_sl_pct: float | None = None,
) -> dict[str, Any]:
    """Attach sl_diagnostic fields to an open-trades-timed row."""
    style = str(trade_row.get("style") or (audit or {}).get("style") or "swing")
    venue = str(trade_row.get("exchange") or "mt5")
    position = {
        "pair": trade_row.get("pair"),
        "direction": trade_row.get("direction"),
        "entry": trade_row.get("entry"),
        "sl": trade_row.get("sl"),
        "tp": trade_row.get("tp"),
        "profit": trade_row.get("profit"),
        "ticket": trade_row.get("ticket"),
        "markPrice": trade_row.get("mark") or trade_row.get("markPrice"),
    }
    recomputed = None
    if recompute_levels_fn and audit:
        try:
            recomputed = recompute_levels_fn(audit, style)
        except Exception:
            recomputed = None
    diag = build_position_sl_diagnostic(
        position=position,
        audit=audit,
        tcfg=tcfg,
        style=style,
        venue=venue,
        recomputed_levels=recomputed,
        max_sl_pct=max_sl_pct,
    )
    trade_row["sl_diagnostic"] = diag
    trade_row["sl_close_line"] = diag.get("distance", {}).get("display_line")
    trade_row["closes_at_sl"] = diag.get("distance", {}).get("closes_at_price")
    trade_row["pct_to_sl"] = diag.get("distance", {}).get("pct_remaining_to_sl")
    trade_row["current_r"] = diag.get("distance", {}).get("current_r")
    trade_row["exit_summary"] = diag.get("exit_policy", {}).get("summary")
    return trade_row
