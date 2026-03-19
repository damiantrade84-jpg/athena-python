"""ai_learning.py — AI self-learning and outcome tracking for Sentinel Pro.

After each trade closes, extract what the AI predicted vs what actually happened.
Build a feedback context injected into future AI prompts so Marcus Reid learns
from real live (and demo) outcomes over time.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta

log = logging.getLogger("sentinel.learning")

# ── DB schema ────────────────────────────────────────────────────────────────

_CREATE_LEARNING_LOG = """
CREATE TABLE IF NOT EXISTS learning_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    trade_ts         TEXT,
    ticket           TEXT,
    pair             TEXT NOT NULL,
    asset_type       TEXT NOT NULL,
    direction        TEXT,
    ai_grade         TEXT,
    edge_prob        REAL,
    confluence_score REAL,
    max_score        REAL,
    score_pct        REAL,
    votes_json       TEXT,
    regime           TEXT,
    pnl              REAL,
    r_multiple       REAL,
    win              INTEGER,
    exit_reason      TEXT,
    holding_hours    REAL,
    is_demo          INTEGER DEFAULT 0,
    factors_json     TEXT
);
"""

_CREATE_META_LOG = """
CREATE TABLE IF NOT EXISTS meta_analysis_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    days      INTEGER,
    summary   TEXT,
    insights  TEXT,
    raw_response TEXT
);
"""

_LEARNING_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_ll_pair  ON learning_log(pair);",
    "CREATE INDEX IF NOT EXISTS idx_ll_asset ON learning_log(asset_type);",
    "CREATE INDEX IF NOT EXISTS idx_ll_ts    ON learning_log(ts);",
    "CREATE INDEX IF NOT EXISTS idx_ll_win   ON learning_log(win);",
]


def init_learning_db(db_path: str) -> None:
    """Create learning tables and indexes if they don't exist."""
    try:
        with sqlite3.connect(db_path, timeout=15.0) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(_CREATE_LEARNING_LOG)
            con.execute(_CREATE_META_LOG)
            for idx in _LEARNING_IDX:
                con.execute(idx)
            # Migrate: add factors_json if missing
            existing = {
                r[1] for r in con.execute("PRAGMA table_info(learning_log)").fetchall()
            }
            if "factors_json" not in existing:
                con.execute("ALTER TABLE learning_log ADD COLUMN factors_json TEXT")
            con.commit()
        log.info("[LEARN] Learning DB ready")
    except Exception as e:
        log.warning(f"[LEARN] DB init failed: {e}")


# ── Learning extraction ───────────────────────────────────────────────────────


def extract_learning_from_trade(
    db_path: str, ticket: str, is_demo: bool = False
) -> None:
    """Pull the closed trade from audit_log and write a learning_log row.

    Called by _update_trade_outcome() after the outcome UPDATE is committed.
    In demo mode all closed trades are logged; live mode requires r_multiple != NULL.
    """
    try:
        with sqlite3.connect(db_path, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT * FROM audit_log WHERE ticket=? ORDER BY id DESC LIMIT 1",
                (ticket,),
            ).fetchone()
        if row is None:
            return
        # sqlite3.Row doesn't support .get() or set operations — convert so we can
        # safely use dict access patterns (e.g. "max_score" in row.keys()) below.
        row = dict(row)
        if not is_demo and row["r_multiple"] is None:
            return  # live only learns from real outcomes

        pair = row["pair"] or ""
        asset_type = row["asset_class"] or _infer_asset_type(pair)
        pnl = row["pnl"]
        r_mult = row["r_multiple"]
        win = 1 if (r_mult is not None and r_mult > 0) else 0

        # Parse votes if stored
        votes_raw = None
        try:
            votes_raw = row["votes_json"] if "votes_json" in row.keys() else None
        except Exception:
            pass

        # Copy factor scores if available
        factors_raw = row.get("factors_json")

        with sqlite3.connect(db_path, timeout=15.0) as con:
            con.execute(
                """INSERT INTO learning_log
                   (ts, trade_ts, ticket, pair, asset_type, direction,
                    ai_grade, edge_prob, confluence_score, max_score, score_pct,
                    votes_json, regime, pnl, r_multiple, win, exit_reason,
                    holding_hours, is_demo, factors_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    row["ts"],
                    ticket,
                    pair,
                    asset_type,
                    row["direction"],
                    row["grade"] if "grade" in row.keys() else None,
                    None,  # edge_prob — not stored in audit_log currently
                    row["score"],
                    row["max_score"] if "max_score" in row.keys() else None,
                    round(row["score"] / row["max_score"], 3)
                    if row["score"] and "max_score" in row.keys() and row["max_score"]
                    else None,
                    votes_raw,
                    row["regime"],
                    pnl,
                    r_mult,
                    win,
                    row["exit_reason"],
                    row["holding_period_hours"],
                    1 if is_demo else 0,
                    factors_raw,
                ),
            )
            con.commit()
        log.debug(f"[LEARN] Logged outcome for {pair} win={win} R={r_mult}")
    except Exception as e:
        log.debug(f"[LEARN] extract_learning_from_trade failed for {ticket}: {e}")


def _infer_asset_type(pair: str) -> str:
    p = pair.upper()
    if "USDT" in p:
        return "crypto"
    if any(c in p for c in ["XAU", "XAG", "OIL"]):
        return "commodity"
    if any(c in p for c in ["EUR", "GBP", "USD", "JPY", "AUD", "NZD", "CHF", "CAD"]):
        return "forex"
    return "stock"


# ── Learning context query ────────────────────────────────────────────────────


def get_ai_learning_context(
    pair: str, asset_type: str, db_path: str, lookback_days: int = 90
) -> dict:
    """Query learning_log and build a context dict for AI prompt injection.

    Returns empty dict if insufficient data.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    try:
        with sqlite3.connect(db_path, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM learning_log WHERE ts > ? ORDER BY ts DESC", (cutoff,)
            ).fetchall()
    except Exception as e:
        log.debug(f"[LEARN] context query failed: {e}")
        return {}

    if len(rows) < 5:
        return {"sample_size": len(rows)}

    rows = [dict(r) for r in rows]

    # ── Grade accuracy ───────────────────────────────────────────────────────
    grade_acc: dict = {}
    for r in rows:
        g = (r.get("ai_grade") or "?").strip()
        if g not in grade_acc:
            grade_acc[g] = {"trades": 0, "wins": 0, "r_sum": 0.0}
        grade_acc[g]["trades"] += 1
        grade_acc[g]["wins"] += r.get("win", 0)
        grade_acc[g]["r_sum"] += r.get("r_multiple") or 0.0

    grade_summary = {}
    for g, s in grade_acc.items():
        if s["trades"] >= 2:
            grade_summary[g] = {
                "trades": s["trades"],
                "win_rate": round(s["wins"] / s["trades"], 2),
                "avg_r": round(s["r_sum"] / s["trades"], 2),
            }

    # ── Asset-type stats ─────────────────────────────────────────────────────
    at_rows = [r for r in rows if r.get("asset_type") == asset_type]
    asset_stats = {}
    if at_rows:
        wins = sum(r.get("win", 0) for r in at_rows)
        r_sum = sum(r.get("r_multiple") or 0.0 for r in at_rows)
        asset_stats = {
            "win_rate": round(wins / len(at_rows), 2),
            "avg_r": round(r_sum / len(at_rows), 2),
            "total_trades": len(at_rows),
        }

    # ── Pair-specific stats ──────────────────────────────────────────────────
    pair_rows = [r for r in rows if r.get("pair") == pair]
    pair_stats = {}
    if len(pair_rows) >= 3:
        wins = sum(r.get("win", 0) for r in pair_rows)
        r_sum = sum(r.get("r_multiple") or 0.0 for r in pair_rows)
        regimes = [
            r.get("regime") for r in pair_rows if r.get("win") and r.get("regime")
        ]
        best_regime = max(set(regimes), key=regimes.count) if regimes else None
        pair_stats = {
            "win_rate": round(wins / len(pair_rows), 2),
            "avg_r": round(r_sum / len(pair_rows), 2),
            "total_trades": len(pair_rows),
            "best_regime": best_regime,
        }

    # ── Recent failures (last 5 losses) ─────────────────────────────────────
    losses = [r for r in rows if r.get("win") == 0][:5]
    recent_failures = [
        {
            "pair": r.get("pair"),
            "grade": r.get("ai_grade"),
            "r": r.get("r_multiple") or 0.0,
            "regime": r.get("regime"),
            "time_in_trade": r.get("holding_hours", 0.0),
        }
        for r in losses
    ]

    # ── Top performing vote combos ────────────────────────────────────────────
    vote_win_counts: dict = {}
    vote_total_counts: dict = {}
    for r in rows:
        if not r.get("votes_json"):
            continue
        try:
            votes = json.loads(r["votes_json"])
            fired = [k for k, v in votes.items() if (v or 0) > 0]
            for v in fired:
                vote_total_counts[v] = vote_total_counts.get(v, 0) + 1
                if r.get("win"):
                    vote_win_counts[v] = vote_win_counts.get(v, 0) + 1
        except Exception:
            pass

    top_votes = []
    for v, total in vote_total_counts.items():
        if total >= 3:
            wr = round(vote_win_counts.get(v, 0) / total, 2)
            top_votes.append({"vote": v, "win_rate": wr, "count": total})
    top_votes.sort(key=lambda x: x["win_rate"], reverse=True)

    # ── Factor score analysis (from factors_json) ─────────────────────────────
    factor_stats: dict = {}
    for r in rows:
        if not r.get("factors_json"):
            continue
        try:
            fdata = json.loads(r["factors_json"])
            scores = fdata.get("scores") or {}
            for fname, fval in scores.items():
                if fval is None:
                    continue
                factor_stats.setdefault(
                    fname,
                    {
                        "win_sum": 0.0,
                        "loss_sum": 0.0,
                        "win_n": 0,
                        "loss_n": 0,
                        "choppy_time": 0.0,
                        "choppy_n": 0,
                    },
                )
                if r.get("win"):
                    factor_stats[fname]["win_sum"] += fval
                    factor_stats[fname]["win_n"] += 1
                else:
                    factor_stats[fname]["loss_sum"] += fval
                    factor_stats[fname]["loss_n"] += 1

                    # Track time-in-trade for choppy session analysis
                    hold_time = r.get("holding_hours", 0.0)
                    regime = r.get("regime", "")

                    # Penalize factors that result in long hold times in ranging regimes
                    if regime in ["RANGING", "LOW_VOLATILITY"] and hold_time > 2.0:
                        factor_stats[fname]["choppy_time"] += hold_time
                        factor_stats[fname]["choppy_n"] += 1
        except Exception:
            pass

    factor_reliability = []
    for fname, fs in factor_stats.items():
        total_n = fs["win_n"] + fs["loss_n"]
        if total_n >= 5:
            avg_win = round(fs["win_sum"] / fs["win_n"], 3) if fs["win_n"] else 0
            avg_loss = round(fs["loss_sum"] / fs["loss_n"], 3) if fs["loss_n"] else 0

            # Calculate choppy session penalty
            choppy_penalty = 0.0
            if fs["choppy_n"] > 0:
                avg_choppy_time = fs["choppy_time"] / fs["choppy_n"]
                # Higher penalty for longer average time in choppy sessions
                choppy_penalty = round(avg_choppy_time, 2)

            factor_reliability.append(
                {
                    "factor": fname,
                    "avg_win": avg_win,
                    "avg_loss": avg_loss,
                    "count": total_n,
                    "choppy_penalty": choppy_penalty,
                    "choppy_trades": fs["choppy_n"],
                }
            )

    # Sort by choppy penalty first, then by reliability difference
    factor_reliability.sort(
        key=lambda x: (x["choppy_penalty"], abs(x["avg_win"] - x["avg_loss"])),
        reverse=True,
    )

    return {
        "sample_size": len(rows),
        "grade_accuracy": grade_summary,
        "asset_type_stats": asset_stats,
        "pair_stats": pair_stats if pair_stats else None,
        "recent_failures": recent_failures,
        "top_votes": top_votes[:5],
        "factor_reliability": factor_reliability[:8],
    }


# ── Weekly meta-analysis ──────────────────────────────────────────────────────


def get_meta_analysis_context(db_path: str, days: int = 7) -> str:
    """Build a plain-text summary of recent outcomes for Claude meta-analysis."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        with sqlite3.connect(db_path, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM learning_log WHERE ts > ? ORDER BY ts DESC", (cutoff,)
            ).fetchall()
    except Exception:
        return ""

    rows = [dict(r) for r in rows]
    if len(rows) < 3:
        return f"Insufficient data: only {len(rows)} trade(s) in last {days} days."

    wins = sum(r.get("win", 0) for r in rows)
    r_vals = [r.get("r_multiple") or 0.0 for r in rows]
    avg_r = round(sum(r_vals) / len(r_vals), 2) if r_vals else 0
    wr_pct = round(wins / len(rows) * 100, 1)

    by_regime: dict = {}
    for r in rows:
        reg = r.get("regime") or "UNKNOWN"
        if reg not in by_regime:
            by_regime[reg] = {"n": 0, "wins": 0}
        by_regime[reg]["n"] += 1
        by_regime[reg]["wins"] += r.get("win", 0)

    regime_lines = []
    for reg, s in by_regime.items():
        wr = round(s["wins"] / s["n"] * 100, 0) if s["n"] else 0
        regime_lines.append(f"  {reg}: {wr:.0f}% WR ({s['n']} trades)")

    by_pair: dict = {}
    for r in rows:
        p = r.get("pair") or "?"
        if p not in by_pair:
            by_pair[p] = {"n": 0, "wins": 0}
        by_pair[p]["n"] += 1
        by_pair[p]["wins"] += r.get("win", 0)

    worst_pairs = sorted(
        [(p, s) for p, s in by_pair.items() if s["n"] >= 2],
        key=lambda x: x[1]["wins"] / x[1]["n"],
    )[:3]

    lines = [
        f"LEARNING OUTCOMES (last {days} days, N={len(rows)} trades)",
        f"Overall: {wr_pct}% win rate | Avg R: {avg_r:+.2f}",
        "",
        "By regime:",
    ] + regime_lines

    if worst_pairs:
        lines.append("")
        lines.append("Underperforming pairs:")
        for p, s in worst_pairs:
            wr = round(s["wins"] / s["n"] * 100, 0)
            lines.append(f"  {p}: {wr:.0f}% WR ({s['n']} trades)")

    by_grade: dict = {}
    for r in rows:
        g = r.get("ai_grade") or "?"
        if g not in by_grade:
            by_grade[g] = {"n": 0, "wins": 0}
        by_grade[g]["n"] += 1
        by_grade[g]["wins"] += r.get("win", 0)

    if by_grade:
        lines.append("")
        lines.append("AI grade calibration:")
        for g in sorted(by_grade.keys()):
            s = by_grade[g]
            wr = round(s["wins"] / s["n"] * 100, 0) if s["n"] else 0
            lines.append(f"  Grade {g}: {wr:.0f}% WR ({s['n']} trades)")

    return "\n".join(lines)


_META_SYSTEM = """You are a quantitative trading systems analyst reviewing live trade outcomes.
Your job is to identify systematic biases in the AI signal grader and scoring system,
and suggest specific, actionable adjustments.
Be direct, data-driven, and concise. No fluff."""

_META_USER_TMPL = """{context}

Step 1 — Initial analysis:
Based on the above outcomes data, identify:
a) The 2-3 most significant patterns (what's working, what's not)
b) Specific adjustments to make (e.g. "reduce position size on B-grade crypto in RANGING regimes")
c) One thing the signal system is systematically getting wrong

Step 2 — Devil's advocate (challenge your Step 1 conclusions):
Before finalising, argue the opposing case: what evidence in the data contradicts your patterns?
Could the sample size be too small to draw conclusions? Are there confounding factors?
State the strongest counter-argument to your own analysis in one sentence.

Step 3 — Final verdict:
Synthesise Steps 1 and 2 into a balanced conclusion.

Reply in JSON only:
{{"summary":"one sentence overall assessment","insights":["insight1","insight2","insight3"],"adjustments":["adj1","adj2"],"blind_spot":"one key systematic miss","devils_advocate":"strongest counter-argument to the above conclusions"}}"""


def run_meta_analysis(db_path: str, xai_key: str, model: str, days: int = 7) -> dict:
    """Ask xAI Grok to review recent outcomes and identify systematic biases."""
    if not xai_key or xai_key == "YOUR_XAI_API_KEY":
        return {"error": "xAI API key not configured"}

    context = get_meta_analysis_context(db_path, days=days)
    if not context or context.startswith("Insufficient"):
        return {"error": context or "No data"}

    try:
        import openai

        client = openai.OpenAI(api_key=xai_key, base_url="https://api.x.ai/v1")
        resp = client.responses.create(
            model=model,
            max_output_tokens=800,
            input=[
                {"role": "system", "content": _META_SYSTEM},
                {"role": "user", "content": _META_USER_TMPL.format(context=context)},
            ],
        )
        raw = resp.output_text.strip()
        import re
        import json as _json

        try:
            result = _json.loads(raw)
        except Exception:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            result = _json.loads(m.group()) if m else {"summary": raw}

        # Persist to meta_analysis_log
        try:
            with sqlite3.connect(db_path, timeout=15.0) as con:
                con.execute(
                    "INSERT INTO meta_analysis_log (ts, days, summary, insights, raw_response) VALUES (?,?,?,?,?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        days,
                        result.get("summary"),
                        _json.dumps(result.get("insights", [])),
                        raw,
                    ),
                )
                con.commit()
        except Exception as _dbe:
            log.debug(f"[LEARN] meta-analysis DB write failed: {_dbe}")

        log.info(f"[LEARN] Meta-analysis complete: {result.get('summary', '')[:80]}")
        return result

    except Exception as e:
        log.error(f"[LEARN] Meta-analysis failed: {e}")
        return {"error": str(e)}


def get_last_meta_analysis(db_path: str) -> dict | None:
    """Return the most recent meta-analysis result."""
    try:
        with sqlite3.connect(db_path, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT * FROM meta_analysis_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row:
            import json as _json

            return {
                "ts": row["ts"],
                "summary": row["summary"],
                "insights": _json.loads(row["insights"] or "[]"),
            }
    except Exception:
        pass
    return None


# NOTE: build_learning_memory() was removed as dead code (never called).
# The active learning path uses get_ai_learning_context() which returns a dict
# that _build_signal_message() formats into the AI prompt at runtime.
