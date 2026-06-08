def backtest_pair_scalp(pair: dict, validation_mode: str = "standard") -> dict | None:
    """Backtest Engine D scalp setups with a stable M15 execution proxy.

    Walk-forward approach:
      1. Fetch M15 structure candles
      2. At each closed M15 bar: build VP context, then run the Engine D setup pipeline
      3. If setup valid: enter at the next M15 bar, resolve SL/TP on M15 bars
      4. Collect trades, compute stats using _format_backtest_results()

    Live Engine D still executes on M1/M5. The backtest keeps the M15 proxy until
    lower-timeframe historical execution has been validated against known-good runs.
    """
    from scalp_engine import (
        _build_volume_profile,
        _build_trade_bucket_volume_profile,
        _check_aaa_sequence,
        _check_absorption,
        _check_cvd,
        _check_trade_bucket_cvd,
        _check_vwap_lean,
        _classify_market_state,
        _classify_setup,
        _coerce_utc_datetime,
        _guess_asset_type,
        _locate_price_vs_vp,
        _overlay_eodhd_volume_for_scalp,
        get_grade_sessions_for_mode,
        infer_bias_from_ema_stack,
        scalp_session_window,
        ai_quality_grade as _ai_quality_grade,
    )

    display = pair.get("display", pair.get("symbol", "UNKNOWN"))
    asset_type = pair.get("type", _guess_asset_type(display))
    cfg = CONFIG.get("SCALP_ENGINE", {})

    if not cfg.get("BT_ENABLED", True):
        return {"error": f"Scalp backtest disabled in config for {display}"}

    walk_bars = int(cfg.get("BT_WALK_BARS", 12))
    max_concurrent = int(cfg.get("BT_MAX_CONCURRENT", 2))
    min_rr = float(cfg.get("MIN_RR", 1.2))
    vp_bins = int(cfg.get("VP_BINS", 64))
    abs_vol_mult = float(cfg.get("ABSORPTION_VOL_MULT", 2.0))
    slippage_ticks = int(cfg.get("BT_SLIPPAGE_TICKS", 3))
    _grade_order = ["A", "B", "C", "D"]
    _min_grade_str = str(cfg.get("MIN_GRADE_AUTO_EXECUTE", cfg.get("MIN_GRADE", "C"))).upper()
    _min_grade_idx = _grade_order.index(_min_grade_str) if _min_grade_str in _grade_order else 2
    scratch_enabled = bool(cfg.get("BT_SCRATCH_ENABLED", True))
    scratch_bars = max(1, int(cfg.get("BT_SCRATCH_BARS", 3)))
    scratch_min_r = max(0.0, float(cfg.get("BT_SCRATCH_MIN_R", 0.10)))

    log.info(f"[SCALP-BT] Starting backtest for {display} (type={asset_type})")

    # ── Fetch M15 candles ───────────────────────────────────────────────────
    try:
        if asset_type == "crypto":
            from scalp_engine import _scalp_fetch_candles
            pair_dict = {
                "display": display, "symbol": display.replace("/", ""),
                "type": "crypto", "source": "binance",
            }
            m15_raw = _scalp_fetch_candles(pair_dict, "M15", 2000)
        else:
            from scalp_engine import mt5_fetch_scalp_candles
            from mt5_executor import mt5_map_symbol
            mt5_sym = mt5_map_symbol(display)
            if not mt5_sym:
                return {"error": f"No MT5 symbol mapping for {display}"}
            m15_raw = mt5_fetch_scalp_candles(mt5_sym, "M15", 2000, include_forming=False)
            m15_raw, _ = _overlay_eodhd_volume_for_scalp(display, asset_type, "M15", m15_raw, live=False)
    except Exception as e:
        log.error(f"[SCALP-BT] Candle fetch failed for {display}: {e}")
        return {"error": f"Candle fetch failed: {e}"}

    if not m15_raw or len(m15_raw) < 100:
        return {"error": f"Insufficient M15 data for {display}: {len(m15_raw) if m15_raw else 0} bars (need 100+)"}

    def _normalize_bt_candles(raw: list | None, tf_minutes: int) -> list:
        out = []
        for seq, c in enumerate(raw or []):
            dt = _coerce_utc_datetime(c.get("time"))
            row = {
                "time": c.get("time"),
                "open": float(c.get("open", 0)),
                "high": float(c.get("high", 0)),
                "low": float(c.get("low", 0)),
                "close": float(c.get("close", 0)),
                "vol": float(c.get("vol", 0)),
                "_seq": seq,
                "_dt": dt,
                "_close_dt": (dt + timedelta(minutes=tf_minutes)) if dt else None,
            }
            out.append(row)
        return out

    # Normalize candles to ensure float types and attach optional bar timestamps.
    candles = _normalize_bt_candles(m15_raw, 15)

    def _resample_closed_m15_context(context: list, tf: str) -> list:
        tf_norm = str(tf or "M15").upper()
        factor_map = {"M15": 1, "H1": 4, "H4": 16}
        factor = factor_map.get(tf_norm)
        if factor is None:
            log.warning("[SCALP-BT] Unsupported BIAS_TIMEFRAME=%s; falling back to M15 bias", tf_norm)
            factor = 1
        if factor <= 1:
            return context
        closed_len = (len(context) // factor) * factor
        out = []
        for start in range(0, closed_len, factor):
            chunk = context[start:start + factor]
            if len(chunk) < factor:
                continue
            first = chunk[0]
            last = chunk[-1]
            out.append({
                "time": first.get("time"),
                "open": first["open"],
                "high": max(c["high"] for c in chunk),
                "low": min(c["low"] for c in chunk),
                "close": last["close"],
                "vol": sum(float(c.get("vol", 0) or 0) for c in chunk),
                "_seq": len(out),
                "_dt": first.get("_dt"),
                "_close_dt": last.get("_close_dt"),
            })
        return out

    bias_tf = str(cfg.get("BIAS_TIMEFRAME", "H1")).upper()

    # ── Volume quality check ────────────────────────────────────────────────
    raw_vols = [c["vol"] for c in candles]
    nonzero_vols = [v for v in raw_vols if v > 0]
    vol_quality_pct = round(len(nonzero_vols) / len(candles) * 100, 1) if candles else 0
    if vol_quality_pct < 30:
        log.warning(
            f"[SCALP-BT] {display}: LOW VOLUME QUALITY — only {vol_quality_pct}% of "
            f"M15 bars have non-zero tick volume. Absorption/CVD signals may be unreliable. "
            f"Consider using a crypto pair for orderflow backtesting."
        )

    # ── Pre-compute ATR for the full series ─────────────────────────────────
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    atr_full = calc_atr(highs, lows, closes, 14)

    # ── Walk-forward backtest loop ──────────────────────────────────────────
    trades = []
    active_exit_indices = []
    same_bar_both_hit_count = 0
    vp_lookback = max(
        20,
        int(cfg.get("BT_VP_LOOKBACK_BARS", cfg.get("SCALP_VP_LOOKBACK_BARS", cfg.get("VP_LOOKBACK_BARS", 50)))),
    )
    min_lookback = max(80, vp_lookback + 30)  # need enough bars for VP + bias/context

    # OOS split: last 30% of trades flagged as out-of-sample
    total_bars = len(candles)

    for i in range(min_lookback, total_bars - walk_bars - 1):
        active_exit_indices = [exit_idx for exit_idx in active_exit_indices if exit_idx > i]
        # Skip if at max concurrent positions
        if len(active_exit_indices) >= max_concurrent:
            continue

        # Current M15 structure context. A setup is only known after this M15
        # bar is closed; lower-TF execution starts after signal_close_dt.
        structure_bar = candles[i]
        candle_dt = _coerce_utc_datetime(structure_bar.get("time"))
        signal_close_dt = structure_bar.get("_close_dt") or candle_dt
        m15_context = candles[: i + 1]
        # Backtest execution intentionally remains on the M15 proxy. The M1/M5
        # live path is not used here because it changed known-good historical
        # results by pairing later entries with stale M15 VP targets.
        exec_context = m15_context[-100:]
        context_for_vwap = m15_context[-vp_lookback:]

        current_price = structure_bar["close"]
        current_atr = atr_full[i] if i < len(atr_full) else 0
        if current_atr <= 0:
            continue
        session_ok, session_label = scalp_session_window(asset_type, when=signal_close_dt, backtest=True)
        if not session_ok:
            continue

        # ── Step 1: Run the same VP/setup pipeline as live scan ─────────────
        if len(m15_context) < vp_lookback:
            continue
        vp_window = m15_context[-vp_lookback:]
        vp = (
            _build_trade_bucket_volume_profile(display, reference_ts=signal_close_dt, require_fresh=False)
            if asset_type == "crypto" and cfg.get("TRADE_BUCKET_VP_ENABLED", True)
            else {"valid": False}
        )
        if not vp.get("valid"):
            vp = _build_volume_profile(vp_window)
            if vp.get("valid"):
                vp.setdefault("volume_source", "candles")
        if not vp.get("valid") or vp.get("poc") is None:
            continue

        poc = vp["poc"]
        vah = vp["vah"]
        val = vp["val"]
        va_width = abs(vah - val)
        if va_width <= 0:
            continue

        market_state = _classify_market_state(vp)
        price_loc = _locate_price_vs_vp(current_price, vp)
        absorption = _check_absorption(exec_context)
        cvd = (
            _check_trade_bucket_cvd(display, reference_ts=signal_close_dt, require_fresh=False)
            if asset_type == "crypto" and cfg.get("TRADE_BUCKET_CVD_ENABLED", True)
            else {"source": "disabled"}
        )
        if not cvd.get("direction"):
            cvd = _check_cvd(exec_context)
            cvd["source"] = "candles"
        aaa = _check_aaa_sequence(exec_context, absorption, cvd) if cfg.get("AAA_ENABLED", True) else {"complete": False, "phase": "disabled"}
        vwap = _check_vwap_lean(context_for_vwap, current_price) if cfg.get("VWAP_ENABLED", True) else {"lean": None, "vwap_value": 0}
        bias_context = _resample_closed_m15_context(m15_context, bias_tf)
        htf_bias = infer_bias_from_ema_stack(bias_context) if len(bias_context) >= 200 else None
        setup = _classify_setup(
            market_state,
            price_loc,
            absorption,
            cvd,
            aaa,
            vwap,
            htf_bias,
            asset_type=asset_type,
            candles=exec_context,
        )
        if not setup.get("valid"):
            continue
        direction = setup["direction"]
        setup_type = setup["setup_type"]

        # Grade gate: compute grade from pipeline outputs BEFORE entering the trade.
        # Mirrors live run_scalp_scan() which skips grades below MIN_GRADE_AUTO_EXECUTE.
        _pre_grade_sessions = get_grade_sessions_for_mode(asset_type, when=signal_close_dt, backtest=True)
        _pre_quality = _ai_quality_grade(
            vp=vp, price_loc=price_loc, absorption=absorption, cvd=cvd,
            aaa=aaa, vwap=vwap, setup=setup, sessions=_pre_grade_sessions,
            spread_pips=0.0, htf_bias=htf_bias,
        )
        _pre_grade = _pre_quality.get("grade", "D")
        if _grade_order.index(_pre_grade) > _min_grade_idx:
            continue

        if absorption.get("detected"):
            trigger_type = "absorption"
        elif cvd.get("direction") == direction:
            trigger_type = "cvd_shift"
        elif aaa.get("complete"):
            trigger_type = "aaa"
        else:
            trigger_type = "vwap" if vwap.get("lean") == direction else "setup"

        loc = price_loc.get("location")
        if loc == "at_val":
            zone_level = float(val)
        elif loc == "at_vah":
            zone_level = float(vah)
        elif loc == "at_poc":
            zone_level = float(poc)
        else:
            zone_level = float(price_loc.get("nearest_level") or (val if direction == "LONG" else vah))

        # ── Step 4: Calculate entry, SL, TP1, TP2 ───────────────────────────
        exit_tf = "M15"
        if i + 1 >= total_bars:
            continue
        entry_bar = candles[i + 1]
        walk_rows = candles[i + 2:i + 2 + walk_bars]
        entry_bar_idx = i + 1

        point_est = current_atr * 0.001  # rough point estimate
        slippage = slippage_ticks * point_est

        if direction == "LONG":
            entry = entry_bar["open"] + slippage
        else:
            entry = entry_bar["open"] - slippage

        proximity = max(va_width * 0.15, current_atr * 0.05)
        safety_buffer = max(current_atr * 0.5, abs(entry) * 0.0005, point_est)
        if direction == "LONG":
            sl = zone_level - proximity - (current_atr * 0.3)
            if sl >= entry:
                sl = entry - safety_buffer
            sl_distance = entry - sl
            min_target = entry + (sl_distance * min_rr)
            if str(setup_type).startswith("trend"):
                tp1 = min_target
            else:
                tp1 = max(float(poc), min_target) if poc > entry else min_target
            tp2 = max(float(vah), tp1 + sl_distance)
        else:
            sl = zone_level + proximity + (current_atr * 0.3)
            if sl <= entry:
                sl = entry + safety_buffer
            sl_distance = sl - entry
            min_target = entry - (sl_distance * min_rr)
            if str(setup_type).startswith("trend"):
                tp1 = min_target
            else:
                tp1 = min(float(poc), min_target) if poc < entry else min_target
            tp2 = min(float(val), tp1 - sl_distance)

        if sl_distance <= 0:
            continue

        if not math.isfinite(tp1) or (direction == "LONG" and tp1 <= entry) or (direction == "SHORT" and tp1 >= entry):
            continue

        actual_rr = abs(tp1 - entry) / sl_distance if sl_distance > 0 else 0
        if actual_rr < 1.0:
            continue

        # Fabio: first scale-out is always at +1R ("pay yourself first")
        tp_partial = entry + sl_distance if direction == "LONG" else entry - sl_distance

        # ── Step 5: Walk forward — Engine D Partial-exit model ──────────────────────────
        # Sequence: hit TP1 -> close ENGINE_D_TP1_SIZE (default 50%) -> move SL to BE -> trail to TP2/SL/TIMEOUT
        exit_reason = None
        exit_price = None
        exit_bar_idx = None
        best_favorable_r = 0.0
        
        tp1_hit = False
        tp2_hit = False
        sl_hit = False
        timeout_hit = False
        scratch_hit = False
        be_hit = False

        partial_enabled = bool(cfg.get("ENGINE_D_PARTIAL_EXIT_ENABLED", True))
        tp1_size = float(cfg.get("ENGINE_D_TP1_SIZE", 0.5)) if partial_enabled else 1.0
        move_sl_to_be = bool(cfg.get("ENGINE_D_MOVE_SL_TO_BE_AFTER_TP1", True))
        runner_enabled = bool(cfg.get("ENGINE_D_RUNNER_ENABLED", True))
        same_candle_policy = str(cfg.get("ENGINE_D_SAME_CANDLE_POLICY", "conservative_sl_first"))
        
        # If partials are disabled, TP1 is the terminal exit (100% size)
        if not partial_enabled or not runner_enabled:
            tp1_size = 1.0

        live_sl = sl
        be_armed = False
        be_stop = entry

        for w, wbar in enumerate(walk_rows, start=1):
            walk_idx = int(wbar.get("_seq", entry_bar_idx + w))
            if direction == "LONG":
                bar_high = wbar["high"]
                bar_low = wbar["low"]
                best_favorable_r = max(best_favorable_r, max(0.0, (bar_high - entry) / sl_distance))
                
                # Check for same-candle hits
                hit_sl_now = bar_low <= live_sl
                hit_tp1_now = bar_high >= tp1
                
            else:
                bar_high = wbar["high"]
                bar_low = wbar["low"]
                best_favorable_r = max(best_favorable_r, max(0.0, (entry - bar_low) / sl_distance))
                
                # Check for same-candle hits
                hit_sl_now = bar_high >= live_sl
                hit_tp1_now = bar_low <= tp1

            # Same candle resolution
            if hit_sl_now and hit_tp1_now and same_candle_policy == "conservative_sl_first":
                hit_tp1_now = False # assume SL hit first

            # 1. Check SL (or BE)
            if hit_sl_now:
                sl_hit = True
                exit_price = live_sl
                exit_bar_idx = walk_idx
                if be_armed:
                    be_hit = True
                break

            # 2. Check TP1
            if hit_tp1_now and not tp1_hit:
                tp1_hit = True
                if move_sl_to_be:
                    live_sl = be_stop
                    be_armed = True
                if tp1_size >= 1.0:
                    # Full exit
                    exit_price = tp1
                    exit_bar_idx = walk_idx
                    break

            # 3. Check TP2 (only if TP1 hit and runner enabled)
            if tp1_hit and tp1_size < 1.0:
                hit_tp2_now = (direction == "LONG" and bar_high >= tp2) or (direction == "SHORT" and bar_low <= tp2)
                if hit_tp2_now:
                    tp2_hit = True
                    exit_price = tp2
                    exit_bar_idx = walk_idx
                    break

            # 4. Check Scratch
            if scratch_enabled and not tp1_hit and w >= scratch_bars and best_favorable_r < scratch_min_r:
                scratch_hit = True
                exit_price = wbar["close"]
                exit_bar_idx = walk_idx
                break

        # Timeout / End of Data fallback
        if not exit_bar_idx:
            timeout_bar = walk_rows[-1] if walk_rows else entry_bar
            timeout_idx = int(timeout_bar.get("_seq", entry_bar_idx))
            exit_price = timeout_bar["close"]
            timeout_hit = True
            exit_bar_idx = timeout_idx

        # Calculate Gross R
        def _calc_r(px):
            if direction == "LONG":
                return (px - entry) / sl_distance
            else:
                return (entry - px) / sl_distance

        if tp1_hit:
            if tp1_size >= 1.0:
                gross_R = _calc_r(tp1)
            else:
                runner_price = tp2 if tp2_hit else exit_price
                gross_R = (tp1_size * _calc_r(tp1)) + ((1.0 - tp1_size) * _calc_r(runner_price))
        else:
            gross_R = _calc_r(exit_price)

        # Path exit reason
        if tp1_hit:
            if tp1_size >= 1.0:
                path_exit_reason = "DIRECT_TP1_TERMINAL"
                primary_exit_reason = "TP1"
            elif tp2_hit:
                path_exit_reason = "TP1_THEN_TP2"
                primary_exit_reason = "TP2"
            elif sl_hit or be_hit:
                path_exit_reason = "TP1_THEN_BE" if be_hit else "TP1_THEN_SL"
                primary_exit_reason = "BE" if be_hit else "SL"
            elif timeout_hit:
                path_exit_reason = "TP1_THEN_TIMEOUT"
                primary_exit_reason = "TIMEOUT"
            else:
                path_exit_reason = "TP1_THEN_SCRATCH"
                primary_exit_reason = "SCRATCH"
        elif sl_hit:
            path_exit_reason = "DIRECT_SL"
            primary_exit_reason = "SL"
        elif scratch_hit:
            path_exit_reason = "SCRATCH_NO_TP1"
            primary_exit_reason = "SCRATCH_NO_FOLLOW_THROUGH"
        else:
            path_exit_reason = "TIMEOUT_NO_TP1"
            primary_exit_reason = "TIMEOUT"

        exit_reason = primary_exit_reason
        final_exit_reason = path_exit_reason

        # Fee & Slippage calculation
        # fee_R = estimated_fee_pct / risk_pct
        risk_pct = sl_distance / entry if entry > 0 else 0
        estimated_fee_pct = float(cfg.get("ESTIMATED_FEE_PCT", 0.0006))
        fee_R = estimated_fee_pct / risk_pct if risk_pct > 0 else 0
        slippage_R = slippage_ticks * point_est / sl_distance if sl_distance > 0 else 0
        
        # Only apply fees if we entered the trade
        net_R = round(gross_R - fee_R - slippage_R, 4)
        r_multiple = net_R

        # Grade through the same scorer as live scan
        _grade_sessions = get_grade_sessions_for_mode(asset_type, when=signal_close_dt, backtest=True)
        _quality = _ai_quality_grade(
            vp=vp, price_loc=price_loc, absorption=absorption, cvd=cvd, aaa=aaa,
            vwap=vwap, setup=setup, sessions=_grade_sessions, spread_pips=0.0, htf_bias=htf_bias,
        )
        grade = _quality["grade"]
        grade_score = _quality["score"]

        # Determine OOS flag
        oos = i > total_bars * 0.70
        exit_structure_idx = exit_bar_idx if exit_bar_idx is not None else (i + 1)

        trade = {
            "bar_index": i,
            "entry_bar_index": entry_bar_idx,
            "exit_bar_index": exit_bar_idx,
            "structure_entry_bar_index": i + 1,
            "structure_exit_bar_index": exit_structure_idx,
            "execution_tf": exit_tf,
            "context_tf": "M15",
            "structure_tf": "M15",
            "symbol": display.replace("/", "") if asset_type == "crypto" else display,
            "pair": display,
            "asset_group": asset_type,
            "engine": "ENGINE_D",
            "strategy_family": "ENGINE_D_SCALP",
            "direction": direction,
            "setup_type": setup_type,
            "trigger_type": trigger_type,
            "trigger_pattern": trigger_type.upper() if trigger_type else "NONE",
            "grade": grade,
            "ai_grade": grade,
            "ai_score": grade_score,
            "ai_reasons": _quality.get("reasons", []),
            "size_multiplier": _quality.get("size_multiplier"),
            "entry": round(entry, 6),
            "sl": round(sl, 6),
            "tp_partial": round(tp_partial, 6),
            "tp1": round(tp1, 6),
            "tp2": round(tp2, 6) if tp2 else round(tp1, 6),
            "tp1_hit": bool(tp1_hit),
            "partial_taken_1r": bool(tp1_hit),
            "runner_be_armed": bool(be_armed),
            "exit_price": round(exit_price, 6),
            "exit_reason": exit_reason,
            "primary_exit_reason": primary_exit_reason,
            "final_exit_reason": final_exit_reason,
            "path_exit_reason": path_exit_reason,
            "gross_R": round(gross_R, 4),
            "fee_R": round(fee_R, 4),
            "slippage_R": round(slippage_R, 4),
            "net_R": net_R,
            "resultR": r_multiple,
            "r_multiple": r_multiple,
            "rr_planned": round(actual_rr, 2),
            "sl_distance": round(sl_distance, 6),
            "poc": round(poc, 6),
            "vah": round(vah, 6),
            "val": round(val, 6),
            "zone_level": round(zone_level, 6),
            "atr": round(current_atr, 6),
            "session": session_label,
            "oos": oos,
            "regime": "SCALP",
            "bars_held": (exit_bar_idx - entry_bar_idx) if exit_bar_idx else 0,
            **build_strategy_lab_telemetry(
                engine="ENGINE_D",
                strategy_family="ENGINE_D_SCALP",
                regime="breakout" if "breakout" in (setup_type or "").lower() else "unknown",
                setup_type=setup_type,
                timeframe=exit_tf,
                failure_reason=exit_reason if exit_reason not in ["TP1", "TP2", "TP_PARTIAL"] else None,
                entry_reason=None,
                exit_reason=exit_reason,
                source_module="backtest_runner",
                source_function="backtest_pair"
            ),
            "market_state": market_state,
            "price_location": price_loc.get("location"),
            "vp_volume_source": vp.get("volume_source", "candles"),
            "vp_bucket_count": vp.get("bucket_count"),
            "absorption_count": int(absorption.get("count", 0) or 0),
            "cvd_direction": cvd.get("direction"),
            "cvd_source": cvd.get("source", "candles"),
            "cvd_bucket_count": cvd.get("bucket_count"),
            "aaa_complete": bool(aaa.get("complete")),
            "vwap_lean": vwap.get("lean"),

            # Compatibility fields for _format_backtest_results
            "ob_at_zone": False,
            "bos_mtf_confirmed": False,
            "breaker_active": False,
            "fvg_overlap": False,
            "liquidity_sweep": False,
            "zone_touched": True,
            "bos_volume_confirmed": trigger_type == "absorption",
            "choch_confirmed": False,
        }
        trades.append(trade)
        if exit_bar_idx is not None:
            active_exit_indices.append(exit_structure_idx)

    # ── Format results ──────────────────────────────────────────────────────
    if not trades:
        return {"error": f"No scalp setups found for {display} in {total_bars} M15 bars"}

    log.info(
        f"[SCALP-BT] {display}: {len(trades)} trades from {total_bars} M15 bars "
        f"(same_bar_both_hit={same_bar_both_hit_count})"
    )

    result = _format_backtest_results(
        trades, pair, engine_type="SCALP_VP",
        same_bar_both_hit=same_bar_both_hit_count,
        validation_mode=validation_mode,
    )

    # Override style fields
    result["btStyle"] = "scalp"
    result["btStyleRequested"] = "scalp"
    result["engine"] = "scalp_vp"
    result["vol_quality_pct"] = vol_quality_pct
    result["vol_quality_warning"] = vol_quality_pct < 30
    result["structure_tf"] = "M15"
    result["context_tf"] = "M15"
    result["execution_tf"] = "M15"
    result["vp_lookback_bars"] = vp_lookback
    result["lower_tf_fallback"] = True
    result["backtest_model"] = "M15_STABLE_PROXY"

    # ── Scalp-specific analysis ─────────────────────────────────────────────
    absorption_trades = [t for t in trades if t.get("trigger_type") == "absorption"]
    cvd_trades = [t for t in trades if t.get("trigger_type") == "cvd_shift"]
    rejection_trades = [t for t in trades if t.get("trigger_type") == "rejection"]
    mr_trades = [t for t in trades if t.get("setup_type") == "mean_reversion"]
    trend_trades = [t for t in trades if str(t.get("setup_type", "")).startswith("trend")]

    def _subset_wr(subset):
        if not subset:
            return None
        wins = len([t for t in subset if t.get("r_multiple", 0) > 0])
        return round(wins / len(subset) * 100, 1)

    def _subset_avg_r(subset):
        if not subset:
            return None
        return round(sum(t.get("r_multiple", 0) for t in subset) / len(subset), 3)

    result["scalp_analysis"] = {
        "absorption": {"count": len(absorption_trades), "wr": _subset_wr(absorption_trades), "avg_r": _subset_avg_r(absorption_trades)},
        "cvd_shift": {"count": len(cvd_trades), "wr": _subset_wr(cvd_trades), "avg_r": _subset_avg_r(cvd_trades)},
        "rejection": {"count": len(rejection_trades), "wr": _subset_wr(rejection_trades), "avg_r": _subset_avg_r(rejection_trades)},
        "mean_reversion": {"count": len(mr_trades), "wr": _subset_wr(mr_trades), "avg_r": _subset_avg_r(mr_trades)},
        "trend": {"count": len(trend_trades), "wr": _subset_wr(trend_trades), "avg_r": _subset_avg_r(trend_trades)},
        "grade_A": {"count": len([t for t in trades if t.get("grade") == "A"]), "wr": _subset_wr([t for t in trades if t.get("grade") == "A"])},
        "grade_B": {"count": len([t for t in trades if t.get("grade") == "B"]), "wr": _subset_wr([t for t in trades if t.get("grade") == "B"])},
        "grade_C": {"count": len([t for t in trades if t.get("grade") == "C"]), "wr": _subset_wr([t for t in trades if t.get("grade") == "C"])},
    }

    # ── Save to DB ──────────────────────────────────────────────────────────
    try:
        import sqlite3 as _sq
        _wf = result.get("wfSplit", {}) or {}
        _is_sqn = _wf.get("is_sqn")
        _oos_sqn = _wf.get("oos_sqn")
        with _sq.connect(_rt().AUDIT_DB, timeout=15.0) as _con:
            _con.execute(
                "INSERT INTO backtest_results "
                "(run_date,pair,asset_type,engine,trades,win_rate,profit_factor,"
                "expectancy,sqn,sharpe,sortino,is_score,oos_score,max_dd_pct,bt_min,atr_source,notes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    display,
                    asset_type,
                    "scalp_vp",
                    result.get("totalTrades", 0),
                    result.get("winRate"),
                    result.get("profitFactor"),
                    result.get("expectancy"),
                    result.get("sqn"),
                    result.get("sharpe"),
                    result.get("sortino"),
                    round(_is_sqn, 4) if _is_sqn is not None else None,
                    round(_oos_sqn, 4) if _oos_sqn is not None else None,
                    result.get("maxDrawdownPct"),
                    min_rr,
                    "M15_ATR",
                    f"vp_bins={vp_bins};vp_lookback={vp_lookback};exec_tf={result['execution_tf']};walk={walk_bars};abs_mult={abs_vol_mult};slippage={slippage_ticks}",
                ),
            )
            _con.commit()
        log.info(f"[SCALP-BT-DB] Saved: {display} SQN={result.get('sqn', 0):.2f} ({len(trades)} trades)")
    except Exception as _dbe:
        log.warning(f"[SCALP-BT-DB] Failed to save: {_dbe}")

    try:
        record_backtest_summary(
            engine="scalp_vp",
            pass_rate=None,
            expectancy_r=result.get("expectancy"),
            score=result.get("sqn"),
            max_score=10.0,
            meta={"pair": display, "trades": len(trades)},
        )
    except Exception:
        pass

    return result


