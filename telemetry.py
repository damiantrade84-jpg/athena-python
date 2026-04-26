"""Telemetry helpers for Athena Strategy Lab v1.2."""

def build_strategy_lab_telemetry(
    *,
    engine: str | None,
    strategy_family: str | None,
    regime: str | None,
    setup_type: str | None,
    timeframe: str | None,
    failure_reason: str | None,
    entry_reason: str | None,
    exit_reason: str | None,
    source_module: str,
    source_function: str,
    extra: dict | None = None,
) -> dict:
    """Standardize telemetry for historical and live trades to be parsed by Strategy Lab.
    
    Valid strategy families: ENGINE_A_ONLY, ENGINE_B_ONLY, ENGINE_C_CONSENSUS, ENGINE_D_SCALP, BASELINE_MA_RIBBON_RSI_OBV, UNKNOWN
    Valid regimes: trending, ranging, volatility_expansion, volatility_compression, breakout, sweep_reversal, unknown
    """
    
    # Clean strategy_family
    valid_families = {
        "ENGINE_A_ONLY", "ENGINE_B_ONLY", "ENGINE_C_CONSENSUS", 
        "ENGINE_D_SCALP", "BASELINE_MA_RIBBON_RSI_OBV", "UNKNOWN"
    }
    sf = str(strategy_family).upper().strip() if strategy_family else "UNKNOWN"
    if sf not in valid_families:
        sf = "UNKNOWN"
        
    # Clean regime
    valid_regimes = {
        "trending", "ranging", "volatility_expansion", 
        "volatility_compression", "breakout", "sweep_reversal", "unknown"
    }
    rg = str(regime).lower().strip() if regime else "unknown"
    if rg not in valid_regimes:
        # Check mapping or fallback
        if "trend" in rg or "adx" in rg: rg = "trending"
        elif "range" in rg: rg = "ranging"
        elif "expansion" in rg: rg = "volatility_expansion"
        elif "compression" in rg or "squeeze" in rg: rg = "volatility_compression"
        elif "breakout" in rg: rg = "breakout"
        elif "sweep" in rg: rg = "sweep_reversal"
        else: rg = "unknown"
        
    return {
        "engine": str(engine).upper() if engine else "UNKNOWN",
        "strategy_family": sf,
        "regime": rg,
        "setup_type": str(setup_type) if setup_type else "UNKNOWN",
        "timeframe": str(timeframe) if timeframe else "UNKNOWN",
        "failure_reason": str(failure_reason) if failure_reason else "UNKNOWN",
        "entry_reason": str(entry_reason) if entry_reason else "UNKNOWN",
        "exit_reason": str(exit_reason).upper() if exit_reason else "UNKNOWN",
        "source_module": source_module,
        "source_function": source_function,
        "telemetry_version": "1.2",
        **(extra or {})
    }
