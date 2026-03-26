"""signal_debate.py — Multi-agent Bull/Bear/Judge debate for Sentinel Pro v4.0.

Before auto-execution, runs a structured 3-step debate using the xAI Grok API:
  1. BULL CASE: Make the strongest case FOR the trade
  2. BEAR CASE: Make the strongest case AGAINST the trade
  3. JUDGE: Evaluate both cases and grade: STRONG_GO / WEAK_GO / PASS / STRONG_AVOID

Based on TradingAgents (UCLA/MIT, arXiv 2024) research showing debate patterns
improve Sharpe ratio and reduce max drawdown vs single-pass analysis.

Only runs on auto-trade candidates (not manual scans) to control API costs.
~3 API calls per debate = ~$0.10/day at 3 trades/day with Grok.
"""

import json
import logging
import os

from ai_schemas import DebateCaseResponse, JudgeVerdictResponse
from ai_utils import parse_json_object

log = logging.getLogger("sentinel.debate")


def run_signal_debate(signal: dict, style_pref: str = "auto") -> dict:
    """Run a Bull/Bear/Judge debate for a trade signal.

    Args:
        signal: Full signal dict from the scanner
        style_pref: Trading style preference

    Returns:
        {
            "grade": "STRONG_GO" | "WEAK_GO" | "PASS" | "STRONG_AVOID",
            "bull_conviction": int (0-10),
            "bear_conviction": int (0-10),
            "reasoning": str,
            "score_adjustment": float,
            "allowed": bool
        }
    """
    api_key = os.environ.get("XAI_API_KEY", "")
    if not api_key:
        try:
            from config import CONFIG

            api_key = CONFIG.get("XAI_API_KEY", "")
            if api_key == "YOUR_XAI_API_KEY":
                api_key = ""
        except ImportError:
            pass
    if not api_key:
        return {
            "grade": "SKIP",
            "allowed": True,
            "reasoning": "No XAI_API_KEY — debate skipped",
            "bull_conviction": 0,
            "bear_conviction": 0,
            "score_adjustment": 0.0,
        }

    try:
        from config import CONFIG
    except Exception:
        CONFIG = {}

    pair = signal.get("display", signal.get("pair", "?"))
    direction = signal.get("direction", "?")
    score = signal.get("confluenceScore", 0)
    max_score = signal.get("maxScore", 13)
    regime = signal.get("trendState", "UNKNOWN")
    votes = signal.get("votes", {})
    asset_type = signal.get("type", "unknown")

    # Factor scores for AI context
    _factor_scores = signal.get("factor_scores", {})
    _factor_str = (
        " | ".join(
            f"{k}={v:.2f}" if v is not None else f"{k}=None"
            for k, v in _factor_scores.items()
        )
        if _factor_scores
        else "N/A"
    )
    _confidence = signal.get("confidence", "?")
    _warnings = signal.get("warnings", [])

    # Build signal context
    context = (
        f"Pair: {pair} | Direction: {direction} | Score: {score}/{max_score} "
        f"| Regime: {regime} | Asset: {asset_type}\n"
        f"Factor Scores: {_factor_str}\n"
        f"Confidence: {_confidence}\n"
        f"Votes: {json.dumps(votes, default=str)}\n"
        f"Entry: {signal.get('price')} | SL: {signal.get('sl')} | "
        f"TP1: {signal.get('tp1')} | TP2: {signal.get('tp2')}\n"
        f"R:R = 1:{signal.get('rr1', '?')} / 1:{signal.get('rr2', '?')}\n"
        f"Vol Ratio: {signal.get('volRatio', '?')} | "
        f"EMA200 Slope: {signal.get('ema200Slope', '?')}%\n"
        f"Warnings: {'; '.join(_warnings) if _warnings else 'None'}"
    )

    try:
        import openai

        client = openai.OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

        _model = CONFIG.get("DEBATE_MODEL") or CONFIG.get("XAI_MODEL", "grok-4.20-0309-reasoning")
        _temp = float(CONFIG.get("AI_TEMPERATURE", 0.3))

        # Step 1: Bull Case
        bull_response = _get_debate_case(client, context, direction, "BULL", _model, _temp)

        # Step 2: Bear Case
        bear_response = _get_debate_case(client, context, direction, "BEAR", _model, _temp)

        # Step 3: Judge
        judge_response = _get_judge_verdict(
            client, context, direction, bull_response, bear_response, _model, _temp
        )

        grade = judge_response.get("grade", "PASS")
        allowed = grade in ("STRONG_GO", "WEAK_GO")
        score_adj = judge_response.get("score_adjustment", 0.0)

        result = {
            "grade": grade,
            "bull_conviction": bull_response.get("conviction", 5),
            "bear_conviction": bear_response.get("conviction", 5),
            "bull_arguments": bull_response.get("key_arguments", []),
            "bear_arguments": bear_response.get("key_arguments", []),
            "reasoning": judge_response.get("reasoning", ""),
            "score_adjustment": score_adj,
            "allowed": allowed,
        }

        log.info(
            f"[DEBATE] {pair} {direction}: {grade} "
            f"(Bull:{result['bull_conviction']}/10, "
            f"Bear:{result['bear_conviction']}/10)"
        )

        return result

    except ImportError:
        log.warning("[DEBATE] openai library not installed — debate skipped")
        return {
            "grade": "SKIP",
            "allowed": True,
            "reasoning": "openai library not available",
            "bull_conviction": 0,
            "bear_conviction": 0,
            "score_adjustment": 0.0,
        }
    except Exception as e:
        log.error(f"[DEBATE] Error: {e}")
        return {
            "grade": "ERROR",
            "allowed": True,
            "reasoning": f"Debate error: {e}",
            "bull_conviction": 0,
            "bear_conviction": 0,
            "score_adjustment": 0.0,
        }


def _get_debate_case(
    client, context: str, direction: str, side: str, model: str, temperature: float
) -> dict:
    """Get bull or bear case from the LLM."""
    if side == "BULL":
        prompt = (
            f"You are a BULL analyst. Given this technical setup:\n{context}\n\n"
            f"Make the STRONGEST case FOR taking this {direction} trade.\n"
            f"Consider: supporting technical factors, regime alignment, "
            f"risk/reward ratio, momentum confirmation, and key levels.\n\n"
            f"Return ONLY valid JSON: "
            f'{{ "conviction": <0-10>, "key_arguments": ["arg1","arg2","arg3"], '
            f'"risk_factors": ["risk1","risk2"] }}'
        )
    else:
        prompt = (
            f"You are a BEAR analyst. Given this technical setup:\n{context}\n\n"
            f"Make the STRONGEST case AGAINST taking this {direction} trade.\n"
            f"Consider: contradicting signals, regime mismatches, macro risks, "
            f"overextension, and failure patterns.\n\n"
            f"Return ONLY valid JSON: "
            f'{{ "conviction": <0-10>, "key_arguments": ["arg1","arg2","arg3"], '
            f'"counter_risks": ["risk1","risk2"] }}'
        )

    system_prompt = (
        "You are a rigorous trading analyst. Use only provided signal data. "
        "Return valid JSON only."
    )

    try:
        completion = client.beta.chat.completions.parse(
            model=model,
            max_tokens=450,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            response_format=DebateCaseResponse,
        )
        if completion.choices[0].message.parsed:
            return completion.choices[0].message.parsed.model_dump()
    except Exception as _so_err:
        log.debug(f"[DEBATE] {side} structured output failed: {_so_err}")

    try:
        response = client.responses.create(
            model=model,
            max_output_tokens=450,
            temperature=temperature,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        )
        text = response.output_text.strip()
        parsed = parse_json_object(text)
        if parsed:
            return parsed
        return {"conviction": 5, "key_arguments": ["Parse error"], "risk_factors": []}
    except Exception as e:
        log.warning(f"[DEBATE] {side} case error: {e}")
        return {"conviction": 5, "key_arguments": [f"Error: {e}"], "risk_factors": []}


def _get_judge_verdict(
    client, context: str, direction: str, bull: dict, bear: dict, model: str, temperature: float
) -> dict:
    """Get judge verdict comparing bull and bear cases."""
    prompt = (
        f"You are an impartial JUDGE evaluating a {direction} trade.\n\n"
        f"Signal: {context}\n\n"
        f"BULL CASE (conviction: {bull.get('conviction', '?')}/10):\n"
        f"Arguments: {json.dumps(bull.get('key_arguments', []))}\n\n"
        f"BEAR CASE (conviction: {bear.get('conviction', '?')}/10):\n"
        f"Arguments: {json.dumps(bear.get('key_arguments', bear.get('counter_risks', [])))}\n\n"
        f"Evaluate both cases and give your verdict.\n"
        f"Return ONLY valid JSON: "
        f'{{ "grade": "<STRONG_GO|WEAK_GO|PASS|STRONG_AVOID>", '
        f'"reasoning": "<1-2 sentence verdict>", '
        f'"score_adjustment": <-1.0 to +1.0, how much to adjust signal score> }}'
    )

    system_prompt = (
        "You are an impartial risk committee judge. "
        "Weigh both cases and output strict JSON only."
    )

    try:
        completion = client.beta.chat.completions.parse(
            model=model,
            max_tokens=400,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            response_format=JudgeVerdictResponse,
        )
        if completion.choices[0].message.parsed:
            result = completion.choices[0].message.parsed.model_dump()
            valid_grades = {"STRONG_GO", "WEAK_GO", "PASS", "STRONG_AVOID"}
            if result.get("grade") not in valid_grades:
                result["grade"] = "PASS"
            return result
    except Exception as _so_err:
        log.debug(f"[DEBATE] Judge structured output failed: {_so_err}")

    try:
        response = client.responses.create(
            model=model,
            max_output_tokens=400,
            temperature=temperature,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        )
        text = response.output_text.strip()
        result = parse_json_object(text)
        if result:
            valid_grades = {"STRONG_GO", "WEAK_GO", "PASS", "STRONG_AVOID"}
            if result.get("grade") not in valid_grades:
                result["grade"] = "PASS"
            return result
        return {"grade": "PASS", "reasoning": "Parse error", "score_adjustment": 0.0}
    except Exception as e:
        log.warning(f"[DEBATE] Judge error: {e}")
        return {"grade": "PASS", "reasoning": f"Error: {e}", "score_adjustment": 0.0}
