"""Lottery API route handlers and registration.

Behavior-neutral extraction from athena.py. Lottery AI stays separate from
Marcus/Text review and Chart Vision.
"""

from __future__ import annotations

import json
import logging
import re
import time
from types import SimpleNamespace

from flask import jsonify, request

from config import create_ai_client, get_ai_api_key, get_ai_model, get_ai_provider_label
from lottery_service import (
    add_lottery_draw,
    clear_lottery_draws,
    delete_lottery_draw,
    import_lottery_csv,
)
from lottery_engine import (
    LOTTERY_GAME_RULES,
    build_lottery_dashboard,
    compare_generator_modes,
    compute_bonus_intelligence,
    compute_entropy_analysis,
    compute_pair_lift,
    compute_number_frequency,
    compute_positional_distribution,
    compute_recommended_sum_range,
    compute_rolling_frequency,
    compute_overdue_numbers,
    compute_pair_frequency,
    compute_repeat_from_last_draw_stats,
    compute_consecutive_stats,
    compute_low_high_distribution,
    compute_odd_even_distribution,
    compute_sum_distribution,
    compute_triplet_frequency,
    flag_anomalous_draws,
    generate_tickets,
    generate_wheel,
    history_rows as lottery_history_rows,
    score_ticket,
    simulate_generator,
)

CONFIG: dict = {}
_AUDIT_DB = ""
log = logging.getLogger(__name__)


def api_lottery_import():
    try:
        payload = request.get_json(silent=True) or {}
        game = str(request.form.get("game") or payload.get("game") or "").strip().lower()
        mode = str(request.form.get("mode") or payload.get("mode") or "append").strip().lower()
        preview_flag = request.form.get("preview_only")
        if preview_flag is None:
            preview_flag = payload.get("preview_only")
        preview_only = str(preview_flag or "").strip().lower() in ("1", "true", "yes", "on")

        if mode not in ("append", "replace"):
            return jsonify({"error": "Invalid import mode"}), 400

        file_obj = request.files.get("file")
        csv_text = ""
        source_file = ""
        if file_obj:
            csv_text = file_obj.read().decode("utf-8-sig", errors="replace")
            source_file = file_obj.filename or ""
        else:
            csv_text = str(payload.get("csv_text") or "")
            source_file = str(payload.get("source_file") or "")

        if not game:
            return jsonify({"error": "Missing game"}), 400
        if not csv_text.strip():
            return jsonify({"error": "Missing CSV file"}), 400

        result = import_lottery_csv(
            _AUDIT_DB,
            game=game,
            csv_text=csv_text,
            source_file=source_file,
            mode=mode,
            preview_only=preview_only,
        )
        return jsonify(result), (400 if result.get("error") else 200)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _lottery_filter_args():
    game = str(request.args.get("game") or "").strip().lower()
    if not game:
        raise ValueError("Missing game")
    start_date = str(request.args.get("start_date") or "").strip() or None
    end_date = str(request.args.get("end_date") or "").strip() or None
    include_bonus_raw = str(request.args.get("include_bonus") or "false").strip().lower()
    include_bonus = include_bonus_raw in ("1", "true", "yes", "on")
    limit_raw = request.args.get("limit", 200)
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 200
    return game, start_date, end_date, include_bonus, max(1, min(limit, 2000))


def _lottery_ai_extract_json(raw_text: str) -> dict:
    text = str(raw_text or "").strip()
    if not text:
        return {}
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    candidates = [fenced, text]
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("AI response was not valid JSON")


def _lottery_ai_openai_message_text(content) -> str:
    """Extract assistant text from OpenAI-compatible chat completion ``message.content``."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    parts = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
        else:
            t = getattr(block, "text", None)
            if t:
                parts.append(str(t))
    return "\n".join(parts).strip()


def _lottery_ai_prompt_payload(
    game: str,
    start_date=None,
    end_date=None,
    window: int = 50,
    z_threshold: float = 2.5,
    db_path: str | None = None,
):
    game_key = str(game or "").strip().lower()
    rules = LOTTERY_GAME_RULES[game_key]
    board = build_lottery_dashboard(
        game_key,
        start_date=start_date,
        end_date=end_date,
        include_bonus=True,
        db_path=db_path,
    )
    if not board.get("total_draws"):
        raise ValueError("No draw history found - please import draws first")

    rolling = compute_rolling_frequency(
        game_key, window=window, start_date=start_date, end_date=end_date, db_path=db_path
    )
    pair_lift = compute_pair_lift(
        game_key, start_date=start_date, end_date=end_date, limit=50, db_path=db_path
    )
    anomalies = flag_anomalous_draws(
        game_key, z_threshold=z_threshold, start_date=start_date, end_date=end_date, db_path=db_path
    )
    bonus = compute_bonus_intelligence(
        game_key, window=window, start_date=start_date, end_date=end_date, db_path=db_path
    )
    entropy = compute_entropy_analysis(game_key, start_date=start_date, end_date=end_date, db_path=db_path)
    recent = lottery_history_rows(game_key, start_date=start_date, end_date=end_date, limit=10, db_path=db_path)

    recent_draws = "\n".join(
        [
            f"- {row.get('draw_date')}: {', '.join(str(n) for n in row.get('numbers', []))}"
            + (f" | bonus {row.get('bonus')}" if row.get("bonus") is not None else "")
            for row in recent.get("history", [])
        ]
    ) or "No recent draws"
    anomalous_rows = anomalies.get("anomalous_draws", [])
    anomalous_summary = (
        "\n".join(
            [
                f"- {row.get('draw_date')}: flags={', '.join(row.get('flags', []))}; "
                f"sum={row.get('metrics', {}).get('draw_sum')}"
                for row in anomalous_rows[:5]
            ]
        )
        if anomalous_rows
        else "No anomalous draws at this threshold."
    )
    most_recent_flag = ", ".join((anomalous_rows[-1].get("flags") or [])) if anomalous_rows else "None"

    rolling_rows = rolling.get("numbers", [])
    heating_numbers = [row["number"] for row in rolling_rows if row.get("z_score") is not None and float(row["z_score"]) > 1.5][:12]
    cooling_numbers = [row["number"] for row in rolling_rows if row.get("z_score") is not None and float(row["z_score"]) < -1.5][:12]
    overdue_lookup = {row["number"]: row for row in board.get("overdue_numbers", [])}
    overdue_neutral = [
        row["number"]
        for row in rolling_rows
        if row.get("trend") == "neutral" and row["number"] in overdue_lookup
    ][:12]
    overdue_list = "\n".join(
        [
            f"- {row['number']}: {row['draws_since_seen']} draws since seen (last seen {row.get('last_seen_date') or 'never'})"
            for row in board.get("overdue_numbers", [])[:10]
        ]
    ) or "None"
    pair_lift_list = "\n".join(
        [
            f"- {row['pair'][0]}-{row['pair'][1]}: lift {row['lift']}, conf {row['confidence_ab']}, count {row['observed']}"
            for row in pair_lift.get("pairs", [])
            if float(row.get("lift") or 0) > 1.5
        ][:10]
    ) or "None above 1.5"
    positional_ranges = "\n".join(
        [
            f"- Ball {row['position']}: min {row['min']}, p10 {row['p10']}, mean {row['mean']}, p90 {row['p90']}, max {row['max']}"
            for row in board.get("positional_distribution", [])
        ]
    ) or "Unavailable"
    bonus_top_picks = ", ".join(str(row["number"]) for row in bonus.get("top_picks", [])[:5]) if bonus.get("has_bonus") else "N/A"
    bonus_overdue = ", ".join(str(row["number"]) for row in bonus.get("overdue", [])[:5]) if bonus.get("has_bonus") else "N/A"
    bonus_heating = ", ".join(
        str(row["number"]) for row in bonus.get("rolling", []) if row.get("trend") == "heating"
    ) if bonus.get("has_bonus") else "N/A"

    # --- Entropy intelligence (lotto / powerball only) -----------------------
    entropy_section = ""
    if entropy and entropy.get("eligible"):
        pb = entropy.get("per_ball") or {}
        entropy_section = f"""
ENTROPY ANALYSIS (Shannon information theory):
H(X) = -Σ p(xi)·log2(p(xi)).  Max entropy (perfect uniform) = {entropy.get('h_max_bits')} bits.
Overall: {pb.get('entropy_bits')} bits - ratio {pb.get('entropy_ratio')} of maximum ({entropy.get('draw_count')} draws, {pb.get('total_observations')} ball observations)
Fairness verdict: {entropy.get('fairness')}
Rolling trend: {entropy.get('rolling_trend')}"""
        for rw in entropy.get("rolling", []):
            entropy_section += f"\n  Window {rw['window']}: {rw['entropy_bits']} bits (ratio {rw['entropy_ratio']})"
        low_entropy_positions = [
            p for p in entropy.get("positional", [])
            if p.get("entropy_ratio") is not None and float(p["entropy_ratio"]) < 0.85
        ]
        if low_entropy_positions:
            entropy_section += "\nLow-entropy positions (ratio < 0.85 - concentrated ranges):"
            for p in low_entropy_positions:
                entropy_section += f"\n  Ball {p['position']}: {p['entropy_bits']} bits (ratio {p['entropy_ratio']}, {p['distinct_values']} distinct values)"
        else:
            entropy_section += "\nAll ball positions have normal entropy (≥ 0.85 ratio)."

    rules_text = json.dumps(
        {
            "game": game_key,
            "main_count": rules["main_count"],
            "main_min": rules["main_min"],
            "main_max": rules["main_max"],
            "has_bonus": rules["has_bonus"],
            "bonus_min": rules.get("bonus_min"),
            "bonus_max": rules.get("bonus_max"),
        },
        indent=2,
    )
    sum_range = board.get("recommended_sum_range", {}) or {}
    sum_summary = board.get("sum_distribution_summary", {}) or {}
    prompt = f"""
You are a lottery number analyst. Analyse the following data for {game_key}
and recommend exactly which numbers to add to a wheeling system for
tonight's draw. Be precise and data-driven.

GAME RULES:
{rules_text}

RECENT DRAW HISTORY (last 10 draws):
{recent_draws}

ANOMALOUS DRAWS DETECTED:
{anomalous_summary}
Most recent anomaly flag: {most_recent_flag}

SMART SUM RANGE:
Recommended: {sum_range.get("min")} to {sum_range.get("max")} (covers 70% of historical draws)
Average sum: {sum_summary.get("avg_sum")}

NUMBER TEMPERATURE (rolling {window} draws):
Heating numbers (z > 1.5): {heating_numbers}
Cooling numbers (z < -1.5): {cooling_numbers}
Neutral but overdue: {overdue_neutral}

TOP OVERDUE NUMBERS:
{overdue_list}

TOP PAIR AFFINITIES (lift > 1.5):
{pair_lift_list}

BALL POSITION RANGES:
{positional_ranges}

BONUS BALL DATA (if applicable):
Top picks: {bonus_top_picks}
Most overdue bonus: {bonus_overdue}
Heating bonus balls: {bonus_heating}
{entropy_section}

IMPORTANT MATHEMATICAL CONTEXT:
- Each lottery draw is an independent event (memoryless property).
  Hot/cold/overdue patterns are descriptive, NOT predictive (Tversky & Kahneman, 1971).
- The ONLY mathematically justified strategy is anti-crowd: avoiding popular numbers
  to reduce jackpot split probability (Henze & Riedwyl, 1998 - ~20-30% higher expected
  payout, not higher win probability).
- Entropy ratio close to 1.0 confirms the draw is consistent with fair RNG.
  Deviations below 0.95 warrant investigation but are expected in small samples.
- Use entropy data to assess draw fairness and weight your confidence accordingly.
  If entropy is highly uniform, frequency deviations are just noise.
  If entropy shows notable deviation, frequency patterns carry more weight.

Based on ALL of the above data, provide:

1. RECOMMENDED POOL (10-12 numbers for the wheel):
   List exactly 10-12 main numbers with a one-line reason for each.
   Ensure coverage across all ball positions.
   Prioritise: overdue + heating > pair anchors > positional fillers.

2. NUMBERS TO AVOID TONIGHT:
   List 3-5 numbers to exclude and why.

3. GENERATOR MODE RECOMMENDATION:
   Which mode to use: pure_random / hot_bias / cold_bias /
   overdue_bias / balanced_mix / pair_bias / anti_crowd
   And why.

4. BONUS BALL PICK (if applicable):
   Top 2 bonus ball recommendations with reasoning.

5. SUM FILTER:
   Recommended min_sum and max_sum for scoring tonight
   based on anomaly context.

6. ENTROPY ASSESSMENT (if entropy data provided):
   Interpret the entropy ratio and rolling trend.
   Does entropy support or undermine frequency-based selections?
   Flag any low-entropy ball positions that suggest concentrated ranges.

7. CONFIDENCE LEVEL:
   Rate your confidence in this selection: Low / Medium / High
   Factor in: entropy fairness verdict, sample size, rolling trend,
   and the mathematical reality that lottery draws are independent events.

Respond in this exact JSON format:
{{
  "recommended_pool": [list of 10-12 integers],
  "avoid_numbers": [list of 3-5 integers],
  "generator_mode": "mode_name",
  "bonus_picks": [list of 2 integers or empty if no bonus],
  "sum_filter": {{"min": integer, "max": integer}},
  "entropy_assessment": {{
    "fairness": "highly_uniform/normal/notable_deviation/significant_deviation",
    "interpretation": "what the entropy tells us about this draw history",
    "impact_on_selection": "how entropy influenced the recommended pool"
  }},
  "confidence": "Low/Medium/High",
  "reasoning": {{
    "pool_reasoning": "explanation per number",
    "avoid_reasoning": "why avoid these",
    "mode_reasoning": "why this mode",
    "bonus_reasoning": "why these bonus balls",
    "entropy_reasoning": "how entropy data shaped the analysis",
    "confidence_reasoning": "what drives or limits confidence"
  }}
}}
""".strip()
    return {
        "prompt": prompt,
        "board": board,
        "rolling": rolling,
        "pair_lift": pair_lift,
        "anomalies": anomalies,
        "bonus": bonus,
        "entropy": entropy,
        "recent": recent,
        "rules": rules,
    }


def api_lottery_dashboard():
    try:
        game, start_date, end_date, include_bonus, _limit = _lottery_filter_args()
        payload = build_lottery_dashboard(
            game,
            start_date=start_date,
            end_date=end_date,
            include_bonus=include_bonus,
            db_path=_AUDIT_DB,
        )
        return jsonify(payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_frequency():
    try:
        game, start_date, end_date, include_bonus, _limit = _lottery_filter_args()
        freq = compute_number_frequency(
            game,
            start_date=start_date,
            end_date=end_date,
            include_bonus=include_bonus,
            db_path=_AUDIT_DB,
        )
        overdue = compute_overdue_numbers(
            game,
            start_date=start_date,
            end_date=end_date,
            include_bonus=include_bonus,
            db_path=_AUDIT_DB,
        )
        return jsonify(
            {
                "game": game,
                "draw_count": freq.get("draw_count", 0),
                "number_frequency": freq.get("main_number_frequency", []),
                "overdue": overdue.get("overdue", []),
                "decade_groups": freq.get("decade_groups", []),
                "bonus_frequency": freq.get("bonus_frequency", []),
                "bonus_overdue": overdue.get("bonus_overdue", []),
            }
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_pairs():
    try:
        game, start_date, end_date, _include_bonus, limit = _lottery_filter_args()
        return jsonify(
            compute_pair_frequency(
                game,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
                db_path=_AUDIT_DB,
            )
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_triplets():
    try:
        game, start_date, end_date, _include_bonus, limit = _lottery_filter_args()
        return jsonify(
            compute_triplet_frequency(
                game,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
                db_path=_AUDIT_DB,
            )
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_history():
    try:
        game, start_date, end_date, _include_bonus, limit = _lottery_filter_args()
        return jsonify(
            lottery_history_rows(
                game,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
                db_path=_AUDIT_DB,
            )
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_distributions():
    try:
        game, start_date, end_date, _include_bonus, _limit = _lottery_filter_args()
        return jsonify(
            {
                "game": game,
                "sum_distribution": compute_sum_distribution(
                    game, start_date=start_date, end_date=end_date, db_path=_AUDIT_DB
                ),
                "odd_even_distribution": compute_odd_even_distribution(
                    game, start_date=start_date, end_date=end_date, db_path=_AUDIT_DB
                ),
                "low_high_distribution": compute_low_high_distribution(
                    game, start_date=start_date, end_date=end_date, db_path=_AUDIT_DB
                ),
                "consecutive": compute_consecutive_stats(
                    game, start_date=start_date, end_date=end_date, db_path=_AUDIT_DB
                ),
                "repeat_from_last_draw": compute_repeat_from_last_draw_stats(
                    game, start_date=start_date, end_date=end_date, db_path=_AUDIT_DB
                ),
            }
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_sum_range():
    try:
        game, start_date, end_date, _include_bonus, _limit = _lottery_filter_args()
        return jsonify(
            compute_recommended_sum_range(
                game,
                start_date=start_date,
                end_date=end_date,
                db_path=_AUDIT_DB,
            )
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_positional():
    try:
        game, start_date, end_date, _include_bonus, _limit = _lottery_filter_args()
        return jsonify(
            compute_positional_distribution(
                game,
                start_date=start_date,
                end_date=end_date,
                db_path=_AUDIT_DB,
            )
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_rolling_frequency():
    try:
        game, start_date, end_date, _include_bonus, _limit = _lottery_filter_args()
        try:
            window = int(request.args.get("window", 50))
        except (TypeError, ValueError):
            window = 50
        return jsonify(
            compute_rolling_frequency(
                game,
                window=window,
                start_date=start_date,
                end_date=end_date,
                db_path=_AUDIT_DB,
            )
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_pair_lift():
    try:
        game, start_date, end_date, _include_bonus, limit = _lottery_filter_args()
        return jsonify(
            compute_pair_lift(
                game,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
                db_path=_AUDIT_DB,
            )
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_anomalous_draws():
    try:
        game, start_date, end_date, _include_bonus, _limit = _lottery_filter_args()
        try:
            z_threshold = float(request.args.get("z_threshold", 2.5))
        except (TypeError, ValueError):
            z_threshold = 2.5
        return jsonify(
            flag_anomalous_draws(
                game,
                z_threshold=z_threshold,
                start_date=start_date,
                end_date=end_date,
                db_path=_AUDIT_DB,
            )
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_bonus_intelligence():
    try:
        game, start_date, end_date, _include_bonus, _limit = _lottery_filter_args()
        try:
            window = int(request.args.get("window", 50))
        except (TypeError, ValueError):
            window = 50
        return jsonify(
            compute_bonus_intelligence(
                game,
                window=window,
                start_date=start_date,
                end_date=end_date,
                db_path=_AUDIT_DB,
            )
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_ai_analysis():
    try:
        ai_key = get_ai_api_key(CONFIG)
        if not ai_key:
            return jsonify({"error": "AI API key not configured"}), 500

        data = request.get_json(silent=True) or {}
        game = str(data.get("game") or "").strip().lower()
        if not game:
            return jsonify({"error": "Missing game"}), 400
        start_date = str(data.get("start_date") or "").strip() or None
        end_date = str(data.get("end_date") or "").strip() or None
        try:
            window = int(data.get("window", 50))
        except (TypeError, ValueError):
            window = 50
        try:
            z_threshold = float(data.get("z_threshold", 2.5))
        except (TypeError, ValueError):
            z_threshold = 2.5

        analytics = _lottery_ai_prompt_payload(
            game,
            start_date=start_date,
            end_date=end_date,
            window=window,
            z_threshold=z_threshold,
            db_path=_AUDIT_DB,
        )
        _lottery_model = (
            str(CONFIG.get("LOTTERY_AI_MODEL") or "").strip()
            or get_ai_model(CONFIG, "AI_MODEL", "grok-4.3")
        )
        _temp = float(CONFIG.get("AI_TEMPERATURE", 0.3))
        client = create_ai_client(CONFIG, api_key=ai_key)
        completion = client.chat.completions.create(
            model=_lottery_model,
            max_tokens=1800,
            temperature=_temp,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a lottery number analyst. Reply with a single valid JSON object "
                        "exactly matching the schema the user requested. No markdown fences, no preamble."
                    ),
                },
                {"role": "user", "content": analytics["prompt"]},
            ],
        )
        choice = completion.choices[0].message if completion.choices else None
        raw_text = _lottery_ai_openai_message_text(
            getattr(choice, "content", None) if choice else None
        )
        try:
            parsed = _lottery_ai_extract_json(raw_text)
        except ValueError as e:
            try:
                from ai_review_logger import (
                    AI_STATE_REVIEW_INCOMPLETE,
                    REVIEW_TYPE_LOTTERY_AI,
                    log_ai_review,
                )

                log_ai_review(
                    symbol=game,
                    asset_type="lottery",
                    review_type=REVIEW_TYPE_LOTTERY_AI,
                    model=_lottery_model,
                    provider=get_ai_provider_label(CONFIG),
                    prompt_version="lottery_ai_v1",
                    input_packet=analytics.get("prompt", ""),
                    has_chart_image=False,
                    candle_freshness_status="not_applicable",
                    engine_a_state=None,
                    engine_b_state=None,
                    engine_c_state=None,
                    engine_d_state=None,
                    risk_state=None,
                    ai_review_state=AI_STATE_REVIEW_INCOMPLETE,
                    ai_confidence=None,
                    contradictions_count=0,
                    missing_information_count=1,
                    parse_success=False,
                    schema_valid=False,
                    execution_allowed_before_ai=True,
                    execution_allowed_after_ai=True,
                    final_action="advisory",
                )
            except Exception as _log_exc:
                log.debug("[LOTTERY-AI] audit log failed: %s", _log_exc)
            return jsonify({"error": str(e)}), 400
        try:
            from ai_review_logger import (
                AI_STATE_CAUTION,
                REVIEW_TYPE_LOTTERY_AI,
                log_ai_review,
            )

            _required = {
                "recommended_pool",
                "avoid_numbers",
                "generator_mode",
                "confidence",
                "reasoning",
            }
            log_ai_review(
                symbol=game,
                asset_type="lottery",
                review_type=REVIEW_TYPE_LOTTERY_AI,
                model=_lottery_model,
                provider=get_ai_provider_label(CONFIG),
                prompt_version="lottery_ai_v1",
                input_packet=analytics.get("prompt", ""),
                has_chart_image=False,
                candle_freshness_status="not_applicable",
                engine_a_state=None,
                engine_b_state=None,
                engine_c_state=None,
                engine_d_state=None,
                risk_state=None,
                ai_review_state=AI_STATE_CAUTION,
                ai_confidence=None,
                contradictions_count=0,
                missing_information_count=0,
                parse_success=True,
                schema_valid=_required.issubset(parsed.keys()),
                execution_allowed_before_ai=True,
                execution_allowed_after_ai=True,
                final_action="advisory",
            )
        except Exception as _log_exc:
            log.debug("[LOTTERY-AI] audit log failed: %s", _log_exc)
        recommended_pool = [int(x) for x in (parsed.get("recommended_pool") or []) if str(x).strip()]
        avoid_numbers = [int(x) for x in (parsed.get("avoid_numbers") or []) if str(x).strip()]
        bonus_picks = [int(x) for x in (parsed.get("bonus_picks") or []) if str(x).strip()]
        return jsonify(
            {
                "analysis": parsed,
                "raw_analysis_text": raw_text,
                "model": _lottery_model,
                "recommended_pool": recommended_pool,
                "avoid_numbers": avoid_numbers,
                "generator_mode": str(parsed.get("generator_mode") or ""),
                "bonus_picks": bonus_picks[:2],
                "sum_filter": parsed.get("sum_filter", {}),
                "entropy_assessment": parsed.get("entropy_assessment", {}),
                "confidence": str(parsed.get("confidence") or ""),
                "reasoning": parsed.get("reasoning", {}),
            }
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_draws():
    """Backward-compatible alias for phase-1 draws endpoint."""
    try:
        game, start_date, end_date, _include_bonus, limit = _lottery_filter_args()
        return jsonify(
            lottery_history_rows(
                game,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
                db_path=_AUDIT_DB,
            )
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_stats():
    """Backward-compatible alias for phase-1 stats endpoint."""
    try:
        game, start_date, end_date, include_bonus, _limit = _lottery_filter_args()
        return jsonify(
            build_lottery_dashboard(
                game,
                start_date=start_date,
                end_date=end_date,
                include_bonus=include_bonus,
                db_path=_AUDIT_DB,
            )
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_clear():
    try:
        payload = request.get_json(silent=True) or {}
        game = str(payload.get("game") or request.form.get("game") or "").strip().lower() or None
        return jsonify(clear_lottery_draws(_AUDIT_DB, game=game))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_add_draw():
    """Manually enter a single draw result without uploading a CSV.

    POST JSON body:
      {
        "game":      "lotto" | "powerball" | "daily_lotto",
        "draw_date": "YYYY-MM-DD",
        "numbers":   [7, 14, 22, 31, 40, 51],   // main numbers (unsorted is fine)
        "bonus":     12                           // required for lotto/powerball, omit for daily_lotto
      }
    """
    try:
        payload = request.get_json(silent=True) or {}
        game = str(payload.get("game") or "").strip().lower()
        draw_date = str(payload.get("draw_date") or "").strip()
        numbers = payload.get("numbers")
        bonus = payload.get("bonus")

        if not game:
            return jsonify({"error": "Missing game"}), 400
        if not draw_date:
            return jsonify({"error": "Missing draw_date"}), 400
        if not numbers or not isinstance(numbers, list):
            return jsonify({"error": "numbers must be a non-empty list"}), 400

        result = add_lottery_draw(
            _AUDIT_DB,
            game=game,
            draw_date=draw_date,
            numbers=numbers,
            bonus=bonus,
            source_file="manual",
        )
        status = 200 if result["inserted"] else 409
        return jsonify(result), status
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_delete_draw():
    """Delete a specific draw by game + date.

    POST JSON body:
      { "game": "lotto", "draw_date": "2024-06-01" }
    """
    try:
        payload = request.get_json(silent=True) or {}
        game = str(payload.get("game") or "").strip().lower()
        draw_date = str(payload.get("draw_date") or "").strip()
        if not game:
            return jsonify({"error": "Missing game"}), 400
        if not draw_date:
            return jsonify({"error": "Missing draw_date"}), 400
        return jsonify(delete_lottery_draw(_AUDIT_DB, game=game, draw_date=draw_date))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_generate():
    try:
        payload = request.get_json(silent=True) or {}
        game = str(payload.get("game") or "").strip().lower()
        mode = str(payload.get("mode") or "pure_random").strip().lower()
        ticket_count = int(payload.get("ticket_count") or 5)
        include_bonus = bool(payload.get("include_bonus", False))
        start_date = payload.get("start_date")
        end_date = payload.get("end_date")
        filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}

        result = generate_tickets(
            game,
            mode=mode,
            ticket_count=ticket_count,
            include_bonus=include_bonus,
            filters=filters,
            start_date=start_date,
            end_date=end_date,
            db_path=_AUDIT_DB,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_wheel():
    try:
        payload = request.get_json(silent=True) or {}
        game = str(payload.get("game") or "").strip().lower()
        chosen_numbers = payload.get("chosen_numbers") or []
        guarantee_if = payload.get("guarantee_if")
        return jsonify(
            generate_wheel(
                game,
                chosen_numbers=chosen_numbers,
                guarantee_if=guarantee_if,
                db_path=_AUDIT_DB,
            )
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_score_ticket():
    try:
        payload = request.get_json(silent=True) or {}
        game = str(payload.get("game") or "").strip().lower()
        ticket = payload.get("ticket") or {}
        start_date = payload.get("start_date")
        end_date = payload.get("end_date")
        include_bonus = bool(payload.get("include_bonus", False))
        return jsonify(
            score_ticket(
                game,
                ticket=ticket,
                include_bonus=include_bonus,
                start_date=start_date,
                end_date=end_date,
                db_path=_AUDIT_DB,
            )
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_lottery_simulate():
    req_started = time.perf_counter()
    try:
        payload = request.get_json(silent=True) or {}
        game = str(payload.get("game") or "").strip().lower()
        mode = str(payload.get("mode") or "pure_random").strip().lower()
        tickets_per_draw = int(payload.get("tickets_per_draw") or 1)
        include_bonus = bool(payload.get("include_bonus", False))
        start_date = payload.get("start_date")
        end_date = payload.get("end_date")
        filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
        seed = None
        raw_seed = payload.get("seed")
        if raw_seed is not None:
            try:
                seed = int(raw_seed)
            except (TypeError, ValueError):
                seed = None
        ticket_cost = payload.get("ticket_cost")
        payout_table = payload.get("payout_table") or {}
        if isinstance(payout_table, str):
            payout_table = json.loads(payout_table or "{}")
        if not isinstance(payout_table, dict):
            payout_table = {}

        log.info(
            "[LotterySimAPI] request received game=%s mode=%s tickets_per_draw=%s start=%s end=%s include_bonus=%s",
            game,
            mode,
            tickets_per_draw,
            start_date,
            end_date,
            include_bonus,
        )

        result = simulate_generator(
            game,
            mode=mode,
            tickets_per_draw=tickets_per_draw,
            start_date=start_date,
            end_date=end_date,
            include_bonus=include_bonus,
            ticket_cost=ticket_cost,
            payout_table=payout_table,
            filters=filters,
            seed=seed,
            db_path=_AUDIT_DB,
        )
        serialize_started = time.perf_counter()
        response = jsonify(result)
        log.info(
            "[LotterySimAPI] response serialization complete game=%s mode=%s total_ms=%.1f serialize_ms=%.1f",
            game,
            mode,
            (time.perf_counter() - req_started) * 1000.0,
            (time.perf_counter() - serialize_started) * 1000.0,
        )
        return response
    except ValueError as e:
        log.warning("[LotterySimAPI] value error after %.1f ms: %s", (time.perf_counter() - req_started) * 1000.0, e)
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.exception("[LotterySimAPI] failure after %.1f ms", (time.perf_counter() - req_started) * 1000.0)
        return jsonify({"error": str(e)}), 500


def api_lottery_compare_modes():
    try:
        payload = request.get_json(silent=True) or {}
        game = str(payload.get("game") or "").strip().lower()
        modes = payload.get("modes") or []
        if isinstance(modes, str):
            modes = [m.strip() for m in modes.split(",") if m.strip()]
        if not isinstance(modes, list):
            modes = []
        tickets_per_draw = int(payload.get("tickets_per_draw") or 1)
        include_bonus = bool(payload.get("include_bonus", False))
        start_date = payload.get("start_date")
        end_date = payload.get("end_date")
        filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
        ticket_cost = payload.get("ticket_cost")
        payout_table = payload.get("payout_table") or {}
        if isinstance(payout_table, str):
            payout_table = json.loads(payout_table or "{}")
        if not isinstance(payout_table, dict):
            payout_table = {}

        seed = None
        raw_seed = payload.get("seed")
        if raw_seed is not None:
            try:
                seed = int(raw_seed)
            except (TypeError, ValueError):
                seed = None

        result = compare_generator_modes(
            game,
            modes=modes,
            tickets_per_draw=tickets_per_draw,
            start_date=start_date,
            end_date=end_date,
            include_bonus=include_bonus,
            ticket_cost=ticket_cost,
            payout_table=payout_table,
            filters=filters,
            seed=seed,
            db_path=_AUDIT_DB,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def register_lottery_routes(app, runtime: SimpleNamespace) -> None:
    """Register lottery routes using runtime state supplied by athena.py."""
    global CONFIG, _AUDIT_DB, log

    CONFIG = runtime.CONFIG
    _AUDIT_DB = runtime.AUDIT_DB
    log = runtime.log

    app.add_url_rule(
        "/api/lottery/import",
        "api_lottery_import",
        api_lottery_import,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/lottery/dashboard",
        "api_lottery_dashboard",
        api_lottery_dashboard,
    )
    app.add_url_rule(
        "/api/lottery/frequency",
        "api_lottery_frequency",
        api_lottery_frequency,
    )
    app.add_url_rule(
        "/api/lottery/pairs",
        "api_lottery_pairs",
        api_lottery_pairs,
    )
    app.add_url_rule(
        "/api/lottery/triplets",
        "api_lottery_triplets",
        api_lottery_triplets,
    )
    app.add_url_rule(
        "/api/lottery/history",
        "api_lottery_history",
        api_lottery_history,
    )
    app.add_url_rule(
        "/api/lottery/distributions",
        "api_lottery_distributions",
        api_lottery_distributions,
    )
    app.add_url_rule(
        "/api/lottery/sum-range",
        "api_lottery_sum_range",
        api_lottery_sum_range,
    )
    app.add_url_rule(
        "/api/lottery/positional",
        "api_lottery_positional",
        api_lottery_positional,
    )
    app.add_url_rule(
        "/api/lottery/rolling-frequency",
        "api_lottery_rolling_frequency",
        api_lottery_rolling_frequency,
    )
    app.add_url_rule(
        "/api/lottery/pair-lift",
        "api_lottery_pair_lift",
        api_lottery_pair_lift,
    )
    app.add_url_rule(
        "/api/lottery/anomalous-draws",
        "api_lottery_anomalous_draws",
        api_lottery_anomalous_draws,
    )
    app.add_url_rule(
        "/api/lottery/bonus-intelligence",
        "api_lottery_bonus_intelligence",
        api_lottery_bonus_intelligence,
    )
    app.add_url_rule(
        "/api/lottery/ai-analysis",
        "api_lottery_ai_analysis",
        api_lottery_ai_analysis,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/lottery/draws",
        "api_lottery_draws",
        api_lottery_draws,
    )
    app.add_url_rule(
        "/api/lottery/stats",
        "api_lottery_stats",
        api_lottery_stats,
    )
    app.add_url_rule(
        "/api/lottery/clear",
        "api_lottery_clear",
        api_lottery_clear,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/lottery/add-draw",
        "api_lottery_add_draw",
        api_lottery_add_draw,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/lottery/delete-draw",
        "api_lottery_delete_draw",
        api_lottery_delete_draw,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/lottery/generate",
        "api_lottery_generate",
        api_lottery_generate,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/lottery/wheel",
        "api_lottery_wheel",
        api_lottery_wheel,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/lottery/score-ticket",
        "api_lottery_score_ticket",
        api_lottery_score_ticket,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/lottery/simulate",
        "api_lottery_simulate",
        api_lottery_simulate,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/lottery/compare-modes",
        "api_lottery_compare_modes",
        api_lottery_compare_modes,
        methods=["POST"],
    )
