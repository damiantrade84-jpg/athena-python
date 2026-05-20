"""Read-only Engine A V2 forensic audit helper.

Scores representative pairs via calc_confluence (forex via MT5 confirmed candles),
writes JSON summary under logs/audits/. Does not mutate config or scoring logic.

Usage::

    .venv\\Scripts\\python.exe tools/engine_a_v2_forensic_audit.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators import calc_indicators_with_normalized  # noqa: E402
from scoring import calc_confluence  # noqa: E402

FOREX_PAIRS = [
    {"symbol": "EURUSD=X", "type": "forex", "display": "EUR/USD", "source": "mt5", "enabled": True},
    {"symbol": "GBPUSD=X", "type": "forex", "display": "GBP/USD", "source": "mt5", "enabled": True},
    {"symbol": "USDJPY=X", "type": "forex", "display": "USD/JPY", "source": "mt5", "enabled": True},
    {"symbol": "EURCHF=X", "type": "forex", "display": "EUR/CHF", "source": "mt5", "enabled": True},
    {"symbol": "AUDUSD=X", "type": "forex", "display": "AUD/USD", "source": "mt5", "enabled": True},
    {"symbol": "AUDNZD=X", "type": "forex", "display": "AUD/NZD", "source": "mt5", "enabled": True},
    {"symbol": "NZDUSD=X", "type": "forex", "display": "NZD/USD", "source": "mt5", "enabled": True},
    {"symbol": "USDCAD=X", "type": "forex", "display": "USD/CAD", "source": "mt5", "enabled": True},
    {"symbol": "USDCHF=X", "type": "forex", "display": "USD/CHF", "source": "mt5", "enabled": True},
    {"symbol": "EURJPY=X", "type": "forex", "display": "EUR/JPY", "source": "mt5", "enabled": True},
    {"symbol": "GBPJPY=X", "type": "forex", "display": "GBP/JPY", "source": "mt5", "enabled": True},
    {"symbol": "AUDJPY=X", "type": "forex", "display": "AUD/JPY", "source": "mt5", "enabled": True},
    {"symbol": "EURAUD=X", "type": "forex", "display": "EUR/AUD", "source": "mt5", "enabled": True},
    {"symbol": "GBPAUD=X", "type": "forex", "display": "GBP/AUD", "source": "mt5", "enabled": True},
    {"symbol": "USDZAR=X", "type": "forex", "display": "USD/ZAR", "source": "mt5", "enabled": True},
    {"symbol": "USDMXN=X", "type": "forex", "display": "USD/MXN", "source": "mt5", "enabled": True},
    {"symbol": "USDSGD=X", "type": "forex", "display": "USD/SGD", "source": "mt5", "enabled": True},
    {"symbol": "USDBRL=X", "type": "forex", "display": "USD/BRL", "source": "mt5", "enabled": True},
    {"symbol": "USDINR=X", "type": "forex", "display": "USD/INR", "source": "mt5", "enabled": True},
    {"symbol": "AUDCHF=X", "type": "forex", "display": "AUD/CHF", "source": "mt5", "enabled": True},
    {"symbol": "EURGBP=X", "type": "forex", "display": "EUR/GBP", "source": "mt5", "enabled": True},
]

OTHER_PAIRS = [
    {"symbol": "BTCUSDT", "display": "BTC/USDT", "type": "crypto", "source": "binance", "enabled": True},
    {"symbol": "ETHUSDT", "display": "ETH/USDT", "type": "crypto", "source": "binance", "enabled": True},
    {"symbol": "GC=F", "display": "XAU/USD", "type": "commodity", "source": "mt5", "enabled": True},
]

CANDLE_LIMITS = {"D1": 260, "H4": 260, "H1": 260}


def _fetch_confirmed(pair: dict, tf: str, limit: int) -> list[dict]:
    from candle_manager import fetch_market_state

    state = fetch_market_state(pair, tf, limit)
    if isinstance(state, dict):
        return list(state.get("confirmed") or [])
    return []


def _fetch_binance(pair: dict, tf: str, limit: int) -> list[dict]:
    try:
        from athena import fetch_candles

        return list(fetch_candles(pair, tf, limit) or [])
    except Exception as exc:
        return []


def score_pair(pair: dict) -> dict[str, Any]:
    display = pair.get("display", "?")
    ptype = pair.get("type", "?")
    fetch_fn = _fetch_binance if pair.get("source") == "binance" else _fetch_confirmed
    candles_by_tf: dict[str, list] = {}
    snaps: dict[str, dict] = {}
    errors: list[str] = []

    for tf, limit in CANDLE_LIMITS.items():
        try:
            raw = fetch_fn(pair, tf, limit)
        except Exception as exc:
            errors.append(f"{tf}: {exc}")
            raw = []
        candles_by_tf[tf] = raw
        if len(raw) < 30:
            errors.append(f"{tf}: insufficient ({len(raw)})")
        if raw:
            snaps[tf] = calc_indicators_with_normalized(raw, ptype) or {"snap": {}}
        else:
            snaps[tf] = {"snap": {}}

    if not (snaps.get("H4") or {}).get("snap"):
        return {"pair": display, "asset_type": ptype, "status": "error", "errors": errors}

    result = calc_confluence(
        snaps.get("D1") or {"snap": {}},
        snaps["H4"],
        snaps.get("H1") or {"snap": {}},
        1.0,
        {},
        pair,
        "neutral",
        d1_candles=candles_by_tf.get("D1") or [],
        h4_candles=candles_by_tf.get("H4") or [],
        h1_candles=candles_by_tf.get("H1") or [],
    )
    fd = result.get("factorDiagnostics") or {}
    h4_snap = (snaps.get("H4") or {}).get("snap") or {}
    d1_snap = (snaps.get("D1") or {}).get("snap") or {}
    trend_coherence = fd.get("trendCoherence") or {}

    return {
        "pair": display,
        "asset_type": ptype,
        "normalized": pair.get("symbol"),
        "route_caller": "tools/engine_a_v2_forensic_audit.py",
        "scorer_selected": fd.get("scorerSelected"),
        "v2_called": True,
        "legacy_called": False,
        "fallback_used": False,
        "score": float(result.get("score") or 0),
        "direction": result.get("direction"),
        "max_score": result.get("maxScoreOverride"),
        "abort_reason": fd.get("abortReason"),
        "engine_version": fd.get("engineVersion"),
        "d1_count": len(candles_by_tf.get("D1") or []),
        "h4_count": len(candles_by_tf.get("H4") or []),
        "h1_count": len(candles_by_tf.get("H1") or []),
        "adx_value": fd.get("trendStateAdxValue"),
        "adx_source": (fd.get("regimeLabelsDualCapture") or {}).get("trendStateAdxSource"),
        "trend_coherence": trend_coherence,
        "min_directional_failed": fd.get("minDirectionalFailed"),
        "feed_status": fd.get("feedStatus"),
        "factor_scores": result.get("factor_scores"),
        "errors": errors,
        "status": "ok",
    }


def main() -> int:
    out_dir = ROOT / "logs" / "audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"engine_a_v2_forensic_{ts}.json"

    rows: list[dict[str, Any]] = []
    for pair in FOREX_PAIRS + OTHER_PAIRS:
        rows.append(score_pair(pair))

    abort_hist = Counter(
        r.get("abort_reason") or ("non_zero" if (r.get("score") or 0) > 0 else "no_abort_tag")
        for r in rows
        if r.get("status") == "ok" and (r.get("score") or 0) == 0
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "zero_score_abort_histogram": dict(abort_hist),
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out_path}")
    for r in rows:
        if r.get("status") != "ok":
            print(f"ERR {r.get('pair')}: {r.get('errors')}")
            continue
        print(
            f"{r['pair']:12s} score={r['score']:.2f} dir={r.get('direction')} "
            f"abort={r.get('abort_reason')} D1={r['d1_count']} H4={r['h4_count']} H1={r['h1_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
