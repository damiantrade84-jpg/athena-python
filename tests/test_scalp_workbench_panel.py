from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCALP_WORKBENCH = ROOT / "static/react-app/app/src/components/panels/ScalpWorkbenchPanel.tsx"
AI_SCALP_HELPER = ROOT / "static/react-app/app/src/lib/aiScalpChartReview.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_scalp_workbench_builds_compact_ai_snapshot_from_structured_contracts():
    source = _read(SCALP_WORKBENCH)
    chart_snapshot = _read(ROOT / "static/react-app/app/src/lib/scalpWorkbenchChart/chartSnapshot.ts")

    assert "buildScalpChartSnapshot" in chart_snapshot
    assert "profile:" in chart_snapshot
    assert "renderedLayers" in chart_snapshot
    assert "buildScalpChartSnapshot" in source
    assert "chart_snapshot:" in _read(AI_SCALP_HELPER)


def test_scalp_workbench_ai_capture_posts_server_trusted_review():
    panel = _read(SCALP_WORKBENCH)
    helper = _read(AI_SCALP_HELPER)

    assert "postScalpChartReview" in panel
    assert "AIReviewProviderToggle" in panel
    assert "aiReviewProvider" in panel
    assert "setAiReviewProvider" in panel
    assert "provider: aiReviewProvider" in panel
    assert "chart_snapshot:" in panel
    assert "rendered_layers" in panel
    assert "downscaleToCap" in panel
    assert "/api/ai/scalp-chart-review" in helper
    assert "postScalpChartReview" in helper
    assert "chart_snapshot" in helper
    assert "rendered_layers" in helper
    assert "ScalpAIReviewCard" in panel
    assert "scalpAiReviewResponse" in panel
    assert "postChartReview" not in panel
    assert "/api/ai/chart-review" not in panel
    assert "/api/auto-trade" not in panel

    capture_body = panel[panel.index("const captureScalpChartForAIReview"):]
    capture_body = capture_body[: capture_body.index("useEffect", 1)]
    assert "postScan('/api/scalp-scan'" not in capture_body
    assert "execution_tf: executionTf" in capture_body
    assert "chart_timeframe: timeframe" in capture_body


def test_scalp_workbench_refresh_remains_only_scan_trigger():
    source = _read(SCALP_WORKBENCH)

    assert source.count("postScan('/api/scalp-scan'") == 1
    assert "onClick={refreshScan}" in source
    assert "onClick={captureScalpChartForAIReview}" in source
    assert "captureScalpChartForAIReview" in source
    capture_body = source[source.index("const captureScalpChartForAIReview"):]
    capture_body = capture_body[: capture_body.index("useEffect", 1)]
    assert "postScan('/api/scalp-scan'" not in capture_body


def test_scalp_workbench_defaults_to_m5_display():
    source = _read(SCALP_WORKBENCH)

    assert "preferredScalpDisplayTf" in source
    assert "useState<(typeof TIMEFRAMES)[number]>('M5')" in source
    assert "M5 Context Chart" in source
    assert "Execution TF: M1" in source


def test_scalp_workbench_m1_still_available():
    source = _read(SCALP_WORKBENCH)

    assert "const TIMEFRAMES = ['M1', 'M5', 'M15']" in source
    assert "Display override" in source


def test_scalp_workbench_execution_tf_lock_present():
    source = _read(SCALP_WORKBENCH)

    assert "executionTf" in source
    assert "tfDisplayOverride" in source
    assert "disabled={!tfDisplayOverride}" in source
    assert "setTimeframe(executionTf)" not in source


def test_scalp_workbench_flag_watch_setup_button():
    source = _read(SCALP_WORKBENCH)

    assert "Flag / Watch Setup" in source
    assert "/api/suggested-trades/flag" in source
    assert "View Suggested Trades" in source
    flag_idx = source.index("Flag / Watch Setup")
    flag_section = source[flag_idx:flag_idx + 800]
    assert "postExecute('/api/scalp-execute'" not in flag_section


def test_scalp_workbench_execute_scalp_button():
    source = _read(SCALP_WORKBENCH)

    assert "Execute Scalp" in source
    assert "/api/scalp-execute" in source
    assert "buildScalpExecutePayload" in source
    assert "evaluateScalpExecuteBlock" in source
    assert "Confirm Scalp Execution" in source
    assert "refresh/revalidate before order" in source
    assert "{executeBlockReason &&" in source
    assert "executeBlockReason || 'Execute Scalp'" not in source


def test_scalp_workbench_view_suggested_trades_when_watches_exist():
    source = _read(SCALP_WORKBENCH)

    assert "showViewSuggestedTrades" in source
    assert "symbolWatches.length > 0" in source


def test_scalp_workbench_runner_status_badge():
    source = _read(SCALP_WORKBENCH)

    assert "useSuggestedTradeRunnerStatus" in source
    assert "Runner:" in source


def test_scalp_workbench_execute_disabled_unless_executable():
    source = _read(SCALP_WORKBENCH)
    helper = _read(ROOT / "static/react-app/app/src/lib/manualExecuteHelpers.ts")

    assert "executeBlockReason" in source
    assert "AI review required" in helper
    assert "scalpMechanicalGateBlock" in helper or "Grade D not executable" in helper


def test_scalp_workbench_consumes_scalp_workbench_intent():
    source = _read(SCALP_WORKBENCH)

    assert "scalpWorkbenchIntent" in source
    assert "appliedIntentIdRef" in source
    assert "Opened from Scalp Lab" in source


def test_scalp_workbench_symbol_select_stays_controlled_before_refresh():
    source = _read(SCALP_WORKBENCH)

    assert "EMPTY_SYMBOL_SELECT_VALUE" in source
    assert "value={activeSymbolKey || EMPTY_SYMBOL_SELECT_VALUE}" in source
    assert "if (key === EMPTY_SYMBOL_SELECT_VALUE) return;" in source
    assert '<SelectItem value={EMPTY_SYMBOL_SELECT_VALUE} disabled>' in source


def test_scalp_workbench_trade_skill_panel_fields():
    card = _read(ROOT / "static/react-app/app/src/components/athena/ScalpAIReviewCard.tsx")
    panel = _read(ROOT / "static/react-app/app/src/components/athena/TradeSkillReviewPanel.tsx")

    assert "TradeSkillReviewPanel" in card
    assert "Market state" in panel
    assert "Location" in panel
    assert "Aggression" in panel
    assert "Entry model" in panel
    assert "Invalidation" in panel


def test_scalp_workbench_execute_disabled_for_trade_skill_wait():
    helper = _read(ROOT / "static/react-app/app/src/lib/manualExecuteHelpers.ts")

    assert "tradeSkillBlocksExecute" in helper
    assert "WAIT_FOR_PULLBACK" in helper
    assert "WATCH_ONLY" in helper
    assert "NO_TRADE" in helper
    assert "entryAllowedNow" in helper


def test_scalp_workbench_shows_ai_grade_and_score():
    source = _read(SCALP_WORKBENCH)

    assert 'label="AI grade"' in source
    assert 'label="AI score"' in source


def test_scalp_workbench_ai_review_in_main_review_rail():
    source = _read(SCALP_WORKBENCH)

    assert 'data-review-rail' in source
    rail_idx = source.index('data-review-rail')
    ai_idx = source.index("<ScalpAIReviewCard")
    grid_idx = source.index("xl:grid-cols-[minmax(0,1fr)_360px]")
    assert ai_idx > grid_idx
    assert ai_idx > rail_idx


def test_scalp_workbench_execute_and_flag_near_ai_review():
    source = _read(SCALP_WORKBENCH)

    ai_idx = source.index("<ScalpAIReviewCard")
    execute_idx = source.index("Execute Scalp")
    flag_idx = source.index("Flag / Watch Setup")
    assert execute_idx < ai_idx + 800
    assert flag_idx < ai_idx + 800
    assert 'data-review-action-strip' in source


def test_scalp_workbench_diagnostics_collapsed():
    source = _read(SCALP_WORKBENCH)

    assert "Engine D diagnostics" in source
    assert "Candidate context and logs" in source
    assert "<details" in source


def test_scalp_workbench_debug_json_closed_by_default():
    source = _read(SCALP_WORKBENCH)

    assert "open={!scalpAiReviewResponse}" not in source
    assert "AI review debug" in source


def test_scalp_workbench_right_edge_label_layout():
    source = _read(SCALP_WORKBENCH)

    assert "ChartRightEdgeLabelPrimitive" in source
    assert "layoutRightEdgeLabels" in _read(ROOT / "static/react-app/app/src/lib/chartRightEdgeLabels.ts")
    assert "axisLabelVisible: false" in source


def test_scalp_workbench_builds_chart_snapshot_with_profile_liquidity_engineb_orderflow():
    source = _read(SCALP_WORKBENCH)
    helper_dir = ROOT / "static/react-app/app/src/lib/scalpWorkbenchChart"
    assert helper_dir.joinpath("chartSnapshot.ts").is_file()
    assert "buildScalpChartSnapshot" in helper_dir.joinpath("chartSnapshot.ts").read_text(encoding="utf-8")
    assert "profile:" in helper_dir.joinpath("chartSnapshot.ts").read_text(encoding="utf-8")
    assert "liquidity:" in helper_dir.joinpath("chartSnapshot.ts").read_text(encoding="utf-8")
    assert "engineBContext" in helper_dir.joinpath("chartSnapshot.ts").read_text(encoding="utf-8")
    assert "orderFlow:" in helper_dir.joinpath("chartSnapshot.ts").read_text(encoding="utf-8")
    assert "buildScalpChartSnapshot" in source
    assert "liquidityContext" in source


def test_scalp_workbench_review_button_enabled_for_ai_review_eligible_even_when_executable_false():
    source = _read(SCALP_WORKBENCH)
    helper = _read(ROOT / "static/react-app/app/src/lib/manualExecuteHelpers.ts")
    assert "isScalpAiReviewEligible" in helper
    assert "aiReviewEligible" in source
    assert "disabled={!aiReviewEligible" in source


def test_scalp_workbench_screenshot_meta_contains_rendered_layers():
    helper = _read(AI_SCALP_HELPER)
    source = _read(SCALP_WORKBENCH)
    assert "rendered_layers" in helper
    assert "chart_snapshot" in helper
    assert "renderedLayers" in source
    assert "missing_layers" in source


def test_scalp_workbench_execute_block_requires_ai_review():
    helper = _read(ROOT / "static/react-app/app/src/lib/manualExecuteHelpers.ts")
    assert "requiresScalpAiEntryNow" in helper
    assert "AI ENTRY_NOW required" in helper


def test_scalp_workbench_layout_keeps_chart_and_ai_verdict_visible():
    source = _read(SCALP_WORKBENCH)
    assert "data-review-rail" in source
    assert "ScalpAIReviewCard" in source
    assert "Order flow / source" in source
    assert "xl:grid-cols-[minmax(0,1fr)_360px]" in source


def test_scalp_workbench_overlay_toggles_present():
    source = _read(SCALP_WORKBENCH)
    assert "overlayToggles" in source
    assert "Auction Levels" in source
    assert "Engine B Structure" in source
    assert "ScalpThesisBadge" in source

