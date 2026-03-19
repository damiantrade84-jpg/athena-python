"""
engine_b_ai.py - AI integration for Engine B (Naked Structure Engine)

Adapts Engine A's AI design pattern for Engine B structural analysis.
"""
import logging
from typing import Optional

log = logging.getLogger("athena")


def build_engine_b_signal_message(
    pair: str,
    direction: str,
    current_price: float,
    structure_result: dict,
    confidence_result: dict,
    learning_ctx: Optional[dict] = None,
) -> str:
    """
    Build AI prompt message for Engine B structural signals.
    Follows Engine A pattern but emphasizes price action structure over indicators.
    
    Args:
        pair: Trading pair display name
        direction: LONG or SHORT
        current_price: Current market price
        structure_result: Output from NakedEngine.analyze_structure()
        confidence_result: Output from NakedEngine.calculate_confidence()
        learning_ctx: AI learning context from trade outcomes
        
    Returns:
        Formatted message string for AI analysis
    """
    lines = []
    
    # === SIGNAL ===
    conf_score = confidence_result.get("score", 0)
    max_score = confidence_result.get("max_possible", 3.0)
    score_pct = round((conf_score / max_score * 100)) if max_score else 0
    
    lines.append("=== ENGINE B SIGNAL (NAKED STRUCTURE) ===")
    lines.append(f"Pair: {pair} | Direction: {direction} | Price: {current_price}")
    lines.append(f"Confidence: {conf_score:.2f} / {max_score} ({score_pct}%)")
    lines.append(f"Verdict: {structure_result.get('structural_verdict', 'UNCLEAR')}")
    lines.append(f"Actionable: {'YES' if confidence_result.get('is_actionable', False) else 'NO'}")
    
    # === STRUCTURE ===
    lines.append("")
    lines.append("=== MARKET STRUCTURE ===")
    
    # Swing sequences
    seq_data = structure_result.get("sequence_data", {})
    macro_seq_data = structure_result.get("macro_sequence_data", {})
    lines.append(f"H1 Swing: {seq_data.get('state', 'RANGING')} (last: {seq_data.get('last_swing', 'N/A')})")
    lines.append(f"H4 Swing: {macro_seq_data.get('state', 'RANGING')} (last: {macro_seq_data.get('last_swing', 'N/A')})")
    
    # BOS and sweeps
    bos = structure_result.get("bos_data", {})
    sweep = structure_result.get("sweep_data", {})
    lines.append(f"Break of Structure: Bull={bos.get('bos_bull', False)} Bear={bos.get('bos_bear', False)}")
    lines.append(f"Liquidity Sweep: Bull={sweep.get('bull_sweep', False)} Bear={sweep.get('bear_sweep', False)}")
    
    # FVG
    fvg_count = len(structure_result.get("fvg_zones", []))
    fvg_overlap = structure_result.get("fvg_overlap_with_entry", False)
    lines.append(f"Fair Value Gaps: {fvg_count} detected, Entry overlap: {fvg_overlap}")
    
    # === LEVELS ===
    lines.append("")
    lines.append("=== STRUCTURAL LEVELS ===")
    
    res_zone = structure_result.get("nearest_resistance_zone")
    sup_zone = structure_result.get("nearest_support_zone")
    
    if res_zone:
        lines.append(f"Resistance: {res_zone.get('lower', 0):.6f} - {res_zone.get('upper', 0):.6f}")
    else:
        lines.append("Resistance: None detected")
        
    if sup_zone:
        lines.append(f"Support: {sup_zone.get('lower', 0):.6f} - {sup_zone.get('upper', 0):.6f}")
    else:
        lines.append("Support: None detected")
    
    dist_res = structure_result.get("distance_to_res", 0)
    dist_sup = structure_result.get("distance_to_sup", 0)
    lines.append(f"Distance to Resistance: {dist_res:.2f}%")
    lines.append(f"Distance to Support: {dist_sup:.2f}%")
    
    # === TRADE PARAMETERS ===
    lines.append("")
    lines.append("=== TRADE PARAMETERS ===")
    sl = structure_result.get("recommended_stop_loss")
    tp = structure_result.get("recommended_take_profit")
    rr = structure_result.get("risk_reward_ratio", 0)
    
    lines.append(f"Entry: {current_price}")
    lines.append(f"Stop Loss: {sl}")
    lines.append(f"Take Profit: {tp}")
    lines.append(f"Risk:Reward: 1:{rr:.2f}")
    
    # === CONFIDENCE BREAKDOWN ===
    lines.append("")
    lines.append("=== CONFIDENCE BREAKDOWN ===")
    lines.append(f"Structure Score: {confidence_result.get('structure_score', 0):.2f}")
    lines.append(f"Room Score: {confidence_result.get('room_score', 0):.2f}")
    lines.append(f"RR Score: {confidence_result.get('rr_score', 0):.2f}")
    lines.append(f"Catalyst Bonus: {confidence_result.get('catalyst_bonus', 0):.2f}")
    lines.append(f"AI Adjustment: {confidence_result.get('ai_adjustment', 0):.2f}")
    
    # === LEARNING CONTEXT ===
    if learning_ctx and learning_ctx.get("sample_size", 0) >= 5:
        lines.append("")
        lines.append("=== LEARNING CONTEXT (from live outcomes) ===")
        
        pair_stats = learning_ctx.get("pair_stats")
        if pair_stats:
            lines.append(
                f"This pair history: {pair_stats['win_rate']*100:.0f}% WR over "
                f"{pair_stats['total_trades']} trades (avg {pair_stats['avg_r']:+.2f}R)"
            )
        
        asset_stats = learning_ctx.get("asset_type_stats")
        if asset_stats:
            lines.append(
                f"Asset class: {asset_stats['win_rate']*100:.0f}% WR over "
                f"{asset_stats['total_trades']} trades"
            )
        
        # Recent failures
        recent_fails = learning_ctx.get("recent_failures", [])
        if recent_fails:
            fail_strs = [f"{f.get('pair','?')} {f.get('grade','?')} {f.get('r',0):+.1f}R" if isinstance(f, dict) else str(f) for f in recent_fails[:3]]
            lines.append(f"Recent failures: {', '.join(fail_strs)}")
    
    return "\n".join(lines)


def get_engine_b_ai_verdict(
    pair: str,
    direction: str,
    current_price: float,
    structure_result: dict,
    confidence_result: dict,
    learning_ctx: Optional[dict] = None,
    xai_api_key: str = None,
    xai_model: str = "grok-beta",
) -> dict:
    """
    Get AI analysis for Engine B signal using xAI Grok API.
    
    Returns dict with:
        - grade: A+ to F
        - edgeProbability: 0-100
        - riskLevel: LOW/MEDIUM/HIGH
        - verdict: text analysis
        - error: if failed
    """
    if not xai_api_key:
        log.warning("[ENGINE_B_AI] xAI API key not provided, skipping AI analysis")
        return {"error": "API key not configured"}
    
    try:
        import openai
        
        client = openai.OpenAI(api_key=xai_api_key, base_url="https://api.x.ai/v1")
        
        message = build_engine_b_signal_message(
            pair, direction, current_price, structure_result, confidence_result, learning_ctx
        )
        
        expert_prompt = """You are Marcus Reid, veteran SMC/ICT structural trader analyzing naked price action setups.
Focus on: swing structure alignment, BOS confirmation, liquidity sweeps, FVG overlap, zone quality, and risk:reward.
Output strict JSON: {"grade":"A+","edgeProbability":75,"riskLevel":"MEDIUM","verdict":"concise analysis"}
Grade scale: A+ (elite), A (strong), B (acceptable), C (marginal), D/F (reject)."""
        
        parsed = None
        
        # Try structured outputs first (guaranteed valid JSON)
        try:
            from ai_schemas import EngineBResponse
            completion = client.beta.chat.completions.parse(
                model=xai_model,
                max_tokens=800,
                messages=[
                    {"role": "system", "content": expert_prompt},
                    {"role": "user", "content": message}
                ],
                response_format=EngineBResponse,
            )
            if completion.choices[0].message.parsed:
                parsed = completion.choices[0].message.parsed.model_dump()
                log.debug(f"[ENGINE_B_AI] {pair}: structured output success")
        except Exception as _so_err:
            log.debug(f"[ENGINE_B_AI] {pair}: structured output failed ({_so_err}), using fallback")
        
        # Fallback to Responses API + manual parsing
        if parsed is None:
            response = client.responses.create(
                model=xai_model,
                max_output_tokens=800,
                input=[
                    {"role": "system", "content": expert_prompt},
                    {"role": "user", "content": message}
                ]
            )
            
            text = response.output_text.strip()
            import re
            import json
            
            # Try code fence
            if "```" in text:
                for p in text.split("```"):
                    p = p.strip()
                    if p.startswith("json"): p = p[4:].strip()
                    if p.startswith("{"):
                        try: parsed = json.loads(p[:p.rfind("}") + 1]); break
                        except json.JSONDecodeError: pass
            
            # Try regex
            if parsed is None:
                match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text)
                if match:
                    try: parsed = json.loads(match.group())
                    except json.JSONDecodeError: pass
            
            # Fallback to brace matching
            if parsed is None:
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    try: parsed = json.loads(text[start:end])
                    except json.JSONDecodeError: pass
        
        if parsed is None:
            log.error(f"[ENGINE_B_AI] {pair}: Failed to parse JSON from AI response")
            return {"error": "Invalid AI response format"}
        
        # Validate required keys
        required = {"grade", "edgeProbability", "riskLevel"}
        missing = required - set(parsed.keys())
        if missing:
            log.warning(f"[ENGINE_B_AI] {pair}: Missing keys {missing} in AI response")
        
        log.info(
            f"[ENGINE_B_AI] {pair} => Grade:{parsed.get('grade','?')} "
            f"Prob:{parsed.get('edgeProbability','?')}% Risk:{parsed.get('riskLevel','?')}"
        )
        
        return parsed
        
    except Exception as e:
        log.error(f"[ENGINE_B_AI] {pair}: AI analysis failed - {e}")
        return {"error": str(e)}
