"""Read-only confluence-score diagnostic over frozen Engine A/B backtests."""
from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import socket
import statistics
import sys
from pathlib import Path

AS_OF = "2026-05-30"
SCOPE = {
    "Engine A": {"BTC/USDT": {"LONG"}, "ETH/USDT": {"LONG"}, "XAG/USD": {"LONG"},
                 "EUR/USD": {"LONG", "SHORT"}, "GBP/USD": {"LONG", "SHORT"}},
    "Engine B": {"XAU/USD": {"LONG", "SHORT"}, "XAG/USD": {"LONG", "SHORT"},
                 "ETH/USDT": {"SHORT"}, "BTC/USDT": {"LONG", "SHORT"},
                 "GBP/USD": {"SHORT"}, "EUR/USD": {"LONG", "SHORT"}},
}


def block_network():
    def blocked(*_a, **_k):
        raise RuntimeError("NETWORK_BLOCKED_CONFLUENCE_DIAGNOSTIC")
    socket.create_connection = blocked
    try:
        import requests
        requests.sessions.Session.request = blocked
    except ImportError:
        pass


def load_athena(root):
    spec = importlib.util.spec_from_file_location("athena_confluence_diagnostic", root / "athena.py")
    mod = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mod; spec.loader.exec_module(mod)
    return mod


def percentile(values, p):
    values = sorted(values)
    if not values: return None
    x = (len(values) - 1) * p; lo = int(x); hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (x - lo)


def metrics(trades):
    r = [float(t["resultR"]) for t in trades]; n = len(r)
    wins = [x for x in r if x > 0]; losses = [x for x in r if x <= 0]
    mean = statistics.fmean(r) if r else 0.0
    sd = statistics.stdev(r) if len(r) > 1 else 0.0
    eq = peak = dd = 0.0
    for x in r:
        eq += x; peak = max(peak, eq); dd = max(dd, peak - eq)
    gl = abs(sum(losses))
    return {"n": n, "win_pct": round(100 * len(wins) / n, 2) if n else None,
            "expR": round(mean, 4) if n else None,
            "SQN": round(mean / sd * math.sqrt(n), 3) if sd else None,
            "profit_factor": round(sum(wins) / gl, 3) if gl else None,
            "avg_win_R": round(statistics.fmean(wins), 4) if wins else None,
            "avg_loss_R": round(statistics.fmean(losses), 4) if losses else None,
            "max_drawdown_R": round(dd, 3), "median_resultR": round(statistics.median(r), 4) if r else None,
            "worst_resultR": min(r) if r else None, "best_resultR": max(r) if r else None}


def practical_thresholds(engine, scores):
    if engine == "Engine A":
        candidates = [1 + 0.25 * i for i in range(9)]
    else:
        candidates = [percentile(scores, p) for p in (0, .1, .2, .3, .4, .5, .6, .7, .8, .9)]
    return sorted(set(round(x, 4) for x in candidates if x is not None and min(scores) <= x <= max(scores)))


def classify(bands, viable, n):
    if n < 30: return "INCONCLUSIVE_SAMPLE"
    low, top = bands[0], bands[-2] if bands[-1]["band"] == "top_10pct" else bands[-1]
    if top.get("expR") is None: return "INCONCLUSIVE_SAMPLE"
    if top["expR"] <= low["expR"]: return "SCORE_NOT_PREDICTIVE"
    if viable: return "SCORE_GATE_VALID"
    if top["expR"] > 0 and top["max_drawdown_R"] > low["max_drawdown_R"]: return "HIGH_SCORE_STILL_DANGEROUS"
    return "SCORE_GATE_TOO_LOW" if top["expR"] > 0 else "SCORE_NOT_PREDICTIVE"


def write_csv(path, rows):
    if not rows: return
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


ENGINE_B_DIAGNOSTIC_FIELDS = (
    "pair", "direction", "date", "resultR", "engine_b_score", "engine_b_max",
    "engine_b_score_ratio", "engine_b_min_score_used", "engine_b_candidate_passed",
    "engine_b_manual_quality_passed_score_6_5",
    "engine_b_manual_quality_passed_ratio_0_75",
    "engine_b_autotrade_quality_passed_ratio_0_80", "signalTier",
    "autotradeIngressEligible", "b_only_watchlist_reason", "executionConvictionBase",
    "executionConvictionEffective", "autoMinConviction", "alignedDiscountApplied",
    "metaThresholdDelta", "failedStage", "blockReason",
)


def main():
    root = Path(__file__).resolve().parents[2]; os.chdir(root); sys.path.insert(0, str(root))
    os.environ["BACKTEST_DATA_AS_OF"] = AS_OF; os.environ["ATHENA_DIAGNOSTIC_MODE"] = "1"
    athena = load_athena(root)
    from athena_runtime import rt
    from scoring import get_score_threshold
    from athena_research.engine_b_quality_report import build_engine_b_quality_rows
    import engine_a_v3.evaluator as v3_evaluator
    import engine_a_v3.backtest as v3_backtest
    captured_v3 = {}
    original_v3_evaluate = v3_evaluator.evaluate_engine_a_v3
    def capture_v3_score(*args, **kwargs):
        signal = original_v3_evaluate(*args, **kwargs)
        captured_v3[signal.signalId] = {
            "score": signal.confluenceScore,
            "confluenceScore": signal.confluenceScore,
            "maxScore": signal.maxScore,
            "scoreNorm": signal.scoreNorm,
            "conviction": signal.conviction,
            "confluenceThreshold": signal.confluenceThreshold,
            "factorScores": signal.factorScores,
            "componentScores": signal.componentScores,
            "confidenceDetail": signal.confidenceDetail,
            "factorDiagnostics": signal.factorDiagnostics,
            "scoringProfile": signal.scoringProfile,
        }
        return signal
    v3_evaluator.evaluate_engine_a_v3 = capture_v3_score
    v3_backtest.evaluate_engine_a_v3 = capture_v3_score
    rt().AUDIT_DB = str(root / "tmp" / "confluence_score_diagnostic.db"); block_network()
    pairs = {p["display"]: p for p in athena.ALL_PAIRS}
    raw = {}; field_rows = []
    only_engine = os.environ.get("CONFLUENCE_AUDIT_ENGINE")
    active_scope = {k: v for k, v in SCOPE.items() if not only_engine or k == only_engine}
    for engine, scoped in active_scope.items():
        runner = athena.backtest_pair if engine == "Engine A" else athena.backtest_pair_naked
        for pair, directions in scoped.items():
            result = runner(dict(pairs[pair]), style="auto")
            trades = result.get("trades", [])
            for direction in directions:
                key = f"{engine}|{pair}|{direction}"
                selected = [dict(t) for t in trades if str(t.get("direction", "")).upper() == direction]
                if engine == "Engine A":
                    for trade in selected:
                        trade.update(captured_v3.get(trade.get("signalId"), {}))
                raw[key] = [t for t in selected if t.get("score") is not None and t.get("resultR") is not None]
                fields = sorted({k for t in raw[key] for k in t})
                field_rows.append({"engine": engine, "pair": pair, "direction": direction,
                                   "n": len(raw[key]), "excluded_missing_score_or_result": len(selected)-len(raw[key]),
                                   "fields": ";".join(fields)})
    dist = []; thresholds = []; bands_out = []; losers = []; minimums = []
    engine_b_annotated = []
    for key, trades in raw.items():
        engine, pair, direction = key.split("|"); scores = [float(t["score"]) for t in trades]
        wins = [float(t["score"]) for t in trades if float(t["resultR"]) > 0]
        losses = [float(t["score"]) for t in trades if float(t["resultR"]) <= 0]
        base = {"engine": engine, "pair": pair, "direction": direction}
        dist.append(base | {"n": len(scores), "min_score": min(scores) if scores else None,
            "max_score": max(scores) if scores else None, "average_score": round(statistics.fmean(scores),4) if scores else None,
            "median_score": round(statistics.median(scores),4) if scores else None,
            "winner_average_score": round(statistics.fmean(wins),4) if wins else None,
            "winner_median_score": round(statistics.median(wins),4) if wins else None,
            "loser_average_score": round(statistics.fmean(losses),4) if losses else None,
            "loser_median_score": round(statistics.median(losses),4) if losses else None,
            "winner_minus_loser_median": round(statistics.median(wins)-statistics.median(losses),4) if wins and losses else None})
        viable = []
        for th in practical_thresholds(engine, scores) if scores else []:
            kept = [t for t in trades if float(t["score"]) >= th]; row = base | {"threshold": th,
                "retained_pct": round(100*len(kept)/len(trades),2)} | metrics(kept); thresholds.append(row)
            if row["n"] >= 30 and row["expR"] > 0 and (row["SQN"] or -999) >= 1.5 and (row["profit_factor"] or 0) > 1.2:
                viable.append(row)
        qs = [percentile(scores, p) for p in (.25,.5,.75,.9)] if scores else [None]*4
        specs = [("bottom_25pct", None, qs[0]),("25_50pct",qs[0],qs[1]),("50_75pct",qs[1],qs[2]),
                 ("top_25pct",qs[2],None),("top_10pct",qs[3],None)]
        local_bands=[]
        for name, lo, hi in specs:
            kept=[t for t in trades if (lo is None or float(t["score"]) >= lo) and (hi is None or float(t["score"]) < hi)]
            row=base|{"band":name,"score_low":lo,"score_high_exclusive":hi}|metrics(kept); bands_out.append(row); local_bands.append(row)
        current = get_score_threshold(pairs[pair]) if engine == "Engine A" else None
        high = qs[2]
        for t in trades:
            if float(t["resultR"]) <= 0 and float(t["score"]) >= high:
                losers.append(base | {"timestamp":t.get("date"),"score":t.get("score"),"resultR":t.get("resultR"),
                    "exit_reason":t.get("exit_reason",t.get("outcome")),"SL":t.get("sl"),"TP":t.get("tp1"),
                    "MFE_R":t.get("MFE_R",t.get("mfe_r")),"MAE_R":t.get("MAE_R",t.get("mae_r")),
                    "session":t.get("session"),"regime":t.get("regime"),"setup_type":t.get("setup_type"),
                    "failure_reason":t.get("failure_reason"),"top25_cutoff":high,"current_threshold":current})
        chosen=min(viable,key=lambda x:x["threshold"]) if viable else None
        minimums.append(base | {"minimum_viable_threshold":chosen["threshold"] if chosen else "NO_SAFE_SCORE_THRESHOLD_FOUND",
            "classification":classify(local_bands,chosen,len(trades)),"n":len(trades)})
        if engine == "Engine B":
            for trade in trades:
                engine_b_annotated.append(trade | {"pair": pair, "direction": direction})
    annotated_b, quality_layers = build_engine_b_quality_rows(engine_b_annotated)
    diagnostic_trades = [
        {field: row.get(field) for field in ENGINE_B_DIAGNOSTIC_FIELDS}
        for row in annotated_b
    ]
    out=root/"reports"; stem="confluence_score_audit_2026-05-30" + ("_engine_a" if only_engine == "Engine A" else "")
    for suffix, rows in (("fields",field_rows),("distribution",dist),("thresholds",thresholds),("bands",bands_out),("high_score_losers",losers),("minimums",minimums)):
        write_csv(out/f"{stem}_{suffix}.csv",rows)
    write_csv(out/f"{stem}_engine_b_quality_layers.csv", quality_layers)
    write_csv(out/f"{stem}_engine_b_diagnostic_trades.csv", diagnostic_trades)
    (out/f"{stem}.json").write_text(json.dumps({"as_of":AS_OF,"fields":field_rows,"distribution":dist,
        "thresholds":thresholds,"bands":bands_out,"high_score_losers":losers,"minimums":minimums,
        "engine_b_diagnostic_trades":diagnostic_trades,"engine_b_quality_layers":quality_layers},indent=2),encoding="utf-8")
    print(out/f"{stem}.json")


if __name__ == "__main__": main()
