from vision_prompts import (
    build_dual_prompt,
    build_policy_prompt,
    build_single_prompt,
    build_system_prompt,
    build_triple_prompt,
)


def _build_payload(prompt: str) -> list[str]:
    return [line.strip() for line in prompt.strip().splitlines() if line.strip()]


def test_system_prompt_mentions_context_first_rules():
    prompt = build_system_prompt()
    assert "Trend structure first" in prompt
    assert "Candle anatomy next" in prompt
    assert "Pattern context" in prompt
    assert "Confirmation rule" in prompt


def test_single_prompt_contains_footer_contract_and_ae_order():
    prompt = build_single_prompt(
        symbol="EUR/USD",
        tf="H4",
        direction_str="LONG",
        algo_context="ENGINE A: direction=LONG",
        asset_type="forex",
    )
    assert "RIGHT EDGE: <CONFIRMS|REVIEW|POTENTIAL REVERSAL>" in prompt
    assert "TF ALIGNMENT: ALIGNED or CONFLICTED" in prompt
    assert "SCALP RATING:" in prompt
    assert "SCALP LEVELS: SL=<KEEP|price> TP=<KEEP|price>" in prompt
    assert "INTRADAY RATING:" in prompt
    assert "INTRADAY LEVELS: SL=<KEEP|price> TP=<KEEP|price>" in prompt
    assert "SWING RATING:" in prompt
    assert "SWING LEVELS: SL=<KEEP|price> TP=<KEEP|price>" in prompt

    a = prompt.index("A. Trend/Location")
    b = prompt.index("B. Last Candle Anatomy")
    c = prompt.index("C. Last 3/5 Candle Sequence")
    d = prompt.index("D. Pattern ID")
    e = prompt.index("E. Auction Behaviour")
    f = prompt.index("F. Confirmation")
    g = prompt.index("G. Style Suitability")
    h = prompt.index("H. Verdict")
    assert a < b < c < d < e < f < g < h
    assert "entry quality" in prompt.lower()
    assert "right edge a-h framework" in prompt.lower()
    assert "REQUEST_METADATA: symbol=EUR/USD" in prompt
    assert "REQUEST_METADATA: direction_bias=LONG" in prompt
    assert "REQUEST_METADATA: primary_chart_tf=H4" in prompt


def test_dual_prompt_uses_h4_authoritative_right_edge():
    prompt = build_dual_prompt(
        symbol="BTCUSDT",
        direction_str="SHORT",
        algo_context="ENGINE B: bos_mtf_confirmed=True",
        asset_type="crypto",
    )
    assert "authoritative TF: H4" in prompt
    # After audit fix: rule maps to RIGHT EDGE classification (parsed value),
    # not the prose-only FINAL VERDICT field which was dropped.
    assert "If H4 right edge does not confirm direction" in prompt
    assert "POTENTIAL REVERSAL" in prompt
    assert "REQUEST_METADATA: symbol=BTCUSDT" in prompt
    assert "REQUEST_METADATA: chart_frames=D1+H4" in prompt


def test_triple_prompt_uses_h1_authoritative_right_edge():
    prompt = build_triple_prompt(
        symbol="XAU/USD",
        direction_str="LONG",
        algo_context="ENGINE B: choch_bear=False",
        asset_type="commodity",
    )
    assert "authoritative TF: H1" in prompt
    assert "counter-trend with rising volume" in prompt
    assert "End with exactly these 8 lines" in prompt
    assert "REQUEST_METADATA: symbol=XAU/USD" in prompt
    assert "REQUEST_METADATA: chart_frames=D1+H4+H1" in prompt


def test_policy_prompt_labels_selected_style_and_authoritative_tf():
    prompt = build_policy_prompt(
        symbol="EUR/USD",
        direction_str="LONG",
        algo_context="TIMEFRAME POLICY: setup=M30",
        asset_type="forex",
        frames=["D1", "H4", "H1", "M30"],
        authority_tf="M30",
        selected_style="intraday",
    )

    assert "IMAGE 4 = M30" in prompt
    assert "REQUEST_METADATA: primary_chart_tf=M30" in prompt
    assert "REQUEST_METADATA: selected_style=INTRADAY" in prompt
    assert "M30 is the authoritative RIGHT EDGE chart" in prompt
    assert "Non-selected style ratings are comparison-only" in prompt
