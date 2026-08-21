"""Engine B top-down ICT / Smart Money Concepts hierarchy.

Implements the hierarchical model drawn from Michael Huddleston (ICT) teaching
and Ali Khan's timeframe framework:

    HTF bias  (Daily primary for intraday; Weekly+Daily for swing)
        -> MTF confirmation (H4/H1 reaction at the HTF key level)
            -> LTF entry (M15/M5 trigger, only valued when HTF+MTF align)

Core narrative per side: a *liquidity sweep* on the higher timeframe, followed
by *displacement* (close-based break of structure / large-bodied move), leaving
an unmitigated *Fair Value Gap* or strong *Order Block* behind. That sequence —
not any single indicator — is what establishes directional bias.

This module is intentionally pure: it consumes plain dicts produced by
``market_structure.NakedEngine.precompute_structure_data`` (D1 sweep/BOS/FVG/OB
evidence, D1 swing sequence, resampled weekly evidence) plus per-direction
MTF/LTF evidence booleans resolved by ``calculate_confidence``. It reads no
globals — tunables arrive via the ``config`` argument (see ``DEFAULT_CONFIG``)
— and it must never import ``market_structure`` (which imports this module).

Modes (``ENGINE_B_BIAS_MODE``):
    ``legacy``       — hierarchy is evaluated and logged for diagnostics only;
                       scoring and gates are untouched (full backward compat).
    ``hierarchical`` — soft weighting. A valid aligned Daily bias keeps the
                       score whole (plus a confirmation bonus when MTF also
                       aligns); an unclear Daily narrative cuts confidence
                       (``UNCLEAR_SCORE_MULT``); conflicting Daily evidence or
                       a candidate running counter to a valid Daily bias blocks
                       (or heavily penalises, per config).
    ``strict``       — sequential state machine. State 1 HTF_BIAS must be valid
                       and aligned; only then State 2 MTF_CONFIRMATION is
                       evaluated; only then State 3 LTF_ENTRY. Any failed state
                       blocks the candidate with a stage-specific reason.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

HIERARCHY_VERSION = "engine_b.hierarchy.v1"

BIAS_MODES = ("legacy", "hierarchical", "strict")

# Block / decision reason codes (also surfaced via failed_gate_names).
REASON_HTF_BIAS_UNCLEAR = "htf_bias_unclear"
REASON_HTF_BIAS_CONFLICTING = "htf_bias_conflicting"
REASON_COUNTER_HTF_BIAS = "counter_htf_bias"
REASON_MTF_CONFIRMATION_MISSING = "mtf_confirmation_missing"
REASON_LTF_ENTRY_MISSING = "ltf_entry_missing"

DEFAULT_CONFIG = {
    # ── HTF bias evidence weights (points per side) ──────────────────────────
    "WEIGHT_SWEEP": 3.0,            # D1 liquidity sweep of a prior swing
    "WEIGHT_DISPLACEMENT_BOS": 2.0,  # D1 close-based BOS = displacement through structure
    "WEIGHT_FVG_NARRATIVE": 2.0,    # unmitigated, displacement-backed D1 FVG
    "WEIGHT_ORDER_BLOCK": 1.5,      # unmitigated D1 order block
    "WEIGHT_SEQUENCE": 1.0,         # D1 swing sequence HH_HL / LH_LL (supporting only)
    "WEIGHT_WEEKLY": 2.0,           # swing mode: weekly sequence/BOS agreement
    # Minimum dominant-side points for a valid bias. Sized so a lone D1 sweep
    # (3.0) reaches the points bar but REQUIRE_EVIDENCE_CORROBORATION (below)
    # still denies it validity without a second, independent narrative leg.
    "MIN_BIAS_SCORE": 3.0,
    # A single evidence kind — however weighted — is a raid, not a narrative.
    # ICT bias requires the sequence sweep -> displacement -> FVG/OB, so a
    # valid bias must carry at least one displacement-class leg (BOS / FVG / OB)
    # or two distinct evidence kinds on the dominant side. A lone D1 sweep
    # downgrades to "unclear". Reversible:
    # ENGINE_B_HIERARCHY.REQUIRE_EVIDENCE_CORROBORATION: false.
    "REQUIRE_EVIDENCE_CORROBORATION": True,
    # Dominance ratio top/(bull+bear) required for a clean directional bias;
    # below it with both sides scoring, the narrative is "conflicting".
    "MIN_BIAS_STRENGTH": 0.6,
    # Displacement floor for a D1 FVG to count as bias narrative (body ATRs on
    # the gap's middle candle), matching ENGINE_B_IMBALANCE BAG thresholds.
    "FVG_MIN_DISPLACEMENT_BODY_ATR": 0.8,
    # ── Soft-hierarchical outcomes ────────────────────────────────────────────
    # Penalty/reward symmetry: the applied scale is ~5-6 points, so the old
    # pair (unclear x0.75 vs aligned+MTF +0.5 raw) made penalties ~3x the
    # maximum reward and turned hierarchical mode into a downgrade machine.
    # x0.90 (~-0.55 pts) vs +0.5 bonus is now roughly symmetric.
    "UNCLEAR_SCORE_MULT": 0.90,     # Daily narrative missing -> confidence cut
    "CONFLICT_SCORE_MULT": 0.50,    # both-sided Daily evidence (when not blocking)
    "CONFLICT_BLOCKS": True,
    "COUNTER_BIAS_MODE": "block",   # "block" | "penalty": candidate vs valid Daily bias
    "COUNTER_BIAS_SCORE_MULT": 0.40,
    "ALIGNED_NO_MTF_MULT": 1.0,     # HTF aligned; MTF reaction still pending
    "MTF_CONFIRM_BONUS": 0.5,       # score bonus when HTF + MTF both align
    # ── Swing mode: Weekly+Daily. Weekly candles are resampled from D1. ──────
    "SWING_WEEKLY_ENABLED": True,
    "SWING_WEEKLY_MIN_BARS": 8,     # min resampled W1 bars before weekly counts
    # The bucket containing the newest D1 candle is still forming; including it
    # let W1 BOS/sequence flip intra-week and flicker scan to scan.
    "SWING_WEEKLY_EXCLUDE_FORMING": True,
}


def normalize_bias_mode(value) -> str:
    """Map any configured value onto one of ``BIAS_MODES`` (default legacy)."""
    mode = str(value or "legacy").strip().lower()
    return mode if mode in BIAS_MODES else "legacy"


def merged_config(config: dict | None) -> dict:
    """Overlay a (possibly partial) config dict onto ``DEFAULT_CONFIG``."""
    cfg = dict(DEFAULT_CONFIG)
    if isinstance(config, dict):
        for key, value in config.items():
            if value is not None:
                cfg[key] = value
    return cfg


def _parse_candle_time(value):
    """Parse candle time (ISO string or epoch s/ms) to aware UTC datetime."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 1e12:  # epoch milliseconds
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def resample_d1_to_w1(d1_candles: list, *, exclude_forming: bool = False) -> list:
    """Aggregate D1 candle dicts into weekly (ISO week) candles.

    Used for the swing-style Weekly+Daily bias without changing any live fetch
    wiring. Candles without a parseable time are skipped; the result keeps
    chronological order. Returns [] when no usable timestamps exist (weekly
    evidence is then simply absent — the Daily still decides bias).

    ``exclude_forming`` drops the bucket containing the newest D1 candle when
    its ISO-week window extends beyond that candle's time: an in-progress week
    must not flip W1 BOS/sequence intra-week (and unsorted input is sorted by
    parsed time so bucket close/high/low cannot silently depend on input
    order). Default False preserves the legacy aggregate-everything behaviour;
    the hierarchy call site opts in via SWING_WEEKLY_EXCLUDE_FORMING.
    """
    buckets: dict = {}
    order: list = []
    parsed: list = []
    for candle in d1_candles or []:
        if not isinstance(candle, dict):
            continue
        dt = _parse_candle_time(candle.get("time") or candle.get("datetime"))
        if dt is None:
            continue
        try:
            o = float(candle["open"])
            h = float(candle["high"])
            lo = float(candle["low"])
            cl = float(candle["close"])
        except (KeyError, TypeError, ValueError):
            continue
        vol = float(candle.get("vol", 0) or 0)
        parsed.append((dt, o, h, lo, cl, vol))
    # Defensive sort: bucket OHLC aggregation must not depend on input order.
    parsed.sort(key=lambda row: row[0])
    newest_dt = parsed[-1][0] if parsed else None
    for dt, o, h, lo, cl, vol in parsed:
        iso = dt.isocalendar()
        key = (iso[0], iso[1])
        if key not in buckets:
            buckets[key] = {
                "time": dt.isoformat(),
                "open": o,
                "high": h,
                "low": lo,
                "close": cl,
                "vol": vol,
            }
            order.append(key)
        else:
            bucket = buckets[key]
            bucket["high"] = max(bucket["high"], h)
            bucket["low"] = min(bucket["low"], lo)
            bucket["close"] = cl
            bucket["vol"] = bucket.get("vol", 0.0) + vol

    def _bucket_week_end(key) -> datetime:
        # Monday 00:00 UTC of the ISO week + 7 days == next Monday 00:00.
        year, week = key
        monday = datetime.fromisocalendar(year, week, 1).replace(tzinfo=timezone.utc)
        return monday + timedelta(days=7)

    out = []
    for key in order:
        bucket = buckets[key]
        if (
            exclude_forming
            and key == order[-1]
            and newest_dt is not None
            and newest_dt < _bucket_week_end(key)
        ):
            continue  # still-forming current week — exclude from evidence
        out.append(bucket)
    return out


def evaluate_htf_bias(
    *,
    style: str = "intraday",
    d1_sweep: dict | None = None,
    d1_bos: dict | None = None,
    d1_fvgs: list | None = None,
    d1_order_blocks: list | None = None,
    d1_sequence: dict | None = None,
    w1_evidence: dict | None = None,
    current_price: float | None = None,
    d1_atr: float = 0.0,
    config: dict | None = None,
) -> dict:
    """Score the Daily (and, for swing, Weekly) narrative into a bias.

    Evidence per side (each counted once per side, best instance):
      1. D1 liquidity sweep          (``WEIGHT_SWEEP``)
      2. D1 displacement BOS         (``WEIGHT_DISPLACEMENT_BOS``)
      3. D1 displacement-backed FVG  (``WEIGHT_FVG_NARRATIVE``)
      4. D1 order block              (``WEIGHT_ORDER_BLOCK``)
      5. D1 swing sequence           (``WEIGHT_SEQUENCE``, supporting only)
      6. Weekly alignment            (``WEIGHT_WEEKLY``, swing style only)

    Returns a dict with ``direction`` (LONG/SHORT/None), ``state``
    (``valid``/``conflicting``/``unclear``), ``strength`` (dominance ratio),
    per-side point totals, the evidence list, and the aligned PD-array "draw"
    levels for the dominant side.
    """
    cfg = merged_config(config)
    bull = 0.0
    bear = 0.0
    evidence: list = []
    counted: set = set()  # (side, name) — one contribution per evidence kind

    def _add(side: str, name: str, weight: float, detail: str) -> None:
        nonlocal bull, bear
        if (side, name) in counted:
            return
        counted.add((side, name))
        if side == "LONG":
            bull += weight
        else:
            bear += weight
        evidence.append(
            {"name": name, "direction": side, "weight": float(weight), "detail": detail}
        )

    # 1) D1 liquidity sweep — the narrative anchor (stop raid -> reversal).
    sweep = d1_sweep if isinstance(d1_sweep, dict) else {}
    if sweep.get("bull_sweep"):
        _add("LONG", "d1_liquidity_sweep", cfg["WEIGHT_SWEEP"],
             f"sweep_low={sweep.get('sweep_low')}")
    if sweep.get("bear_sweep"):
        _add("SHORT", "d1_liquidity_sweep", cfg["WEIGHT_SWEEP"],
             f"sweep_high={sweep.get('sweep_high')}")

    # 2) D1 displacement through structure (close-based BOS).
    bos = d1_bos if isinstance(d1_bos, dict) else {}
    if bos.get("bos_bull"):
        _add("LONG", "d1_displacement_bos", cfg["WEIGHT_DISPLACEMENT_BOS"],
             f"broken_high={bos.get('last_broken_high')}")
    if bos.get("bos_bear"):
        _add("SHORT", "d1_displacement_bos", cfg["WEIGHT_DISPLACEMENT_BOS"],
             f"broken_low={bos.get('last_broken_low')}")

    # 3) D1 FVG narrative: unmitigated AND displacement-backed (the gap left
    #    behind by the displacement leg, not just any three-candle hole).
    min_disp = float(cfg["FVG_MIN_DISPLACEMENT_BODY_ATR"])
    for fvg in d1_fvgs or []:
        if not isinstance(fvg, dict) or fvg.get("mitigated"):
            continue
        side = "LONG" if fvg.get("type") == "bullish" else "SHORT"
        try:
            disp = float(fvg.get("displacement_body_atr") or 0.0)
        except (TypeError, ValueError):
            disp = 0.0
        if disp >= min_disp or fvg.get("structure_break"):
            _add(side, "d1_fvg_narrative", cfg["WEIGHT_FVG_NARRATIVE"],
                 f"mid={fvg.get('ce') or fvg.get('midpoint')}, disp_atr={disp:.2f}")

    # 4) D1 order block (unmitigated).
    for ob in d1_order_blocks or []:
        if not isinstance(ob, dict) or ob.get("mitigated"):
            continue
        side = "LONG" if ob.get("type") == "bullish" else "SHORT"
        _add(side, "d1_order_block", cfg["WEIGHT_ORDER_BLOCK"],
             f"strength={ob.get('strength')}, disp={ob.get('displacement')}")

    # 5) D1 swing sequence — supporting evidence only, never sufficient alone
    #    (mirrors ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED discipline).
    seq_state = str((d1_sequence or {}).get("state") or "").upper()
    if seq_state == "HH_HL":
        _add("LONG", "d1_sequence", cfg["WEIGHT_SEQUENCE"], "HH_HL")
    elif seq_state == "LH_LL":
        _add("SHORT", "d1_sequence", cfg["WEIGHT_SEQUENCE"], "LH_LL")

    # 6) Weekly alignment (swing style only): Weekly+Daily must agree; weekly
    #    evidence for the opposite side lands on that side's score instead.
    if (
        str(style or "").lower() == "swing"
        and cfg.get("SWING_WEEKLY_ENABLED")
        and isinstance(w1_evidence, dict)
    ):
        w1_bull = bool(w1_evidence.get("bos_bull")) or str(
            w1_evidence.get("sequence") or ""
        ).upper() == "HH_HL"
        w1_bear = bool(w1_evidence.get("bos_bear")) or str(
            w1_evidence.get("sequence") or ""
        ).upper() == "LH_LL"
        if w1_bull:
            _add("LONG", "w1_alignment", cfg["WEIGHT_WEEKLY"],
                 f"seq={w1_evidence.get('sequence')}, bos_bull={w1_evidence.get('bos_bull')}")
        if w1_bear:
            _add("SHORT", "w1_alignment", cfg["WEIGHT_WEEKLY"],
                 f"seq={w1_evidence.get('sequence')}, bos_bear={w1_evidence.get('bos_bear')}")

    # ── Classification ────────────────────────────────────────────────────────
    total = bull + bear
    min_score = float(cfg["MIN_BIAS_SCORE"])
    min_strength = float(cfg["MIN_BIAS_STRENGTH"])
    if total <= 0:
        state, direction, strength = "unclear", None, 0.0
    else:
        top = max(bull, bear)
        strength = top / total
        if top < min_score:
            state, direction = "unclear", None
        elif strength >= min_strength:
            state = "valid"
            direction = "LONG" if bull > bear else "SHORT"
        else:
            state, direction = "conflicting", None

    # Corroboration: a lone weighted evidence kind is a raid, not a narrative.
    # WEIGHT_SWEEP == MIN_BIAS_SCORE let a single D1 stop-raid wick print a
    # valid bias with no displacement and no PD array — contradicting the
    # sweep -> displacement -> FVG/OB sequence this module documents. A valid
    # bias now needs a displacement-class leg (BOS / FVG / OB) or two distinct
    # evidence kinds on the dominant side.
    corroboration = {
        "required": bool(cfg.get("REQUIRE_EVIDENCE_CORROBORATION", True)),
        "satisfied": True,
        "dominant_kinds": [],
    }
    if state == "valid" and corroboration["required"]:
        _strong_kinds = {"d1_displacement_bos", "d1_fvg_narrative", "d1_order_block"}
        _kinds = {e["name"] for e in evidence if e.get("direction") == direction}
        corroboration["dominant_kinds"] = sorted(_kinds)
        _satisfied = bool(_kinds & _strong_kinds) or len(_kinds) >= 2
        corroboration["satisfied"] = _satisfied
        if not _satisfied:
            state, direction = "unclear", None
            evidence.append(
                {
                    "name": "corroboration_denied",
                    "direction": None,
                    "weight": 0.0,
                    "detail": "single_kind_no_displacement_leg",
                }
            )

    # Aligned PD-array "draw" levels for the dominant side (narrative context:
    # where price is drawn from/towards), nearest first, capped at 3.
    draws: list = []
    if direction and current_price and d1_atr and d1_atr > 0:
        want = "bullish" if direction == "LONG" else "bearish"
        for fvg in d1_fvgs or []:
            if not isinstance(fvg, dict) or fvg.get("mitigated") or fvg.get("type") != want:
                continue
            mid = fvg.get("ce") or fvg.get("midpoint")
            if mid is None and fvg.get("top") is not None and fvg.get("bottom") is not None:
                mid = (float(fvg["top"]) + float(fvg["bottom"])) / 2.0
            if mid is not None:
                draws.append({"type": "fvg", "mid": float(mid),
                              "distance_atr": abs(float(current_price) - float(mid)) / d1_atr})
        for ob in d1_order_blocks or []:
            if not isinstance(ob, dict) or ob.get("mitigated") or ob.get("type") != want:
                continue
            if ob.get("top") is not None and ob.get("bottom") is not None:
                mid = (float(ob["top"]) + float(ob["bottom"])) / 2.0
                draws.append({"type": "order_block", "mid": float(mid),
                              "distance_atr": abs(float(current_price) - mid) / d1_atr})
        draws.sort(key=lambda d: d["distance_atr"])
        draws = draws[:3]

    return {
        "version": HIERARCHY_VERSION,
        "style": str(style or "intraday"),
        "state": state,
        "direction": direction,
        "strength": round(float(strength), 4),
        "bull_score": round(bull, 3),
        "bear_score": round(bear, 3),
        "min_bias_score": min_score,
        "min_bias_strength": min_strength,
        "corroboration": corroboration,
        "evidence": evidence,
        "draw_on_liquidity": draws,
        "weekly_used": any(e["name"] == "w1_alignment" for e in evidence),
    }


def evaluate_directional_hierarchy(
    *,
    mode: str,
    htf_bias: dict | None,
    direction: str,
    mtf_evidence: dict | None = None,
    ltf_evidence: dict | None = None,
    config: dict | None = None,
) -> dict:
    """Evaluate one candidate direction against the HTF->MTF->LTF hierarchy.

    ``mtf_evidence``/``ltf_evidence`` are direction-resolved boolean dicts
    supplied by the caller (``calculate_confidence``), e.g.::

        mtf_evidence = {"bos": True, "choch": False, "sweep": True, "sequence": False}
        ltf_evidence = {"trigger": True, "fvg_reaction": False, "sweep_at_zone": False}

    Returns the stage breakdown, the model ``decision``
    (``confirmed``/``accepted``/``reduced``/``blocked``), and the *applied*
    ``score_multiplier``/``score_bonus``/``blocked`` triple. In ``legacy`` mode
    the decision is computed for diagnostics but never applied.
    """
    cfg = merged_config(config)
    mode = normalize_bias_mode(mode)
    direction = str(direction or "").upper()
    htf = htf_bias if isinstance(htf_bias, dict) else {}
    bias_state = str(htf.get("state") or "unclear").lower()
    if bias_state not in ("valid", "conflicting", "unclear"):
        bias_state = "unclear"
    bias_dir = str(htf.get("direction") or "").upper() or None

    mtf = mtf_evidence if isinstance(mtf_evidence, dict) else {}
    mtf_hits = [name for name in ("bos", "choch", "sweep") if mtf.get(name)]
    mtf_confirmed = bool(mtf_hits)
    mtf_supporting = bool(mtf.get("sequence"))  # weak, reported but insufficient

    ltf = ltf_evidence if isinstance(ltf_evidence, dict) else {}
    ltf_hits = [name for name in ("trigger", "fvg_reaction", "sweep_at_zone") if ltf.get(name)]
    ltf_ok = bool(ltf_hits)

    counter_bias = bool(bias_state == "valid" and bias_dir and direction and direction != bias_dir)
    aligned = bool(bias_state == "valid" and bias_dir and direction == bias_dir)

    stages = {
        "htf_bias": {
            "state": bias_state,
            "direction": bias_dir,
            "strength": htf.get("strength"),
            "passed": aligned,
        },
        "mtf_confirmation": {
            "passed": mtf_confirmed,
            "evidence": mtf_hits,
            "supporting_sequence": mtf_supporting,
        },
        "ltf_entry": {
            "passed": ltf_ok,
            "evidence": ltf_hits,
        },
    }

    decision = "accepted"
    blocked = False
    block_reasons: list = []
    score_multiplier = 1.0
    score_bonus = 0.0
    reasons: list = []

    if counter_bias:
        # Candidate runs against a valid Daily narrative — the core violation
        # of top-down ICT discipline.
        reasons.append(f"candidate_{direction}_vs_htf_{bias_dir}")
        if str(cfg.get("COUNTER_BIAS_MODE", "block")).lower() == "block" or mode == "strict":
            decision = "blocked"
            blocked = True
            block_reasons.append(REASON_COUNTER_HTF_BIAS)
        else:
            decision = "reduced"
            score_multiplier = float(cfg["COUNTER_BIAS_SCORE_MULT"])
    elif bias_state == "conflicting":
        reasons.append("htf_evidence_on_both_sides")
        if cfg.get("CONFLICT_BLOCKS", True) or mode == "strict":
            decision = "blocked"
            blocked = True
            block_reasons.append(REASON_HTF_BIAS_CONFLICTING)
        else:
            decision = "reduced"
            score_multiplier = float(cfg["CONFLICT_SCORE_MULT"])
    elif not aligned:
        # No valid Daily narrative (or none established): soft mode cuts
        # confidence; strict mode fails State 1 outright.
        reasons.append("htf_bias_not_established")
        if mode == "strict":
            decision = "blocked"
            blocked = True
            block_reasons.append(REASON_HTF_BIAS_UNCLEAR)
        else:
            decision = "reduced"
            score_multiplier = float(cfg["UNCLEAR_SCORE_MULT"])
    else:
        # HTF bias valid and aligned.
        reasons.append(f"htf_bias_{bias_dir}_aligned(str={htf.get('strength')})")
        if mode == "strict":
            # State machine: evaluate State 2 only because State 1 passed.
            if not mtf_confirmed:
                decision = "blocked"
                blocked = True
                block_reasons.append(REASON_MTF_CONFIRMATION_MISSING)
                reasons.append("mtf_confirmation_required")
            elif not ltf_ok:
                # State 3 only because State 2 passed.
                decision = "blocked"
                blocked = True
                block_reasons.append(REASON_LTF_ENTRY_MISSING)
                reasons.append("ltf_entry_required")
            else:
                decision = "confirmed"
                score_bonus = float(cfg["MTF_CONFIRM_BONUS"])
        elif mtf_confirmed:
            decision = "confirmed"
            score_bonus = float(cfg["MTF_CONFIRM_BONUS"])
            reasons.append(f"mtf_confirmed({'+'.join(mtf_hits)})")
        else:
            decision = "accepted"
            score_multiplier = float(cfg["ALIGNED_NO_MTF_MULT"])
            reasons.append("mtf_confirmation_pending")

    applied = mode != "legacy"
    if not applied:
        # Legacy: full diagnostics, zero scoring/gate effect.
        score_multiplier = 1.0
        score_bonus = 0.0
        blocked = False

    return {
        "version": HIERARCHY_VERSION,
        "mode": mode,
        "applied": applied,
        "candidate_direction": direction,
        "stages": stages,
        "decision": decision,
        "blocked": bool(blocked),
        "block_reasons": block_reasons,
        "score_multiplier": round(float(score_multiplier), 4),
        "score_bonus": round(float(score_bonus), 4),
        "reasons": reasons,
    }
