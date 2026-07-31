"""Measure what the 2026-07-31 crypto volume fixes actually changed.

Manual/on-demand tool. Read-only: fetches candles and reads the trade-bucket
store, computes nothing that feeds scoring, writes nothing but its own report.

Two independent checks:

``forming``  Engine D forming-bar trim (fix 3). Recomputes absorption, CVD and
             VWAP lean on the same fetched series with and without the trailing
             forming bar, and reports how often the verdict flips. The trim is
             correct on principle — a partial bar can never trip
             ``vol >= sma * mult`` while dragging the SMA down for every other
             bar — but the *magnitude* was uncalibrated, which is what this
             measures.

``venue``    Trade-bucket venue guard (fix 4). Reports the Bybit-vs-Binance
             basis per symbol and whether the live trade-bucket POC sits inside
             our own candle range, i.e. whether
             ENGINE_B_CRYPTO_AGGTRADE_VENUE_PRICE_GUARD would fire. In normal
             operation it must not: perp basis is a few bps against a 0.5%
             tolerance. Anything firing here is a real venue/symbol divergence.

Usage:
    python scripts/diagnostics/crypto_volume_fix_validation.py            # both
    python scripts/diagnostics/crypto_volume_fix_validation.py --check forming
    python scripts/diagnostics/crypto_volume_fix_validation.py --symbols BTCUSDT,ETHUSDT
    python scripts/diagnostics/crypto_volume_fix_validation.py --json out.json

Run it a few times across different points in the M15 bar (just after a close,
and ~halfway through) — the forming-bar effect is largest mid-bar and near zero
right after a close, so a single sample understates it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.3f}%"


# ── fix 3: Engine D forming-bar trim ────────────────────────────────────────

def _forming_bar_report(symbols: list[str], tf: str = "M15") -> list[dict]:
    import scalp_engine
    from scalp_engine import (
        _check_absorption,
        _check_cvd,
        _check_vwap_lean,
        _scalp_crypto_pair_dict,
        _scalp_drop_forming_bar,
        _scalp_fetch_candles,
    )

    rows: list[dict] = []
    for display in symbols:
        pair = _scalp_crypto_pair_dict(display)
        raw, source = _scalp_fetch_candles(pair, tf, 200)
        if not raw or len(raw) < 40:
            rows.append({"symbol": display, "error": "insufficient_candles", "bars": len(raw or [])})
            continue

        trimmed = _scalp_drop_forming_bar(raw, tf)
        dropped = len(raw) - len(trimmed)

        def _measure(candles: list[dict]) -> dict:
            try:
                absorption = _check_absorption(candles, asset_type="crypto")
            except TypeError:
                absorption = _check_absorption(candles)
            try:
                cvd = _check_cvd(candles, asset_type="crypto")
            except TypeError:
                cvd = _check_cvd(candles)
            price = float(candles[-1].get("close") or 0)
            vwap = _check_vwap_lean(candles, price)
            vols = [float(c.get("vol", 0) or 0) for c in candles[-20:]]
            return {
                "absorption_detected": bool(absorption.get("detected")),
                "absorption_count": len(absorption.get("hits") or []) or absorption.get("count"),
                "cvd_direction": cvd.get("direction"),
                "vwap_lean": vwap.get("lean"),
                "vol_sma20": sum(vols) / len(vols) if vols else 0.0,
                "last_bar_vol": float(candles[-1].get("vol", 0) or 0),
            }

        with_forming = _measure(raw)
        without_forming = _measure(trimmed)
        sma_a = with_forming["vol_sma20"] or 0.0
        sma_b = without_forming["vol_sma20"] or 0.0
        rows.append(
            {
                "symbol": display,
                "source": source,
                "bars": len(raw),
                "forming_bar_dropped": dropped,
                "with_forming": with_forming,
                "without_forming": without_forming,
                "absorption_flipped": with_forming["absorption_detected"]
                != without_forming["absorption_detected"],
                "cvd_flipped": with_forming["cvd_direction"] != without_forming["cvd_direction"],
                "vwap_flipped": with_forming["vwap_lean"] != without_forming["vwap_lean"],
                "vol_sma20_shift_pct": ((sma_b - sma_a) / sma_a) if sma_a > 0 else None,
                # How complete the forming bar was when sampled. Near 1.0 means this
                # sample lands just before a close and understates the effect.
                "forming_bar_vol_ratio": (
                    (with_forming["last_bar_vol"] / sma_b) if sma_b > 0 and dropped else None
                ),
            }
        )
    return rows


# ── fix 4: trade-bucket venue guard ─────────────────────────────────────────

def _venue_report(symbols: list[str], tf: str = "M15") -> list[dict]:
    from config import CONFIG
    from data_feeds import _fetch_bybit_klines
    from athena.microstructure.aggtrade_volume import (
        build_trade_bucket_volume_profile,
        resolve_trade_bucket_exchange,
        trade_bucket_profile_price_consistent,
    )
    from athena_runtime import rt

    scalp_cfg = CONFIG.get("SCALP_ENGINE", {}) or {}
    tolerance = float(
        CONFIG.get("ENGINE_B_CRYPTO_AGGTRADE_VENUE_PRICE_TOLERANCE_PCT", 0.005)
    )
    bucket_exchange = resolve_trade_bucket_exchange(scalp_cfg)

    rows: list[dict] = []
    for display in symbols:
        sym = display.replace("/", "").upper()
        entry: dict = {"symbol": sym, "bucket_exchange": bucket_exchange}

        bybit = _fetch_bybit_klines(sym, tf, 50) or []
        binance = []
        try:
            binance = rt().fetch_candles(
                {"display": display, "symbol": sym, "type": "crypto", "source": "binance"},
                tf,
                50,
            ) or []
        except Exception as exc:
            entry["binance_error"] = str(exc)

        if bybit and binance:
            bybit_close = float(bybit[-1].get("close") or 0)
            binance_close = float(binance[-1].get("close") or 0)
            if bybit_close > 0 and binance_close > 0:
                basis = abs(bybit_close - binance_close) / bybit_close
                entry.update(
                    {
                        "bybit_close": bybit_close,
                        "binance_close": binance_close,
                        "basis_pct": basis,
                        "basis_vs_tolerance": basis / tolerance if tolerance else None,
                        "basis_exceeds_tolerance": basis > tolerance,
                    }
                )
        else:
            entry["candles_error"] = f"bybit={len(bybit)} binance={len(binance)}"

        vp = build_trade_bucket_volume_profile(display, scalp_cfg, require_fresh=True)
        entry["vp_valid"] = bool(vp.get("valid"))
        entry["vp_reason"] = vp.get("reason")
        entry["vp_bucket_count"] = vp.get("bucket_count")
        entry["vp_poc"] = vp.get("poc")
        if vp.get("valid") and bybit:
            ok, reason = trade_bucket_profile_price_consistent(
                vp, bybit, tolerance_pct=tolerance
            )
            entry["guard_passes_vs_bybit"] = ok
            entry["guard_reason"] = reason
        rows.append(entry)
    return rows


# ── entrypoint ──────────────────────────────────────────────────────────────

def _resolve_symbols(explicit: str | None) -> list[str]:
    """Enabled crypto symbols, read from athena.py without importing the monolith.

    Same approach as tools/check_trade_buckets.py: importing athena.py pulls the
    whole app up (and trips the live-order safety gate) for what is a static list.
    """
    if explicit:
        return [s.strip() for s in explicit.split(",") if s.strip()]

    import re

    text = (Path(__file__).resolve().parents[2] / "athena.py").read_text(encoding="utf-8")
    start = text.index("CRYPTO_PAIRS = [")
    end = text.index("\nALL_PAIRS = (", start)
    found = re.findall(r'"symbol":\s*"([^"]+)"', text[start:end])
    return sorted({s.replace("/", "").upper() for s in found if s.strip()})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", choices=("forming", "venue", "both"), default="both")
    ap.add_argument("--symbols", help="Comma-separated, e.g. BTCUSDT,ETHUSDT")
    ap.add_argument("--tf", default="M15")
    ap.add_argument("--json", help="Write the full report to this path")
    args = ap.parse_args()

    symbols = _resolve_symbols(args.symbols)
    if not symbols:
        print("No enabled crypto symbols resolved.")
        return 1
    print(f"Symbols ({len(symbols)}): {', '.join(symbols)}")
    print(f"Sampled at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")

    report: dict = {"generated_utc": time.time(), "tf": args.tf, "symbols": symbols}

    if args.check in ("forming", "both"):
        rows = _forming_bar_report(symbols, args.tf)
        report["forming_bar"] = rows
        usable = [r for r in rows if "error" not in r]
        print("=== FIX 3: Engine D forming-bar trim ===")
        print(f"{'symbol':<12}{'src':<16}{'drop':<6}{'absorp':<8}{'cvd':<6}{'vwap':<6}{'sma_shift':>10}")
        for r in rows:
            if "error" in r:
                print(f"{r['symbol']:<12}{r['error']}")
                continue
            print(
                f"{r['symbol']:<12}{str(r['source']):<16}{r['forming_bar_dropped']:<6}"
                f"{'FLIP' if r['absorption_flipped'] else '-':<8}"
                f"{'FLIP' if r['cvd_flipped'] else '-':<6}"
                f"{'FLIP' if r['vwap_flipped'] else '-':<6}"
                f"{_fmt_pct(r['vol_sma20_shift_pct']):>10}"
            )
        if usable:
            print(
                f"\n  flips: absorption={sum(r['absorption_flipped'] for r in usable)}/{len(usable)}"
                f"  cvd={sum(r['cvd_flipped'] for r in usable)}/{len(usable)}"
                f"  vwap={sum(r['vwap_flipped'] for r in usable)}/{len(usable)}"
            )
            print(
                "  A flip means the forming bar was changing the verdict. High counts are the "
                "bug being fixed, not a regression; re-sample mid-bar to see the worst case.\n"
            )

    if args.check in ("venue", "both"):
        rows = _venue_report(symbols, args.tf)
        report["venue"] = rows
        print("=== FIX 4: trade-bucket venue guard ===")
        print(f"{'symbol':<12}{'basis':>10}{'x_tol':>8}{'vp':<8}{'guard':<8}{'reason':<28}")
        for r in rows:
            ratio = r.get("basis_vs_tolerance")
            print(
                f"{r['symbol']:<12}{_fmt_pct(r.get('basis_pct')):>10}"
                f"{(f'{ratio:.2f}' if ratio is not None else 'n/a'):>8}"
                f"{('ok' if r.get('vp_valid') else 'none'):<8}"
                f"{('PASS' if r.get('guard_passes_vs_bybit') else ('FIRE' if r.get('guard_passes_vs_bybit') is False else '-')):<8}"
                f"{str(r.get('guard_reason') or r.get('vp_reason') or ''):<28}"
            )
        fired = [r for r in rows if r.get("guard_passes_vs_bybit") is False]
        exceeded = [r for r in rows if r.get("basis_exceeds_tolerance")]
        print(
            f"\n  guard fired: {len(fired)}/{len(rows)}   basis over tolerance: {len(exceeded)}/{len(rows)}"
        )
        print(
            "  Expected: both zero. A firing guard is a real venue/symbol divergence — "
            "investigate it, do not widen the tolerance.\n"
        )

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
