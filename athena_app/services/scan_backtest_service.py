"""Scan/backtest request orchestration extracted from athena.py."""

from __future__ import annotations

from typing import Callable


def handle_scan_request(payload: dict, *, run_full_scan: Callable) -> dict:
    """Build scan request contract and run scan."""
    return run_full_scan(
        style=payload.get("style", "auto"),
        asset_class=payload.get("asset_class"),
        refresh_market_data=bool(payload.get("refresh_market_data", False)),
    )


def handle_backtest_request(
    payload: dict,
    *,
    normalize_style: Callable[[str], str],
    all_pairs: list,
    backtest_pair: Callable,
    run_full_backtest: Callable,
    auto_toggle_pair: Callable,
    active_pairs: list,
    allow_auto_toggle: bool,
) -> dict:
    """Run pair or full backtest with unchanged output semantics."""
    bt_style = normalize_style(payload.get("style", "auto"))
    _vm = str(payload.get("validation_mode") or "standard").strip().lower()
    if _vm not in {"", "standard"}:
        return {
            "error": "ENGINE_A_V3_VALIDATION_MODE_UNSUPPORTED",
            "status": 422,
            "validation_mode": _vm,
        }
    try:
        _pg = int(payload.get("purge_gap", 200))
    except (TypeError, ValueError):
        _pg = 200
    try:
        _fd = int(payload.get("folds", 3))
    except (TypeError, ValueError):
        _fd = 3
    sym = payload.get("symbol")
    # Clients (e.g. telegram_bot /bt) often send ``pair`` (display) not ``symbol``.
    # Without this, missing ``symbol`` triggers a full-universe leaderboard backtest.
    if not sym and payload.get("pair") is not None:
        raw = str(payload.get("pair") or "").strip()
        if raw:
            _match = next(
                (
                    p
                    for p in all_pairs
                    if p.get("display") == raw or p.get("symbol") == raw
                ),
                None,
            )
            if not _match:
                return {"error": f"Unknown pair {raw!r}", "status": 404}
            sym = _match["symbol"]

    if sym:
        if not isinstance(sym, str):
            return {"error": "Invalid symbol", "status": 400}
        pair = next((p for p in all_pairs if p["symbol"] == sym), None)
        if not pair:
            return {"error": "Unknown symbol", "status": 404}
        result = backtest_pair(
            pair,
            style=bt_style,
            validation_mode=_vm,
            purge_gap=_pg,
            folds=max(1, _fd),
        )
        if isinstance(result, dict) and result.get("error"):
            try:
                status = int(result.get("status", 400))
            except (TypeError, ValueError):
                status = 400
            if status >= 400:
                return {
                    key: value
                    for key, value in result.items()
                    if key != "data"
                }
        if allow_auto_toggle:
            toggle = auto_toggle_pair(pair, result)
            if toggle:
                result["autoToggle"] = toggle
        result["activePairs"] = len(active_pairs)
        return {"data": result, "status": 200}

    full = run_full_backtest(
        style=bt_style,
        asset_class=payload.get("asset_class"),
        validation_mode=_vm,
        purge_gap=_pg,
        folds=max(1, _fd),
    )
    toggles = []
    if allow_auto_toggle:
        for r in full.get("results", []):
            p = next((p for p in all_pairs if p["symbol"] == r.get("symbol")), None)
            if not p:
                continue
            t = auto_toggle_pair(p, r)
            if t:
                toggles.append({"pair": r["pair"], "action": t, "sqn": r["sqn"]})
    full["autoToggles"] = toggles
    full["activePairs"] = len(active_pairs)
    return {"data": full, "status": 200}

