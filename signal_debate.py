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
from concurrent.futures import ThreadPoolExecutor

from ai_safe_wrappers import ai_call_with_safe_default
from ai_schemas import DebateCaseResponse, JudgeVerdictResponse
from ai_signal_trace import ensure_trace_id
from ai_utils import parse_json_object
from config import (
    AISafetyConstants,
    AITemperatureConfig,
    CONFIG,
    create_ai_client,
    get_ai_api_key,
    get_ai_model,
    get_ai_provider_label,
)
from prompt_store import load_prompt

log = logging.getLogger("sentinel.debate")

_DEBATE_BULL_BEAR_SYSTEM_FALLBACK = (
    "You are a rigorous trading analyst. Use only provided signal data. "
    "Return valid JSON only."
)
_DEBATE_BULL_BEAR_SYSTEM, _DEBATE_BB_SOURCE, _DEBATE_BB_HASH = load_prompt(
    "debate_bull_bear_system",
    fallback=_DEBATE_BULL_BEAR_SYSTEM_FALLBACK,
)

_DEBATE_JUDGE_SYSTEM_FALLBACK = (
    "You are an impartial risk committee judge. "
    "Weigh both cases and output strict JSON only."
)
_DEBATE_JUDGE_SYSTEM, _DEBATE_JUDGE_SOURCE, _DEBATE_JUDGE_HASH = load_prompt(
    "debate_judge_system",
    fallback=_DEBATE_JUDGE_SYSTEM_FALLBACK,
)


def _debate_timeout_sec() -> float:
    """Per-call HTTP timeout for debate LLM requests (seconds)."""
    raw = CONFIG.get("DEBATE_AI_TIMEOUT_SEC")
    if raw is None:
        raw = CONFIG.get("AI_REQUEST_TIMEOUT_SEC")
    try:
        t = float(raw if raw is not None else 30.0)
    except (TypeError, ValueError):
        t = 30.0
    return t if t > 0 else 30.0


def _finalize_score_adjustment(raw: object) -> tuple[float, float]:
    """Return (raw_as_float, effective_adjustment) with effective <= 0 (Audit CRIT-002)."""
    try:
        raw_f = float(raw if raw is not None else 0.0)
    except (TypeError, ValueError):
        raw_f = 0.0
    effective = min(0.0, raw_f)
    if AISafetyConstants.FORCE_DEBATE_DOWNGRADE_ONLY and raw_f > 0:
        log.critical(
            "[DEBATE] Positive score_adjustment %.4f rejected — downgrade-only policy (CRIT-002).",
            raw_f,
        )
    return raw_f, effective


def _signal_max_score(signal: dict) -> float:
    """Return the max possible score for the signal's asset class / engine.

    Priority:
      1. Explicit ``maxScore`` on the signal (Engine D sets 1.0, Engine A sets 3.0).
      2. Engine tag: ``engine == "SCALP"`` -> 1.0 (Engine D normalised scale).
      3. Default: 3.0 (Engine A v2 unified).

    The old heuristic (confluenceScore <= 1.0 -> maxScore=1.0) was removed
    because Engine A can produce confluenceScore < 1.0 on its 0-3 scale.
    """
    raw = signal.get("maxScore")
    if raw is not None:
        try:
            val = float(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    if signal.get("engine") == "SCALP":
        return 1.0
    return 3.0


@ai_call_with_safe_default("debate", max_retries=1)
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
    ensure_trace_id(signal)
    api_key = get_ai_api_key(CONFIG)
    _fail_policy = str(CONFIG.get("AUTO_TRADE_AI_FAIL_POLICY", "block")).lower()
    _allowed_on_fail = (_fail_policy != "block")

    if not api_key:
        return {
            "grade": "SKIP",
            "allowed": _allowed_on_fail,
            "reasoning": "No AI API key configured — debate skipped",
            "bull_conviction": 0,
            "bear_conviction": 0,
            "score_adjustment": 0.0,
            "score_adjustment_raw": 0.0,
            "trace_id": signal.get("trace_id"),
        }


    pair = signal.get("display", signal.get("pair", "?"))
    direction = signal.get("direction", "?")
    score = signal.get("confluenceScore", 0)
    max_score = _signal_max_score(signal)
    regime = signal.get("trendState", "UNKNOWN")
    votes = signal.get("votes", {})
    asset_type = signal.get("type", "unknown")

    # Factor scores for AI context
    _factor_scores = signal.get("factorScores")
    if not isinstance(_factor_scores, dict):
        _factor_scores = signal.get("factor_scores", {})
    _factor_str = (
        " | ".join(
            f"{k}={v:.2f}" if v is not None else f"{k}=None"
            for k, v in _factor_scores.items()
        )
        if _factor_scores
        else "N/A"
    )
    _confidence = signal.get("confidence")
    if _confidence in (None, "?"):
        _confidence = (signal.get("confidenceDetail") or {}).get("confidence", "?")
    _warnings = signal.get("warnings", [])

    # Build signal context using unified calibration context
    try:
        from ai_context import build_ai_calibration_context_string
        _calib_str = build_ai_calibration_context_string(signal, engine_source="Auto-Trade Debate Gate", explicit_style=style_pref)
        _base_context = _calib_str + "\n\n=== ADDITIONAL DETAILS ===\n" + (
            f"Votes: {json.dumps(votes, default=str)}\n"
            f"EMA200 Slope: {signal.get('ema200Slope', '?')}%\n"
            f"Warnings: {'; '.join(_warnings) if _warnings else 'None'}"
        )
    except Exception as _calib_err:
        log.debug("[DEBATE] build_ai_calibration_context_string failed: %s", _calib_err)
        _base_context = (
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
        from ai_utils import build_freshness_ai_context as _build_freshness
        _freshness_str = _build_freshness(signal)
        context = _base_context + ("\n\n" + _freshness_str if _freshness_str else "")
    except Exception:
        context = _base_context


    try:
        client = create_ai_client(CONFIG, api_key=api_key)
        _model = get_ai_model(CONFIG, "DEBATE_MODEL", "grok-4.5")
        _temp_bull = AITemperatureConfig.get_temperature("debate_bull")
        _temp_bear = AITemperatureConfig.get_temperature("debate_bear")
        _temp_judge = AITemperatureConfig.get_temperature("debate_judge")
        _http_timeout = _debate_timeout_sec()

        # Steps 1+2: Bull and Bear in parallel (independent calls).
        # Worst-case latency drops from 3 sequential calls to 2 (bull||bear → judge).
        if bool(CONFIG.get("DEBATE_PARALLEL_BULL_BEAR", True)):
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="debate") as pool:
                bull_future = pool.submit(
                    _get_debate_case,
                    client,
                    context,
                    direction,
                    "BULL",
                    _model,
                    _temp_bull,
                    _http_timeout,
                )
                bear_future = pool.submit(
                    _get_debate_case,
                    client,
                    context,
                    direction,
                    "BEAR",
                    _model,
                    _temp_bear,
                    _http_timeout,
                )
                bull_response = bull_future.result()
                bear_response = bear_future.result()
        else:
            bull_response = _get_debate_case(
                client, context, direction, "BULL", _model, _temp_bull, _http_timeout
            )
            bear_response = _get_debate_case(
                client, context, direction, "BEAR", _model, _temp_bear, _http_timeout
            )

        # Step 3: Judge (depends on both; no duplicate full context — see _get_judge_verdict)
        judge_response = _get_judge_verdict(
            client,
            direction,
            bull_response,
            bear_response,
            _model,
            _temp_judge,
            pair=str(pair),
            score=score,
            max_score=max_score,
            regime=str(regime),
            timeout_sec=_http_timeout,
        )

        grade = judge_response.get("grade", "PASS")
        allowed = grade in ("STRONG_GO", "WEAK_GO")
        raw_adj, score_adj = _finalize_score_adjustment(
            judge_response.get("score_adjustment", 0.0)
        )

        result = {
            "grade": grade,
            "bull_conviction": bull_response.get("conviction", 5),
            "bear_conviction": bear_response.get("conviction", 5),
            "bull_arguments": bull_response.get("key_arguments", []),
            "bear_arguments": bear_response.get("key_arguments", []),
            "reasoning": judge_response.get("reasoning", ""),
            "score_adjustment": score_adj,
            "score_adjustment_raw": raw_adj,
            "allowed": allowed,
            "trace_id": signal.get("trace_id"),
        }

        log.info(
            f"[DEBATE] {pair} {direction}: {grade} "
            f"(Bull:{result['bull_conviction']}/10, "
            f"Bear:{result['bear_conviction']}/10)"
        )

        # Per-debate audit row so prompts/inputs are reproducible from JSONL store.
        try:
            from ai_review_logger import (
                REVIEW_TYPE_SIGNAL_DEBATE,
                log_ai_review,
                map_debate_grade_to_ai_state,
            )
            from prompt_versions import get_prompt_version

            log_ai_review(
                symbol=pair,
                asset_type=str(asset_type or "unknown"),
                review_type=REVIEW_TYPE_SIGNAL_DEBATE,
                model=_model,
                provider=get_ai_provider_label(CONFIG),
                prompt_version=get_prompt_version("debate_judge"),
                input_packet={
                    "context": context,
                    "direction": direction,
                    "score": score,
                    "max_score": max_score,
                    "regime": regime,
                    "bull_conviction": result["bull_conviction"],
                    "bear_conviction": result["bear_conviction"],
                    "bull_arguments": result["bull_arguments"],
                    "bear_arguments": result["bear_arguments"],
                    "judge_reasoning": result["reasoning"],
                },
                has_chart_image=False,
                candle_freshness_status=(signal.get("dataFreshness") or {}).get("reason")
                if isinstance(signal.get("dataFreshness"), dict)
                else None,
                engine_a_state=signal.get("confluenceScore"),
                engine_b_state=(signal.get("engine_b") or {}).get("score_pct")
                if isinstance(signal.get("engine_b"), dict)
                else None,
                engine_c_state=signal.get("combinedConviction"),
                engine_d_state=None,
                risk_state={
                    "rr1": signal.get("rr1"),
                    "score_adjustment": score_adj,
                    "score_adjustment_raw": raw_adj,
                },
                ai_review_state=map_debate_grade_to_ai_state(grade),
                ai_confidence=(result["bull_conviction"] - result["bear_conviction"])
                * 10.0
                + 50.0,
                contradictions_count=0,
                missing_information_count=0,
                parse_success=True,
                schema_valid=grade in ("STRONG_GO", "WEAK_GO", "PASS", "STRONG_AVOID"),
                execution_allowed_before_ai=True,
                execution_allowed_after_ai=allowed,
                final_action="advisory" if allowed else "blocked",
                trace_id=signal.get("trace_id"),
            )
        except Exception as _audit_err:
            log.debug("[DEBATE] audit log failed: %s", _audit_err)

        return result

    except ImportError:
        log.warning("[DEBATE] openai library not installed — debate skipped")
        return {
            "grade": "SKIP",
            "allowed": _allowed_on_fail,
            "reasoning": "openai library not available",
            "bull_conviction": 0,
            "bear_conviction": 0,
            "score_adjustment": 0.0,
            "score_adjustment_raw": 0.0,
            "trace_id": signal.get("trace_id"),
        }
    except Exception as e:
        log.error(f"[DEBATE] Error: {e}")
        _allowed_exc = (
            False
            if AISafetyConstants.DEBATE_FAILURE_DEFAULTS_TO_BLOCK
            else _allowed_on_fail
        )
        return {
            "grade": "ERROR",
            "allowed": _allowed_exc,
            "reasoning": f"Debate error: {e}",
            "bull_conviction": 0,
            "bear_conviction": 0,
            "score_adjustment": 0.0,
            "score_adjustment_raw": 0.0,
            "trace_id": signal.get("trace_id"),
        }



def _get_debate_case(
    client,
    context: str,
    direction: str,
    side: str,
    model: str,
    temperature: float,
    timeout_sec: float,
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

    system_prompt = _DEBATE_BULL_BEAR_SYSTEM

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
            timeout=timeout_sec,
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
            timeout=timeout_sec,
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
    client,
    direction: str,
    bull: dict,
    bear: dict,
    model: str,
    temperature: float,
    *,
    pair: str,
    score: object,
    max_score: float,
    regime: str,
    timeout_sec: float,
) -> dict:
    """Get judge verdict comparing bull and bear cases.

    Full signal context was already supplied to Bull/Bear; judge sees only a
    one-line summary plus both cases to avoid duplicating the entire prompt.
    """
    prompt = (
        f"You are an impartial JUDGE evaluating a {direction} trade on {pair}.\n\n"
        "Bull and Bear analysts already received the full signal context. "
        "Do not invent facts beyond their arguments and the summary line below.\n\n"
        f"SUMMARY: direction={direction} | score={score}/{max_score} | regime={regime}\n\n"
        f"BULL CASE (conviction: {bull.get('conviction', '?')}/10):\n"
        f"Arguments: {json.dumps(bull.get('key_arguments', []))}\n\n"
        f"BEAR CASE (conviction: {bear.get('conviction', '?')}/10):\n"
        f"Arguments: {json.dumps(bear.get('key_arguments', bear.get('counter_risks', [])))}\n\n"
        f"Evaluate both cases and give your verdict.\n"
        f"Return ONLY valid JSON: "
        f'{{ "grade": "<STRONG_GO|WEAK_GO|PASS|STRONG_AVOID>", '
        f'"reasoning": "<1-2 sentence verdict>", '
        f'"score_adjustment": <=0.0 penalty only (never positive; downgrade-only policy) }}'
    )

    system_prompt = _DEBATE_JUDGE_SYSTEM

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
            timeout=timeout_sec,
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
            timeout=timeout_sec,
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
