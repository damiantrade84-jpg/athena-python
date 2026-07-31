import json
from pathlib import Path

from config import _json_safe


ROOT = Path(__file__).resolve().parents[1]
TV_PANEL = ROOT / "static/react-app/app/src/components/panels/TVChartPanel.tsx"
DISPLAY_HELPERS = ROOT / "static/react-app/app/src/lib/engineADiagnosticsDisplay.ts"
VITE_CONFIG = ROOT / "static/react-app/app/vite.config.ts"
MAIN_TSX = ROOT / "static/react-app/app/src/main.tsx"
STORE = ROOT / "static/react-app/app/src/hooks/useStore.tsx"
ATHENA_TYPES = ROOT / "static/react-app/app/src/types/athena.ts"
PROVIDER_TOGGLE = ROOT / "static/react-app/app/src/components/athena/AIReviewProviderToggle.tsx"

ENGINE_A_DIAGNOSTIC_FIXTURE = {
    "type": "forex",
    "display": "EUR/USD",
    "direction": "LONG",
    "entry": 1.1000,
    "sl": 1.0950,
    "tp": 1.1100,
    "atr": 0.0025,
    "factorScores": {"addon": 0, "trend": 1.2},
    "factorDiagnostics": {
        "directionalRampMult": 1.0,
        "minDirectional": 0.25,
        "min_directional_threshold": 0.25,
        "effective_min_directional": 0.3,
        "trendCoherence": {"agreement_count": 3, "coherence_ratio": 1.0},
        "feedStatus": {"addon": "CONFIRMING"},
        "addon_value": 0,
        "engineAAssetDiagnostics": {"carry": "neutral"},
    },
    "atrDiagnostics": {
        "atr_tf": "H4",
        "atr_source": "engine_a",
        "atr_candle_last_ts": "2026-05-21T12:00:00Z",
        "atr_age_seconds": 120.5,
        "atr_confirmed_only": True,
    },
    "candleFetchMeta": {
        "D1": {"cacheHit": True, "primary_provider": "mt5"},
        "H4": {"cacheHit": False, "primary_provider": "mt5"},
        "H1": {"cacheHit": True, "primary_provider": "mt5"},
        "pairSource": "mt5",
    },
}


# Keep in sync with static/react-app/app/src/lib/engineADiagnosticsDisplay.ts
def _first_number(*values):
    for value in values:
        if isinstance(value, (int, float)) and value == value and abs(value) != float("inf"):
            return float(value)
        if isinstance(value, str) and value.strip():
            try:
                parsed = float(value)
            except ValueError:
                continue
            if parsed == parsed and abs(parsed) != float("inf"):
                return parsed
    return None


def _resolve_directional_ramp_display(signal):
    if not signal:
        return "Unavailable — no Engine A candidate for chart symbol"
    diagnostics = signal.get("factorDiagnostics")
    if not isinstance(diagnostics, dict):
        return "Unavailable — factorDiagnostics missing from signal payload"
    numeric = _first_number(
        diagnostics.get("directionalRampMult"),
        diagnostics.get("directionalRampMultiplier"),
        diagnostics.get("directional_ramp_multiplier"),
    )
    if numeric is None:
        return "Unavailable — factorDiagnostics.directionalRampMult missing from payload"
    return f"{numeric:.2f}"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_ai_review_provider_state_defaults_to_openai_and_types_allow_all_providers():
    store = _read(STORE)
    types = _read(ATHENA_TYPES)

    assert "AIReviewProvider" in store
    assert "aiReviewProvider: AIReviewProvider" in store
    assert "useState<AIReviewProvider>('openai')" in store
    assert "setAiReviewProvider" in store
    assert "/api/ai-review/provider" in store
    assert "export type AIChartReviewProvider" in types
    assert "'grok'" in types
    assert "'claude'" in types
    assert "'openai'" in types


def test_tv_chart_ai_review_uses_shared_provider_toggle():
    source = _read(TV_PANEL)
    toggle = _read(PROVIDER_TOGGLE)

    assert "AIReviewProviderToggle" in source
    assert "aiReviewProvider" in source
    assert "setAiReviewProvider" in source
    assert "provider: aiReviewProvider" in source
    assert "Grok" in toggle
    assert "Claude" in toggle
    assert "ChatGPT / GPT-5.5" in toggle


def test_tv_chart_panel_renders_with_lightweight_charts():
    """The panel renders candles via the in-house lightweight-charts pipeline.

    The previous TradingView iframe embed widget capped studies at ~3 for
    anonymous viewers, so 4+ enabled indicators were silently dropped.
    This panel must render every enabled indicator deterministically.
    """
    source = _read(TV_PANEL)

    assert "export default function TVChartPanel" in source
    assert "from 'lightweight-charts'" in source
    assert "createChart" in source
    assert "CandlestickSeries" in source
    assert "LineSeries" in source
    assert "EngineASidePanel" in source
    # The TradingView iframe-embed path must be fully removed — no silent fallback.
    assert "embed-widget-advanced-chart.js" not in source
    assert "srcDoc=" not in source
    assert "MAExp@tv-basicstudies" not in source


def test_indicator_toggles_wire_in_house_series_per_pane():
    """Every indicator toggle maps to an in-house series in the correct pane.

    Price pane (0): candles + EMA20/50/200 + DEMA200.
    Sub-pane (RSI14): created only when rsi14 is on; same for ATR14.
    """
    source = _read(TV_PANEL)

    # Indicator math is implemented in-house — no TV study IDs.
    assert "export function ema(" in source
    assert "export function dema(" in source
    assert "export function rsi(" in source
    assert "export function atr(" in source

    # Each indicator has its own series ref and is pushed into pane 0 or its sub-pane.
    for ref in (
        "ema20SeriesRef",
        "ema50SeriesRef",
        "ema200SeriesRef",
        "dema200SeriesRef",
        "rsiSeriesRef",
        "atrSeriesRef",
    ):
        assert ref in source, f"missing series ref: {ref}"

    # Sub-panes are created only when their study is on (otherwise a pane sits empty).
    assert "if (quantRsi14) {" in source
    assert "if (quantAtr14) {" in source
    assert "chart.addPane()" in source

    # Backend candle fetch is parameterized by symbol + timeframe.
    assert "/api/candles?symbol=" in source
    assert "TF_BACKEND_MAP" in source

    # The indicator-preset selector is in place (replaces the lone layout button).
    assert "PRESET_OPTIONS" in source
    assert "Indicator preset" in source


def test_native_chart_fetches_warmup_history_but_keeps_latest_window_visible():
    source = _read(TV_PANEL)

    assert "const CHART_HISTORY_LIMIT = 1000" in source
    assert "const VISIBLE_BAR_COUNT = 180" in source
    assert "limit=${CHART_HISTORY_LIMIT}" in source
    assert "setVisibleLogicalRange" in source
    assert "rows.length - VISIBLE_BAR_COUNT" in source


def test_native_chart_uses_compact_fixed_height_instead_of_stretch_layout():
    source = _read(TV_PANEL)

    assert "const PRICE_CHART_HEIGHT_PX = 340" in source
    assert "const STUDY_PANE_HEIGHT_PX = 110" in source
    assert "const chartHeightPx = PRICE_CHART_HEIGHT_PX + subPaneStudyCount * STUDY_PANE_HEIGHT_PX" in source
    assert "style={{ height: `${chartHeightPx}px` }}" in source
    assert 'h-[calc(100%-160px)]' not in source
    assert "minHeight: `${chartMinHeightPx}px`" not in source


def test_native_chart_has_full_chart_screenshot_action():
    source = _read(TV_PANEL)

    assert "Camera" in source
    assert "chartCaptureRef" in source
    assert "function downloadChartScreenshot" in source
    assert "querySelectorAll('canvas')" in source
    assert "toBlob" in source
    assert "Screenshot" in source
    assert "aria-label=\"Download full chart screenshot\"" in source


def test_native_chart_consumes_shared_live_tick_feed():
    source = _read(TV_PANEL)

    assert "useLivePrices" in source
    assert "const LIVE_TICK_MAX_AGE_SEC = 20" in source
    assert "const { priceEntryFor } = useLivePrices()" in source
    assert "liveTickFromEntry(priceEntryFor(pair))" in source
    assert "buildLiveCandleRows(baseRows, liveTick, backendTf)" in source
    assert "lastValueVisible: true" in source
    assert "priceLineVisible: true" in source


def test_live_tick_builds_forming_candle_without_mutating_source_history():
    source = _read(TV_PANEL)

    assert "function buildLiveCandleRows" in source
    assert "if (liveTick.ageSec > LIVE_TICK_MAX_AGE_SEC) return baseRows" in source
    assert "const out = [...baseRows]" in source
    assert "bucketStart > lastTime" in source
    assert "open: last.close" in source
    assert "high: Math.max(last.high, liveTick.price)" in source
    assert "low: Math.min(last.low, liveTick.price)" in source
    assert "close: liveTick.price" in source


def test_native_chart_surfaces_live_price_without_overwriting_signal_entry():
    source = _read(TV_PANEL)

    assert 'label="Entry" value={firstNumber(signal?.entry, signal?.price)}' in source
    assert 'label="Live price" value={liveTick?.price}' in source
    assert 'label="Live tick"' in source
    assert 'titleCaseProvider(liveTick.source)' in source
    assert "<EngineASidePanel signal={chartCandidate} liveTick={liveTick} chartPayload={chartPayload} />" in source


def test_tv_chart_panel_consumes_tv_chart_intent():
    source = _read(TV_PANEL)

    assert "tvChartIntent" in source
    assert "const [intentSignal, setIntentSignal] = useState<EngineASignal | null>(null)" in source
    assert "function isEngineSignalLike(value: unknown): value is EngineASignal" in source
    assert "appliedIntentIdRef" in source
    assert "pendingAutoReviewRef" in source
    assert "Opened from Signals" in source
    assert "Auto Review:" in source
    assert "clearTvChartIntent" in source


def test_tv_chart_panel_auto_review_guard_exists():
    source = _read(TV_PANEL)

    assert "autoReviewRanForIntentRef" in source
    assert "pendingAutoReviewRef.current" in source
    assert "chartPaintReadyGenerationRef" in source
    assert "chartPaintReadyTick" in source
    assert "waitForChartPaint" in source
    assert "chartRenderGenerationKey" in source


def test_tv_chart_panel_auto_review_waits_for_chart_paint():
    source = _read(TV_PANEL)

    assert "chartPaintReadyForReview" in source
    assert "chartPaintReadyGenerationRef.current === chartRenderGeneration" in source
    assert "requestAnimationFrame" in source


def test_tv_chart_panel_review_generation_key_uses_rendered_row_count():
    """Gate and paint effect must share the same row-count source (not raw candles.length)."""
    source = _read(TV_PANEL)

    assert "chartRenderGenerationKey(pair, backendTf, studySnapshot.rows.length)" in source
    assert "chartRenderGenerationKey(pair, backendTf, candles.length)" not in source
    assert "chartRenderGenerationKey(pair, backendTf, rows.length)" in source


def test_tv_chart_panel_review_block_reason_helper_exists():
    source = _read(TV_PANEL)

    assert "resolveChartReviewBlockReason" in source
    assert "chartReviewBlockReason" in source
    assert "Waiting for chart paint" in source


def test_tv_chart_panel_capture_failure_surfaces_ai_review_error():
    source = _read(TV_PANEL)

    assert "setAiReviewError(captured.error" in source
    assert "captureReviewCanvas" in source


def test_tv_chart_panel_auto_review_waits_for_engine_b_overlay_state():
    source = _read(TV_PANEL)

    assert "engineBOverlayPendingForReview" in source
    assert "if (!pendingAutoReviewRef.current || chartReviewPendingForReview || aiReviewLoading) return" in source
    assert "engineBOverlayStatus" in source


def test_tv_chart_panel_auto_review_waits_for_indicator_readiness_and_settle_window():
    source = _read(TV_PANEL)

    assert "AUTO_REVIEW_CHART_SETTLE_MS = 10_000" in source
    assert "chartIndicatorsReadyForReview" in source
    assert "autoReviewEarliestRunAtRef" in source
    assert "remainingSettleMs" in source
    assert "setAutoReviewDelayTick" in source


def test_tv_chart_panel_manual_review_cancels_pending_auto_review():
    source = _read(TV_PANEL)

    assert "function runManualAIReview()" in source
    assert "pendingAutoReviewRef.current = false" in source
    assert "autoReviewEarliestRunAtRef.current = null" in source
    assert "onClick={runManualAIReview}" in source


def test_tv_chart_panel_ai_review_sends_visible_range_meta():
    source = _read(TV_PANEL)

    assert "getVisibleLogicalRange()" in source
    assert "visible_range_start" in source
    assert "visible_range_end" in source


def test_tv_chart_panel_ai_review_rejects_blank_capture():
    source = _read(TV_PANEL)

    assert "canvasHasNonBackgroundContent" in source
    assert "captureReviewCanvas" in source
    assert "Chart not painted yet — retrying" in source


def test_tv_chart_panel_flag_watch_setup_button():
    source = _read(TV_PANEL)

    assert "Flag / Watch Setup" in source
    assert "/api/suggested-trades/flag" in source
    assert "View Suggested Trades" in source
    assert "showFlagWatchAction" in source
    assert "data-suggested-watch-action" in source
    flag_idx = source.index("Flag / Watch Setup")
    flag_section = source[flag_idx:flag_idx + 800]
    assert "postJson('/api/quick-execute'" not in flag_section
    assert "onConfirmExecute" not in flag_section


def test_tv_chart_panel_execute_now_button():
    source = _read(TV_PANEL)

    assert "Execute Now" in source
    assert "buildQuickExecutePayload" in source
    assert "/api/quick-execute" in source
    assert "evaluateTvChartExecuteBlock" in source
    assert "Confirm Execute Now" in source
    assert "chartCandidate &&" in source
    assert "{executeBlockReason &&" in source
    assert "executeBlockReason || 'Execute Now'" not in source


def test_tv_chart_panel_execute_does_not_send_visual_engine_b_overlay():
    """Engine A execute must not leak visual-only Engine B overlay into payload.engine_b."""
    source = _read(TV_PANEL)
    confirm_idx = source.index("async function onConfirmExecute()")
    confirm_section = source[confirm_idx:confirm_idx + 900]
    assert "buildQuickExecutePayload" in confirm_section
    assert "isEngineBOnly" in confirm_section
    assert "naked_data ?? chartCandidate.engine_b" in confirm_section
    assert "\n          : undefined," in confirm_section


def test_tv_chart_panel_execute_shown_when_can_flag_watch():
    source = _read(TV_PANEL)

    assert "canFlagWatch" in source
    assert "Execute Now" in source
    assert "showExecuteNow" not in source


def test_tv_chart_panel_watch_action_not_gated_by_chart_candidate():
    source = _read(TV_PANEL)

    action_idx = source.index("data-suggested-watch-action")
    action_strip = source[source.rfind('data-review-action-strip', 0, action_idx):action_idx]
    execute_idx = action_strip.index("{chartCandidate &&")
    flag_idx = action_strip.index("{showFlagWatchAction &&")
    assert execute_idx < flag_idx


def test_tv_chart_panel_view_suggested_trades_when_watches_exist():
    source = _read(TV_PANEL)

    assert "showViewSuggestedTrades" in source
    assert "symbolWatches.length > 0" in source


def test_tv_chart_panel_does_not_execute_from_watch_action():
    source = _read(TV_PANEL)
    flag_idx = source.index("Flag / Watch Setup")
    flag_section = source[flag_idx:flag_idx + 1200]
    assert "onConfirmExecute" not in flag_section
    assert "postJson('/api/quick-execute'" not in flag_section


def test_tv_chart_panel_runner_status_badge():
    source = _read(TV_PANEL)

    assert "useSuggestedTradeRunnerStatus" in source
    assert "Runner:" in source


def test_tv_chart_panel_trade_skill_display_and_execute_gating():
    card = _read(ROOT / "static/react-app/app/src/components/athena/AIReviewCard.tsx")
    helper = _read(ROOT / "static/react-app/app/src/lib/manualExecuteHelpers.ts")

    assert "TradeSkillReviewPanel" in card
    assert "entry allowed" in card.lower() or "TradeSkillReviewPanel" in card
    assert "tradeSkillBlocksExecute" in helper
    assert "entryAllowedNow" in helper
    assert "WAIT_FOR_PULLBACK" in helper


def test_tv_chart_panel_execute_disabled_when_ai_says_wait():
    source = _read(TV_PANEL)
    helper = _read(ROOT / "static/react-app/app/src/lib/manualExecuteHelpers.ts")

    assert "evaluateTvChartExecuteBlock" in source
    assert "AI says wait" in helper
    assert "Waiting for level" in helper
    assert "Waiting for zone" in helper
    assert "aiReviewBlocksManualExecute" in helper


def test_native_chart_opens_on_h4_by_default_for_tradingview_parity():
    source = _read(TV_PANEL)

    assert "const [timeframe, setTimeframe] = useState('240')" in source


def test_tv_chart_panel_applies_server_timeframe_route_once():
    source = _read(TV_PANEL)

    assert "TF_UI_CODE_MAP" in source
    assert "function tfCodeForBackend" in source
    assert "timeframeRoute" in source
    assert "timeframe_route" in source
    assert "engine_a_context?.timeframe_route" in source
    assert "lastAppliedRouteKeyRef" in source
    assert "routeKey === lastAppliedRouteKeyRef.current" in source
    assert "setTimeframe(recommendedCode)" in source


def test_tv_chart_panel_applies_intent_timeframe_policy():
    source = _read(TV_PANEL)

    assert "setTimeframeAutoMode(tvChartIntent.autoReview === true)" in source
    assert "autoReview: keep auto TF mode so server timeframe_route can apply after review" in source


def test_tv_chart_panel_manual_tf_override_and_reset_are_present():
    source = _read(TV_PANEL)

    assert "const [timeframeAutoMode, setTimeframeAutoMode] = useState(true)" in source
    assert "function handleManualTimeframeSelect" in source
    assert "setTimeframeAutoMode(false)" in source
    assert "Reset to recommended" in source
    assert "applyRecommendedTimeframe" in source
    assert "Manual TF override" in source


def test_tv_chart_panel_displays_timeframe_route_badge():
    source = _read(TV_PANEL)

    assert "timeframeRouteLabel" in source
    assert "Context ->" in source
    assert "Setup ->" in source
    assert "Trigger" in source
    assert "Auto TF:" in source
    assert "timeframeRoute?.reason" in source


def test_tv_chart_panel_clears_stale_ai_review_on_pair_change():
    source = _read(TV_PANEL)

    assert "aiReviewSymbolKeyRef" in source
    assert "setAiReview(null)" in source
    assert "setAiReviewError(null)" in source
    assert "routeSymbolKey !== currentSymbolKey" in source


def test_tv_chart_panel_stale_clear_normalizes_timeframes():
    source = _read(TV_PANEL)

    assert "reviewContextFromResponse(aiReview)" in source
    assert "normalizeBackendTf(timeframe)" in source


def test_tv_chart_panel_stale_clear_exempts_auto_route_tf():
    source = _read(TV_PANEL)

    assert "normalizeBackendTf(timeframeRoute?.autoSelectTf)" in source
    assert "autoRouteApplied" in source
    assert "timeframeChanged && !autoRouteApplied" in source


def test_tv_chart_panel_symbol_race_sets_review_error():
    source = _read(TV_PANEL)

    assert "Review completed for a different symbol" in source
    assert "setAiReviewError('Review completed for a different symbol" in source


def test_ai_review_card_handles_incomplete_payload():
    source = _read(ROOT / "static/react-app/app/src/components/athena/AIReviewCard.tsx")

    assert "!ai || !c" in source
    assert "AI review response is incomplete" in source


def test_engine_a_review_layout_enables_required_lean_indicators():
    source = _read(TV_PANEL)

    assert "Engine A Review Layout" in source
    assert "setEma20(true)" in source
    assert "setEma50(true)" in source
    assert "setEma200(true)" in source
    assert "setAtr14(true)" in source
    assert "setRsi14(true)" in source
    assert "setDema200(!isCrypto)" in source
    assert "MACD@tv-basicstudies" not in source
    assert "BB@tv-basicstudies" not in source
    assert "Stochastic@tv-basicstudies" not in source


def test_tv_chart_auto_review_enables_required_lean_indicators():
    source = _read(TV_PANEL)
    auto_review_idx = source.index("if (tvChartIntent.autoReview)")
    auto_review_block = source[auto_review_idx:auto_review_idx + 700]

    assert "setShowQuantDebug(true)" in auto_review_block
    assert "setEma20(true)" in auto_review_block
    assert "setEma21(true)" in auto_review_block
    assert "setEma50(true)" in auto_review_block
    assert "setEma200(true)" in auto_review_block
    assert "setDema200(!isCrypto)" in auto_review_block
    assert "setVwapEnabled(isCrypto)" in auto_review_block
    assert "setAdx14(false)" in auto_review_block
    assert "setAtr14(!isCrypto)" in auto_review_block
    assert "setVolumeBars(isCrypto)" in auto_review_block
    assert "setVolumeMa(isCrypto)" in auto_review_block


def test_crypto_chart_exposes_bybit_provider_badge_and_required_indicators():
    source = _read(TV_PANEL)

    assert "chart_provider" in source
    assert "scoring_provider" in source
    assert "vwap_provider" in source
    assert "provider_mismatch" in source
    assert "ProviderBadge" in source
    assert "Bybit" in source
    assert "EMA21" in source
    assert "VWAP" in source
    assert "ADX14" in source
    assert "Volume" in source
    assert "Volume MA" in source


def test_crypto_chart_prefers_api_indicator_series_when_present():
    source = _read(TV_PANEL)

    assert "apiEmaTrend" in source
    assert "apiAtr14" in source
    assert "useApiIndicators" in source


def test_chart_snapshot_does_not_coerce_null_api_indicators_to_zero():
    """Server nulls indicator fields on warm-up/forming bars (confirmed-only
    policy). Number(null) === 0, so a bare toNum() coercion painted 0-valued
    EMA200/RSI/ATR points: the price autoscale stretched to 0 and squashed
    H1 candles flat, and that broken image was sent to AI review.
    """
    source = _read(TV_PANEL)

    assert "function apiSeriesValue" in source
    assert "if (value == null) continue;" in source
    assert "apiSeriesValue(c.vwap)" in source
    assert "apiSeriesValue(c.volume_ma)" in source
    assert "apiSeriesValue(c.adx14, c.adx)" in source
    assert "apiSeriesValue(c.ema_trend, c.ema21)" in source
    assert "apiSeriesValue(c.ema_momentum, c.ema50)" in source
    assert "apiSeriesValue(c.ema_long, c.ema200)" in source
    assert "apiSeriesValue(c.rsi, c.rsi14)" in source
    assert "apiSeriesValue(c.atr14)" in source
    # The null-coercing pattern must not return to the indicator series reads.
    assert "toNum(c.ema_long ?? c.ema200" not in source
    assert "toNum(c.rsi ?? c.rsi14" not in source
    assert "toNum(c.atr14" not in source
    assert "toNum(c.adx14 ?? c.adx" not in source
    assert "toNum(c.vwap" not in source


def test_crypto_atr_legend_includes_timeframe_and_provider():
    source = _read(TV_PANEL)

    assert "cryptoAtr14LegendLabel" in source
    assert "ATR14 ${tf}" in source or "ATR14 `" in source or 'ATR14 ${tf}' in source or "atr_timeframe" in source


def test_chart_capture_area_contains_provider_header_and_indicator_labels_for_screenshots():
    source = _read(TV_PANEL)

    assert "chartCaptureRef" in source
    assert "data-chart-capture-label" in source
    assert "chartFeedIdentityChips" in source
    assert "ChartFeedHeaderChips" in source
    assert "assetGroupLabel" in source
    assert "candlePolicyLabel" in source
    assert "lastCandleLabel" in source
    assert "pricePanelLegendItems" in source
    assert "studyPanelLegendItems" in source
    assert "IndicatorLegendItem" in source


def test_forex_indicator_legend_uses_series_definitions_and_current_values():
    source = _read(TV_PANEL)

    assert "PRICE_PANEL_INDICATORS" in source
    assert "key: 'ema20'" in source
    assert "key: 'ema50'" in source
    assert "key: 'ema200'" in source
    assert "key: 'dema200'" in source
    assert "formatIndicatorValue(item.value" in source
    assert "PRICE_PANEL_INDICATORS.ema20.color" in source
    assert "EMA20" in source
    assert "DEMA200" in source


def test_study_panel_legend_labels_identify_rsi_atr_or_adx_in_metadata_strip():
    source = _read(TV_PANEL)

    assert "RSI14" in source
    assert "70/30" in source
    assert "ATR14" in source
    assert "ADX14" in source
    assert "STUDY_PANEL_INDICATORS" in source
    assert "studyPanelLegendItems" in source
    assert "ChartMetadataStrip" in source
    assert "studyPanelLegendItems.map" in source
    assert "pointer-events-none absolute left-2" not in source
    assert "Bottom panel identity: forex ATR14; crypto ADX14/ATR14 when enabled" not in source


def test_engine_a_parity_toggle_and_overlay_are_dom_visible():
    source = _read(TV_PANEL)

    assert "Engine A Parity" in source
    assert "engineAParityVisible" in source
    assert "const [engineAParityVisible, setEngineAParityVisible] = useState(false)" in source
    assert "engineAParityRows" in source
    assert "price_inside_ema_cluster" in source
    assert "at_or_below_resistance" in source
    assert "nearest_ema_resistance_distance_atr" in source
    assert "nearest_ema_support_distance_atr" in source


def test_chart_screenshot_draws_dom_labels_over_canvas_export():
    source = _read(TV_PANEL)

    assert "function drawCaptureLabels" in source
    assert "querySelectorAll('[data-chart-capture-label]')" in source
    assert "drawCaptureLabels(outputCtx, captureEl, captureRect)" in source


def test_chart_header_uses_ascii_separator_to_avoid_encoding_artifacts():
    source = _read(TV_PANEL)

    assert "ChartFeedHeaderChips" in source
    assert "FeedCaptureChip" in _read(ROOT / "static/react-app/app/src/components/athena/ChartFeedHeaderChips.tsx")
    assert "·" not in source
    assert "Â" not in source


def test_crypto_chart_uses_payload_live_tick_before_shared_price_fallback():
    source = _read(TV_PANEL)

    assert "liveTickFromChartPayload(chartPayload)" in source
    assert "liveTickFromChartPayload(chartTickPayload)" in source
    assert "/api/chart-tick?symbol=" in source
    assert "const sharedLiveTick = useMemo(() => liveTickFromEntry(priceEntryFor(pair)), [pair, priceEntryFor]);" in source
    assert "function isBybitLiveTickProvider" in source
    assert "value.toLowerCase().startsWith('bybit')" in source
    assert "function isBybitCryptoExecution" in source
    assert "const usesBybitChartTick =" in source
    assert "isBybitCryptoExecution(chartPayload)" in source
    assert "isBybitLiveTickProvider(chartPayload?.live_tick_provider)" in source


def test_title_case_provider_maps_binance_and_bybit_ws_rest():
    source = _read(TV_PANEL)

    assert "binance_ws" in source
    assert "bybit_rest" in source
    assert "return 'Binance WS'" in source
    assert "return 'Bybit REST'" in source


def test_engine_a_side_panel_includes_chart_feed_summary():
    source = _read(TV_PANEL)

    assert "buildChartFeedSummary" in source
    assert "chartPayload={chartPayload}" in source
    assert 'label="Chart feeds"' in source


def test_engine_a_side_panel_follows_current_chart_symbol_not_first_candidate():
    source = _read(TV_PANEL)

    assert "function findEngineACandidateForSymbol" in source
    assert "function findEngineBCandidateForSymbol" in source
    assert "const intentCandidateRows = useMemo(" in source
    assert "intentSignalMatchesPair && intentSignal ? [intentSignal, ...candidateRows] : candidateRows" in source
    assert "useEngineBPrimary" in source
    assert "findEngineBCandidateForSymbol(intentCandidateRows, pair)" in source
    assert "findEngineACandidateForSymbol(intentCandidateRows, pair)" in source
    assert "<EngineASidePanel signal={chartCandidate}" in source
    assert "<EngineBSidePanel" in source
    assert "<EngineASidePanel signal={selectedCandidate}" not in source
    assert 'aria-label="Engine A candidate"' in source


def test_visual_review_state_is_not_consumed_by_execution_paths():
    execution_files = [
        ROOT / "execution.py",
        ROOT / "risk_engine.py",
        ROOT / "guardian.py",
        ROOT / "auto_trader.py",
        ROOT / "mt5_executor.py",
        ROOT / "bybit_executor.py",
    ]
    forbidden = [
        "TVChartPanel",
        "Engine A Review Layout",
        "buildTradingViewStudies",
        "visualReview",
        "visual_review",
    ]

    for path in execution_files:
        source = _read(path)
        for token in forbidden:
            assert token not in source, f"{token} unexpectedly referenced by {path}"


def test_directional_ramp_mult_renders_value_not_unavailable():
    source = _read(TV_PANEL)
    display = _resolve_directional_ramp_display(ENGINE_A_DIAGNOSTIC_FIXTURE)

    assert ENGINE_A_DIAGNOSTIC_FIXTURE["factorDiagnostics"]["directionalRampMult"] == 1.0
    assert display == "1.00"
    assert display != "Unavailable"
    assert "resolveDirectionalRampDisplay" in source
    assert '<DiagnosticRow label="Directional ramp" display={directionalRamp} />' in source


def test_directional_ramp_missing_factor_diagnostics_reason():
    source = _read(DISPLAY_HELPERS)
    signal = {k: v for k, v in ENGINE_A_DIAGNOSTIC_FIXTURE.items() if k != "factorDiagnostics"}
    display = _resolve_directional_ramp_display(signal)

    assert display == "Unavailable — factorDiagnostics missing from signal payload"
    assert "factorDiagnostics missing from signal payload" in source


def test_directional_ramp_missing_key_reason():
    source = _read(DISPLAY_HELPERS)
    signal = {
        **ENGINE_A_DIAGNOSTIC_FIXTURE,
        "factorDiagnostics": {"trendCoherence": {"agreement_count": 1}},
    }
    display = _resolve_directional_ramp_display(signal)

    assert display == "Unavailable — factorDiagnostics.directionalRampMult missing from payload"
    assert "directionalRampMult missing from payload" in source


def test_trend_coherence_renders_agreement_and_ratio_explicitly():
    source = _read(TV_PANEL)
    coherence = ENGINE_A_DIAGNOSTIC_FIXTURE["factorDiagnostics"]["trendCoherence"]

    assert coherence["agreement_count"] == 3
    assert coherence["coherence_ratio"] == 1.0
    assert '<DiagnosticRow label="Agreement count" display={trendCoherenceRows.agreement} />' in source
    assert '<DiagnosticRow label="Coherence ratio" display={trendCoherenceRows.ratio} />' in source
    assert "resolveTrendCoherenceRows" in source


def test_feed_status_addon_renders_explicitly():
    source = _read(TV_PANEL)

    assert ENGINE_A_DIAGNOSTIC_FIXTURE["factorDiagnostics"]["feedStatus"]["addon"] == "CONFIRMING"
    assert '<DiagnosticRow label="Feed addon" display={feedAddon} />' in source
    assert "resolveFeedAddonDisplay" in source
    assert "factorDiagnostics.feedStatus.addon missing from payload" in _read(DISPLAY_HELPERS)


def test_atr_diagnostics_renders_provenance_fields():
    source = _read(TV_PANEL)

    assert 'label="ATR timeframe" display={atrProvenance.timeframe}' in source
    assert 'label="ATR source" display={atrProvenance.source}' in source
    assert 'label="ATR candle last ts" display={atrProvenance.candleLastTs}' in source
    assert 'label="ATR age seconds" display={atrProvenance.ageSeconds}' in source
    assert 'label="ATR confirmed only" display={atrProvenance.confirmedOnly}' in source
    assert "resolveAtrProvenanceRows" in source


def test_candle_fetch_meta_renders_cache_hit():
    source = _read(TV_PANEL)

    assert "Candle Fetch" in source
    assert "resolveCandleFetchMetaRows" in source
    assert "candleFetchMeta" in source
    assert ENGINE_A_DIAGNOSTIC_FIXTURE["candleFetchMeta"]["D1"]["cacheHit"] is True
    assert ENGINE_A_DIAGNOSTIC_FIXTURE["candleFetchMeta"]["H4"]["cacheHit"] is False


def test_explicit_engine_a_fields_precede_generic_diagnostic_blocks():
    source = _read(TV_PANEL)

    directional_idx = source.index('<DiagnosticRow label="Directional ramp"')
    trend_block_idx = source.index('<DiagnosticBlock label="Trend coherence"')
    feed_addon_idx = source.index('<DiagnosticRow label="Feed addon"')
    feed_block_idx = source.index('<DiagnosticBlock label="Feed status"')
    atr_tf_idx = source.index('label="ATR timeframe"')
    atr_block_idx = source.index('<DiagnosticBlock label="ATR diagnostics"')

    assert directional_idx < trend_block_idx
    assert feed_addon_idx < feed_block_idx
    assert atr_tf_idx < atr_block_idx
    assert 'className="truncate text-right text-foreground"' not in source


def test_scan_signal_json_roundtrip_preserves_engine_a_diagnostics():
    payload = {"signals": [dict(ENGINE_A_DIAGNOSTIC_FIXTURE)]}
    cleaned = _json_safe(payload)
    roundtripped = json.loads(json.dumps(cleaned))
    signal = roundtripped["signals"][0]

    fd = signal["factorDiagnostics"]
    assert fd["directionalRampMult"] == 1.0
    assert fd["trendCoherence"]["agreement_count"] == 3
    assert fd["trendCoherence"]["coherence_ratio"] == 1.0
    assert fd["feedStatus"]["addon"] == "CONFIRMING"
    assert signal["atrDiagnostics"]["atr_tf"] == "H4"
    assert signal["atrDiagnostics"]["atr_source"] == "engine_a"
    assert signal["atrDiagnostics"]["atr_candle_last_ts"] == "2026-05-21T12:00:00Z"
    assert signal["atrDiagnostics"]["atr_age_seconds"] == 120.5
    assert signal["atrDiagnostics"]["atr_confirmed_only"] is True
    assert signal["candleFetchMeta"]["D1"]["cacheHit"] is True
    assert signal["candleFetchMeta"]["H4"]["cacheHit"] is False


def test_frontend_build_marker_wired():
    vite_source = _read(VITE_CONFIG)
    main_source = _read(MAIN_TSX)
    panel_source = _read(TV_PANEL)

    assert "VITE_APP_BUILD_ID" in vite_source
    assert "athena-frontend-build" in vite_source
    assert "__ATHENA_FRONTEND__" in main_source
    assert "resolveFrontendBuildLabel" in panel_source
    assert "Frontend bundle:" in panel_source
    assert "isFrontendDebugVisible" in panel_source


def test_ai_review_types_include_engine_a_non_visual_context_fields():
    types_source = _read(ATHENA_TYPES)

    for token in [
        "EngineANonVisualContext",
        "AddonContext",
        "DerivativesContext",
        "MicrostructureContext",
        "IntermarketContext",
        "NewsContext",
        "ScoreAttribution",
        "nonVisualContext?: EngineANonVisualContext",
        "engineANonVisualContext?: EngineANonVisualContext",
        "scoreAttribution?: ScoreAttribution",
        "engineAScoreAttribution?: ScoreAttribution",
    ]:
        assert token in types_source


def test_ai_review_card_renders_engine_a_non_visual_context_panel():
    source = _read(ROOT / "static/react-app/app/src/components/athena/AIReviewCard.tsx")
    panel_source = _read(ROOT / "static/react-app/app/src/components/athena/EngineANonVisualContextPanel.tsx")

    assert "EngineANonVisualContextPanel" in source
    assert "Engine A non-visual context" in source
    assert "AI score mutation" in panel_source
    assert "Intermarket" in panel_source
    assert "Major event" in panel_source


def test_tv_chart_panel_sends_diagnostic_chart_snapshot_in_screenshot_meta():
    panel_source = _read(TV_PANEL)
    helper_source = _read(ROOT / "static/react-app/app/src/lib/aiChartReview.ts")

    assert "chart_snapshot:" in panel_source
    assert "renderedLayers" in panel_source
    assert "visibleCandleCount" in panel_source
    assert "indicatorLayerStates" in panel_source
    assert "engineBOverlayCount" in panel_source
    assert "priceRange" in panel_source
    assert "chart_snapshot?: AIChartReviewChartSnapshot" in _read(ATHENA_TYPES)
    assert "chart_snapshot: args.chart_snapshot" in helper_source


def test_carry_addon_fixture_displays_feed_status_when_missing_or_neutral():
    source = _read(TV_PANEL)
    assert ENGINE_A_DIAGNOSTIC_FIXTURE["type"] == "forex"
    assert "Carry addon" in source
    assert "firstString(feedStatus.addon)" in source or "resolveFeedAddonDisplay" in source


def test_missing_prior_swing_levels_keeps_sl_tp_and_atr_only_explanation():
    source = _read(TV_PANEL)
    assert "priorSwingLevels" not in ENGINE_A_DIAGNOSTIC_FIXTURE

    assert 'label="SL" value={signal?.sl}' in source
    assert 'label="TP" value={firstNumber(signal?.tp, signal?.tp1)}' in source
    assert "Unavailable — Engine A SL/TP is ATR-based; no structural swing levels supplied." in source


def test_key_engine_a_diagnostics_are_not_truncated_or_hidden():
    source = _read(TV_PANEL)

    assert "DiagnosticBlock" in source
    assert "JSON.stringify(record, null, 2)" in source
    assert 'label="Feed status" value={feedStatus}' in source
    assert 'label="Engine A asset diagnostics" value={engineAAssetDiagnostics}' in source
    assert 'label="ATR diagnostics" value={atrDiagnostics}' in source
    assert "engineAAssetDiagnostics" in source
    assert "resolveAtrProvenanceRows" in source


def test_engine_b_filled_zone_style_preserved():
    source = _read(TV_PANEL)
    primitive = _read(ROOT / "static/react-app/app/src/lib/engineBPrimitives.ts")

    assert "Do not replace filled support/resistance/FVG zones with line-only overlays" in source
    assert "ENGINE_B_ZONE_STYLE" in source
    assert "rgba(16, 185, 129, 0.18)" in primitive
    assert "rgba(244, 63, 94, 0.18)" in primitive
    assert "EngineBZonePrimitive" in source
    assert "context.fillRect(x1, y1" in primitive
    assert "buildEngineBZones" in source


def test_tv_chart_right_edge_label_layout_helper_wired():
    source = _read(TV_PANEL)
    labels = _read(ROOT / "static/react-app/app/src/lib/chartRightEdgeLabels.ts")
    primitive = _read(ROOT / "static/react-app/app/src/lib/chartRightEdgeLabelPrimitive.ts")

    assert "layoutRightEdgeLabels" in labels
    assert "RIGHT_EDGE_LABEL_PRIORITY" in labels
    assert "resolveRightEdgeLabels" in source
    assert "ChartRightEdgeLabelPrimitive" in source
    assert "ChartRightEdgeLabelPrimitive" in primitive
    assert "axisLabelVisible: false" in source


def test_tv_chart_compact_header_chips():
    source = _read(TV_PANEL)

    assert "ChartFeedHeaderChips" in source
    assert "chartFeedIdentityChips" in source
    assert "chartFeedDiagnosticsChips" in source
    assert "Exec:" in source
    assert "Candles:" in source


def test_tv_chart_review_rail_groups_ai_and_actions():
    source = _read(TV_PANEL)

    assert "data-review-rail" in source
    assert "data-review-action-strip" in source
    ai_idx = source.index("<AIReviewCard")
    execute_idx = source.index("Execute Now")
    rail_idx = source.index("data-review-rail")
    assert ai_idx > rail_idx
    assert execute_idx > rail_idx
    assert execute_idx < ai_idx + 1200


def test_tv_chart_engine_a_diagnostics_collapsed_by_default():
    source = _read(TV_PANEL)

    assert "Engine A diagnostics" in source
    assert "Engine B diagnostics" in source
    assert "<details" in source
    assert "EngineASidePanel" in source
    assert "EngineBSidePanel" in source
    assert "useEngineBPrimary" in source
    assert "scanCacheB" in source
    assert "AI_CHART_REVIEW_PRIMARY_ENGINE" in source


def test_ai_review_card_collapses_verbose_sections():
    source = _read(ROOT / "static/react-app/app/src/components/athena/AIReviewCard.tsx")

    assert "<Details" in source
    assert 'summary="Reasoning, risks and missing context"' in source
    assert 'summary="Evidence, context and provenance"' in source
