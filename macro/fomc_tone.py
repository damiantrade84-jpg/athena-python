"""FOMC hawkish/dovish tone scoring — deterministic, explainable macro CONTEXT only.

V2 builds on the V1 macro feed (``macro/store.py`` events, ``macro/fomc_rss.py`` releases,
``macro/fomc_fred.py`` FRED confirmation). It NEVER produces a trade signal, never changes
Engine A/B/D score math / thresholds / SL-TP / RR / sizing, and never grants execution
permission. ``executionUse`` is always ``CONTEXT_ONLY``; macro lockout still owns blocking.

Scoring is a transparent rule engine over the official statement text:

* When the previous statement is available we score the *change* in language (added /
  removed / unchanged / intensified) — wording changes weigh more than repeated wording.
* When no previous statement exists we fall back to absolute keyword scoring at lower
  confidence.
* ``target_range_change_bps`` reuses the V1 FRED-confirmed target range (historical fact,
  NOT a market surprise). ``market_expectation_surprise_bps`` stays null — the repo has no
  forecast/consensus/fed-funds-futures expectation feed.

CLI:  python -m macro.fomc_tone --event <macro_event_id>
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import re
from typing import Any

log = logging.getLogger("macro.fomc_tone")

# ── Tunables (isolated so they are easy to audit / adjust) ───────────────────────
_MIN_TEXT_LEN = 40
CONF_UNKNOWN = 0.40  # below this → tone_label = UNKNOWN

# Differential weighting: a wording CHANGE matters more than a repeated phrase.
ADDED_FACTOR = 1.0
UNCHANGED_FACTOR = 0.3
REMOVED_FACTOR = 0.7
ABSOLUTE_FACTOR = 0.6  # no-previous fallback

# Component weights (spec V2). Applied as RELATIVE weights over the components that
# actually carry signal, so a single decisive category is not diluted to neutral by
# silent categories. rate_decision is included only when target-range data exists.
COMPONENT_WEIGHTS = {
    "statement_language": 0.30,
    "inflation_language": 0.20,
    "forward_guidance": 0.20,
    "labor_market": 0.10,
    "growth": 0.10,
    "balance_sheet": 0.10,
}
RATE_DECISION_WEIGHT = 0.25

COMPONENT_KEYS = tuple(COMPONENT_WEIGHTS.keys())

# Category (in phrase dict) → component score key.
_CATEGORY_COMPONENT = {
    "statement": "statement_language",
    "inflation": "inflation_language",
    "forward_guidance": "forward_guidance",
    "labor": "labor_market",
    "growth": "growth",
    "balance_sheet": "balance_sheet",
}

# Tone-label thresholds on the −100..+100 score.
_LABELS = (
    (-60, "STRONGLY_DOVISH"),   # score <= -60
    (-35, "DOVISH"),            # -60 < score <= -35
    (-10, "MILDLY_DOVISH"),     # -35 < score < -10  (handled below as < -10)
    (10, "NEUTRAL"),            # -10 <= score <= +10
    (35, "MILDLY_HAWKISH"),     # +10 < score < +35
    (60, "HAWKISH"),            # +35 <= score < +60
)


def _label_for_score(score: int) -> str:
    if score <= -60:
        return "STRONGLY_DOVISH"
    if score <= -35:
        return "DOVISH"
    if score < -10:
        return "MILDLY_DOVISH"
    if score <= 10:
        return "NEUTRAL"
    if score < 35:
        return "MILDLY_HAWKISH"
    if score < 60:
        return "HAWKISH"
    return "STRONGLY_HAWKISH"


# ── Phrase dictionaries (one place; positive=hawkish, negative=dovish direction) ──
# (phrase [lowercase], category, direction, base impact points)
_PHRASES: tuple[tuple[str, str, str, int], ...] = (
    # ── Inflation ──
    ("inflation remains elevated", "inflation", "hawkish", 45),
    ("inflation is still elevated", "inflation", "hawkish", 45),
    ("inflation remains somewhat elevated", "inflation", "hawkish", 25),
    ("inflation has risen", "inflation", "hawkish", 35),
    ("risks to inflation remain", "inflation", "hawkish", 25),
    ("inflation has eased", "inflation", "dovish", 40),
    ("inflation has made further progress", "inflation", "dovish", 38),
    ("inflation has declined", "inflation", "dovish", 35),
    # ── Labor market ──
    ("labor market remains tight", "labor", "hawkish", 35),
    ("job gains remain strong", "labor", "hawkish", 30),
    ("job gains have remained strong", "labor", "hawkish", 30),
    ("job gains have moderated", "labor", "dovish", 30),
    ("job gains have slowed", "labor", "dovish", 35),
    ("unemployment rate has moved up", "labor", "dovish", 30),
    ("unemployment rate has risen", "labor", "dovish", 30),
    ("risks to employment have increased", "labor", "dovish", 40),
    # ── Growth / economic activity ──
    ("economic activity has been expanding at a strong pace", "growth", "hawkish", 40),
    ("economic activity has continued to expand", "growth", "hawkish", 25),
    ("economic activity has slowed", "growth", "dovish", 35),
    ("economic activity has moderated", "growth", "dovish", 28),
    ("growth has moderated", "growth", "dovish", 28),
    # ── Forward guidance ──
    ("does not expect it will be appropriate to reduce the target range", "forward_guidance", "hawkish", 45),
    ("restrictive stance of monetary policy", "forward_guidance", "hawkish", 25),
    ("restrictive monetary policy", "forward_guidance", "hawkish", 22),
    ("prepared to adjust the stance of monetary policy", "forward_guidance", "hawkish", 8),
    ("lowering the target range", "forward_guidance", "dovish", 45),
    ("lower the target range", "forward_guidance", "dovish", 45),
    ("will act as appropriate to support the economy", "forward_guidance", "dovish", 30),
    ("risks to achieving employment and inflation goals are moving into better balance",
     "forward_guidance", "dovish", 28),
    # NOTE: Phase-5 governs over Phase-4 here — "greater confidence ... toward 2 percent"
    # is treated as DOVISH (it is the stated precondition for rate cuts).
    ("greater confidence that inflation is moving sustainably toward 2 percent",
     "forward_guidance", "dovish", 30),
    # ── Overall statement stance ──
    ("remains highly attentive to inflation risks", "statement", "hawkish", 25),
    ("highly attentive to inflation risks", "statement", "hawkish", 22),
    ("attentive to the risks to both sides of its dual mandate", "statement", "dovish", 12),
    # ── Balance sheet ──
    ("reduce its holdings of treasury securities and agency debt", "balance_sheet", "hawkish", 15),
    ("continue to reduce its holdings", "balance_sheet", "hawkish", 12),
    ("reduce the pace of balance sheet runoff", "balance_sheet", "dovish", 15),
    ("slow the pace of decline in securities holdings", "balance_sheet", "dovish", 15),
)

# Intensified / softened language transitions (previous → current).
# (from_phrase, to_phrase, category, direction, impact)
_TRANSITIONS: tuple[tuple[str, str, str, str, int], ...] = (
    ("somewhat elevated", "elevated", "inflation", "hawkish", 12),
    ("elevated", "somewhat elevated", "inflation", "dovish", 10),
    ("moderated", "slowed", "growth", "dovish", 12),
    ("solid", "strong", "growth", "hawkish", 10),
    ("strong", "solid", "growth", "dovish", 10),
)

_BOILERPLATE_MARKERS = (
    "voting for the monetary policy action",
    "voting against",
    "implementation note",
    "for media inquiries",
    "last update",
)


def _reverse(direction: str) -> str:
    return "dovish" if direction == "hawkish" else "hawkish"


# ── Text handling ────────────────────────────────────────────────────────────────
def clean_statement_text(raw: str | None) -> str:
    """Strip HTML/entities and trailing boilerplate (voting list / footer)."""
    if not raw:
        return ""
    txt = re.sub(r"<[^>]+>", " ", str(raw))
    txt = html.unescape(txt)
    low = txt.lower()
    cut = len(txt)
    for marker in _BOILERPLATE_MARKERS:
        idx = low.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    txt = txt[:cut]
    return re.sub(r"\s+", " ", txt).strip()


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _snippet(text: str, phrase: str, width: int = 55) -> str:
    low = text.lower()
    i = low.find(phrase)
    if i == -1:
        return phrase
    start = max(0, i - width)
    end = min(len(text), i + len(phrase) + width)
    seg = text[start:end].strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{seg}{suffix}"


def _matches(text_low: str) -> set[str]:
    return {p[0] for p in _PHRASES if p[0] in text_low}


# ── Scoring engine ───────────────────────────────────────────────────────────────
def _unknown_result(*, reason: str, target_range_change_bps: float | None, source: str,
                    cleaned: str, warnings: list[str]) -> dict[str, Any]:
    warnings = list(warnings)
    warnings.append("Statement text missing or insufficient; tone unavailable.")
    return {
        "source": source,
        "tone_label": "UNKNOWN",
        "tone_score": 0,
        "tone_direction": "neutral",
        "confidence": 0.0,
        "rate_decision_score": None,
        "statement_language_score": None,
        "inflation_language_score": None,
        "labor_market_score": None,
        "growth_score": None,
        "balance_sheet_score": None,
        "forward_guidance_score": None,
        "dot_plot_score": None,
        "press_conference_score": None,
        "market_expectation_surprise_bps": None,
        "target_range_change_bps": target_range_change_bps,
        "evidence": [],
        "warnings": warnings,
        "raw_text_hash": _hash(cleaned),
        "previous_text_hash": None,
        "execution_use": "CONTEXT_ONLY",
        "reason": reason,
    }


def score_statement(
    current_text: str | None,
    previous_text: str | None = None,
    *,
    target_range_change_bps: float | None = None,
    source: str = "FED_RSS_STATEMENT",
) -> dict[str, Any]:
    """Deterministically score one FOMC statement. Pure — no I/O, fully testable."""
    warnings: list[str] = []
    # Market expectation feed does not exist in this repo → never compute a surprise.
    warnings.append("No market expectation feed available; surprise score not calculated.")

    cleaned = clean_statement_text(current_text)
    if len(cleaned) < _MIN_TEXT_LEN:
        return _unknown_result(
            reason="missing_or_insufficient_statement_text",
            target_range_change_bps=target_range_change_bps,
            source=source, cleaned=cleaned, warnings=warnings,
        )

    cur_low = cleaned.lower()
    prev_cleaned = clean_statement_text(previous_text)
    has_prev = len(prev_cleaned) >= _MIN_TEXT_LEN
    prev_low = prev_cleaned.lower()
    if not has_prev:
        warnings.append(
            "No previous FOMC statement available; absolute keyword scoring only "
            "(lower confidence)."
        )

    cur_match = _matches(cur_low)
    prev_match = _matches(prev_low) if has_prev else set()

    component_raw: dict[str, float] = {k: 0.0 for k in COMPONENT_KEYS}
    evidence: list[dict[str, Any]] = []
    distinct = 0

    for phrase, category, direction, base in _PHRASES:
        in_cur = phrase in cur_match
        in_prev = phrase in prev_match
        if not in_cur and not in_prev:
            continue
        comp = _CATEGORY_COMPONENT[category]
        if has_prev:
            if in_cur and not in_prev:
                factor, eff_dir, change = ADDED_FACTOR, direction, "added"
            elif in_cur and in_prev:
                factor, eff_dir, change = UNCHANGED_FACTOR, direction, "unchanged"
            else:  # removed → reverse pressure
                factor, eff_dir, change = REMOVED_FACTOR, _reverse(direction), "removed"
        else:
            if not in_cur:
                continue
            factor, eff_dir, change = ABSOLUTE_FACTOR, direction, "present"
        sign = 1.0 if eff_dir == "hawkish" else -1.0
        impact = round(base * factor * sign, 1)
        component_raw[comp] += impact
        distinct += 1
        snip_src = cleaned if in_cur else prev_cleaned
        evidence.append({
            "category": category,
            "direction": eff_dir,
            "phrase": phrase,
            "impact": impact,
            "change": change,
            "snippet": _snippet(snip_src, phrase),
        })

    if has_prev:
        for frm, to, category, direction, impact in _TRANSITIONS:
            if frm in prev_low and to in cur_low and frm not in cur_low and to not in prev_low:
                comp = _CATEGORY_COMPONENT[category]
                sign = 1.0 if direction == "hawkish" else -1.0
                pts = round(impact * sign, 1)
                component_raw[comp] += pts
                distinct += 1
                evidence.append({
                    "category": category,
                    "direction": direction,
                    "phrase": f"{frm} → {to}",
                    "impact": pts,
                    "change": "intensified" if abs(impact) >= 12 else "softened",
                    "snippet": _snippet(cleaned, to),
                })

    components = {k: int(max(-100, min(100, round(v)))) for k, v in component_raw.items()}

    rate_decision_score: int | None = None
    if target_range_change_bps is not None:
        rate_decision_score = int(max(-100, min(100, round(target_range_change_bps * 2.4))))
    else:
        warnings.append(
            "FRED target-range change unavailable; rate-decision component excluded."
        )

    # Weighted blend over components that actually carry signal (+ rate decision).
    num = den = 0.0
    for key, score in components.items():
        if score != 0:
            w = COMPONENT_WEIGHTS[key]
            num += w * score
            den += w
    if rate_decision_score is not None:
        num += RATE_DECISION_WEIGHT * rate_decision_score
        den += RATE_DECISION_WEIGHT
    tone_score = int(max(-100, min(100, round(num / den)))) if den > 0 else 0

    # ── Confidence ──
    conf = 0.5
    if has_prev:
        conf += 0.2
        if distinct > 0:
            conf += 0.1
    else:
        conf -= 0.1
    if distinct >= 3:
        conf += 0.1
    if distinct <= 1:
        conf -= 0.2
    active = [s for s in components.values() if s != 0]
    pos = any(s >= 30 for s in components.values())
    neg = any(s <= -30 for s in components.values())
    conflicting = pos and neg
    if len(active) >= 3 and not conflicting:
        conf += 0.1
    if conflicting:
        conf -= 0.15
    if rate_decision_score is not None:
        conf += 0.1
    confidence = round(max(0.0, min(1.0, conf)), 2)

    label = "UNKNOWN" if confidence < CONF_UNKNOWN else _label_for_score(tone_score)
    direction = "hawkish" if tone_score > 10 else "dovish" if tone_score < -10 else "neutral"

    return {
        "source": source,
        "tone_label": label,
        "tone_score": tone_score,
        "tone_direction": direction,
        "confidence": confidence,
        "rate_decision_score": rate_decision_score,
        "statement_language_score": components["statement_language"],
        "inflation_language_score": components["inflation_language"],
        "labor_market_score": components["labor_market"],
        "growth_score": components["growth"],
        "balance_sheet_score": components["balance_sheet"],
        "forward_guidance_score": components["forward_guidance"],
        "dot_plot_score": None,            # no SEP/dot-plot data source
        "press_conference_score": None,    # no transcript data source
        "market_expectation_surprise_bps": None,  # no expectation feed
        "target_range_change_bps": target_range_change_bps,
        "evidence": evidence,
        "warnings": warnings,
        "raw_text_hash": _hash(cleaned),
        "previous_text_hash": _hash(prev_cleaned) if has_prev else None,
        "execution_use": "CONTEXT_ONLY",
        "reason": "differential_scoring" if has_prev else "absolute_keyword_scoring",
    }


# ── Symbol interpretation (CONTEXT only — never a trade signal) ───────────────────
def expected_pressure(symbol: str | None, tone_score: int, asset_type: str | None = None) -> str:
    """Plain-language macro CONTEXT for a USD-sensitive instrument. Not a signal."""
    if tone_score is None or -10 <= tone_score <= 10:
        return "Neutral FOMC tone; no clear macro pressure. Use chart structure."
    hawk = tone_score > 10
    sym = str(symbol or "").strip().upper()
    a = str(asset_type or "").strip().lower()
    usd_strength = "USD-supportive" if hawk else "USD-weakening"
    if sym in ("XAU/USD", "XAG/USD"):
        lean = "bearish pressure on metals" if hawk else "supportive for metals"
        return f"{usd_strength}; {lean} if chart structure confirms."
    if sym == "USD/JPY" or (sym.startswith("USD") and "/" in sym):
        lean = "leans bullish pair" if hawk else "leans bearish pair"
        return f"{usd_strength}; {lean} if chart structure confirms."
    legs = sym.split("/")
    if len(legs) == 2 and legs[1] == "USD":  # e.g. EUR/USD, GBP/USD
        lean = "leans bearish pair" if hawk else "leans bullish pair"
        return f"{usd_strength}; {lean} if chart structure confirms."
    if a == "crypto" or sym.endswith("USDT"):
        lean = "can pressure risk assets" if hawk else "can support risk appetite/liquidity"
        return f"{usd_strength}; {lean} if chart structure confirms."
    return f"{usd_strength} macro context; confirm with chart structure before acting."


# ── Store integration ────────────────────────────────────────────────────────────
def _target_range_change_bps(event: dict[str, Any]) -> float | None:
    """Reuse V1 FRED-confirmed target range (historical fact, NOT a surprise)."""
    pl, pu = event.get("previous_lower"), event.get("previous_upper")
    al, au = event.get("actual_lower"), event.get("actual_upper")
    if None in (pl, pu, al, au):
        return None
    try:
        prev_mid = (float(pl) + float(pu)) / 2.0
        actual_mid = (float(al) + float(au)) / 2.0
    except (TypeError, ValueError):
        return None
    return round((actual_mid - prev_mid) * 100.0, 1)


def _event_text(event: dict[str, Any] | None, *, fetch: bool) -> str | None:
    if not event:
        return None
    text = event.get("raw_text")
    if text and len(str(text)) >= _MIN_TEXT_LEN:
        return str(text)
    if fetch and event.get("raw_url"):
        try:  # production-only; tests pass text directly and never reach here
            from feed_http import feed_get

            resp = feed_get(str(event["raw_url"]))
            resp.raise_for_status()
            return clean_statement_text(resp.text)
        except Exception as exc:
            log.warning("[FOMC-TONE] statement fetch failed (degraded): %s", exc)
    return str(text) if text else None


def _find_previous_statement(store, event: dict[str, Any]) -> dict[str, Any] | None:
    from macro.macro_guard import LOCKOUT_EVENT_TYPES

    cur_id = event.get("id")
    cur_sched = str(event.get("scheduled_time_utc") or "")
    if not cur_sched:
        return None
    prior = [
        e for e in store.list_events(event_types=LOCKOUT_EVENT_TYPES, end_iso=cur_sched, limit=50)
        if e.get("id") != cur_id and str(e.get("scheduled_time_utc") or "") < cur_sched
    ]
    return prior[-1] if prior else None


def process_event(
    macro_event_id: str,
    *,
    store=None,
    tone_store=None,
    now=None,
    current_text: str | None = None,
    previous_text: str | None = None,
    fetch_text: bool = False,
) -> dict[str, Any] | None:
    """Score one stored macro event and idempotently persist the tone record."""
    from macro.store import default_store
    from macro.tone_store import default_tone_store

    store = store or default_store()
    tstore = tone_store or default_tone_store()
    event = store.get_event(macro_event_id)
    if event is None:
        log.debug("[FOMC-TONE] event %s not found", macro_event_id)
        return None

    text = current_text if current_text is not None else _event_text(event, fetch=fetch_text)
    prev_event = _find_previous_statement(store, event)
    if previous_text is not None:
        prev_text = previous_text
    else:
        prev_text = _event_text(prev_event, fetch=fetch_text) if prev_event else None

    result = score_statement(
        text or "",
        prev_text,
        target_range_change_bps=_target_range_change_bps(event),
        source=str(event.get("source") or "FED_RSS_STATEMENT"),
    )

    record = {
        "id": f"tone:{macro_event_id}",
        "macro_event_id": macro_event_id,
        "source": result["source"],
        "event_type": event.get("event_type"),
        "country": event.get("country") or "US",
        "currency": event.get("currency") or "USD",
        "title": event.get("title"),
        "statement_url": event.get("raw_url"),
        "statement_published_utc": event.get("scheduled_time_utc"),
        "previous_statement_event_id": prev_event.get("id") if prev_event else None,
        "previous_statement_url": prev_event.get("raw_url") if prev_event else None,
        "tone_label": result["tone_label"],
        "tone_score": result["tone_score"],
        "confidence": result["confidence"],
        "rate_decision_score": result["rate_decision_score"],
        "statement_language_score": result["statement_language_score"],
        "inflation_language_score": result["inflation_language_score"],
        "labor_market_score": result["labor_market_score"],
        "growth_score": result["growth_score"],
        "balance_sheet_score": result["balance_sheet_score"],
        "forward_guidance_score": result["forward_guidance_score"],
        "dot_plot_score": result["dot_plot_score"],
        "press_conference_score": result["press_conference_score"],
        "market_expectation_surprise_bps": result["market_expectation_surprise_bps"],
        "target_range_change_bps": result["target_range_change_bps"],
        "evidence_json": json.dumps(result["evidence"], default=str),
        "warnings_json": json.dumps(result["warnings"], default=str),
        "raw_text_hash": result["raw_text_hash"],
        "previous_text_hash": result["previous_text_hash"],
    }
    tstore.upsert_tone(record)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FOMC tone scoring")
    parser.add_argument("--event", help="score a stored macro_event_id")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.event:
        parser.print_help()
        return 0
    rec = process_event(args.event, fetch_text=True)
    print(json.dumps(rec, indent=2, default=str) if rec else "no tone produced")
    return 0 if rec else 1


if __name__ == "__main__":
    raise SystemExit(main())
