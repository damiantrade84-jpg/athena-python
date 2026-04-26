def run_scalp_scan():
    for display in pairs_or_symbols:
        if display in _already_skipped:
            continue
        mt5_sym = None
        _funnel: dict[str, Any] = {
            "symbol": display,
            "asset_type": "",
            "source": "",
            "scalp_enabled": True,
            "called": True,
            "data_available": False,
            "candle_timeframes_available": [],
            "lower_tf_candle_count": None,
            "latest_lower_tf_candle": None,
            "freshness_status": "",
            "spread": None,
            "spread_ok": None,
            "atr": None,
            "atr_ok": None,
            "volume_available": None,
            "volume_profile_available": None,
            "poc": None,
            "vah": None,
            "val": None,
            "lvn_count": None,
            "price_near_poc": None,
            "price_near_vah": None,
            "price_near_val": None,
            "cvd_available": None,
            "cvd_bias": None,
            "absorption_detected": None,
            "vwap_available": None,
            "vwap_bias": None,
            "setup_type": None,
            "setup_direction": None,
            "setup_score": None,
            "setup_grade": None,
            "min_grade_required": None,
            "min_score_required": None,
            "rr": None,
            "rr_ok": None,
            "entry": None,
            "sl": None,
            "tp": None,
            "gate_result": "NOT_CALLED",
            "fail_reasons": [],
            "soft_warnings": [],
            "diagnostic_notes": {},
        }
        try:
            try:
                asset_type = _guess_asset_type(display)
                _funnel["asset_type"] = asset_type
                session_ok, active_session = scalp_session_window(asset_type)
                if not session_ok:
                    reason = active_session if active_session == "NY_OPEN_COOLDOWN" else "OUTSIDE_SESSION"
                    _record_stability_sample(display, asset_type, False, reason=reason)
                    skipped.append({"pair": display, "reason": reason})
                    continue

            # ── Fetch candles (crypto vs MT5) ────────────────────────────────
            _vol_src_dominant = "binance_ws" if asset_type == "crypto" else "mt5_tick"
            if asset_type == "crypto":
                pair_dict = {
                    "display": display,
                    "symbol": display.replace("/", ""),
