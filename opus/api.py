"""Flask blueprint for OPUS: /api/opus/*.

Self-contained. It composes only OPUS modules, registers on the host app
without touching any existing route, and degrades to a clear JSON error rather
than raising into the host when a venue is unavailable.

Endpoints
    GET  /api/opus/status      engine identity, mode, universe, store stats
    GET  /api/opus/config      the resolved (non-secret) configuration
    POST /api/opus/scan        run a scan; optional symbol subset
    GET  /api/opus/signals     recently stored signals
    POST /api/opus/execute     submit ONE signal (paper by default)
    GET  /api/opus/orders      order history
    POST /api/opus/reconcile   sweep working orders (fill / expire / hold)
    GET  /api/opus/account     broker account state
    POST /api/opus/resolve     label matured signals (feeds calibration)
    GET  /api/opus/calibration calibration and evidence per bucket
    GET  /api/opus/indicators  full indicator detail for one symbol
"""

from __future__ import annotations

import time

from flask import Blueprint, jsonify, request

import opus.config as config
from opus import engine
from opus.data.feed import FeedError, fetch_bundle
from opus.data.store import default_store
from opus.execution import reconcile, router
from opus.scoring import resolver
from opus.types import plain
from opus.version import ENGINE_NAME, ENGINE_VERSION, SCORE_MODEL_VERSION

_last_scan: dict = {"result": None, "ts": 0.0}
_last_sweep: dict = {"ts": 0.0}

def _sweep_if_due(force: bool = False) -> dict | None:
    """Reconcile working orders, at most once per configured interval.

    The status poll is the reconciler's heartbeat, but it must not turn every
    poll into a round of venue calls. One sweep per interval is enough: a
    resting order's expiry is measured in minutes.
    """
    now = time.time()
    interval = float(
        config.load()["EXECUTION"].get("reconcile_interval_sec", 20.0) or 0.0
    )
    if not force and (now - float(_last_sweep["ts"])) < interval:
        return None
    try:
        store = default_store()
        if not force and int(store.stats().get("working", 0) or 0) <= 0:
            _last_sweep["ts"] = now
            return None
        report = reconcile.reconcile(store, now=now)
    except Exception as exc:  # noqa: BLE001 - a sweep failure is not fatal
        _last_sweep["ts"] = now
        return {"error": str(exc)}
    _last_sweep["ts"] = now
    return report.as_dict()


def _payload() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _error(message: str, status: int = 400, **extra):
    body = {"ok": False, "error": message}
    body.update(extra)
    return jsonify(body), status


def create_opus_blueprint() -> Blueprint:
    bp = Blueprint("opus", __name__, url_prefix="/api/opus")

    @bp.get("/status")
    def status():
        armed, why = router.live_enabled()
        # The panel polls this, so it is the heartbeat a resting order gets:
        # without a sweep somewhere on a timer, a working order is only ever
        # resolved when a human happens to click something.
        sweep = _sweep_if_due()
        try:
            stats = default_store().stats()
        except Exception as exc:  # noqa: BLE001
            stats = {"error": str(exc)}
        return jsonify({
            "ok": True,
            "engine": ENGINE_NAME,
            "engineVersion": ENGINE_VERSION,
            "scoreModelVersion": SCORE_MODEL_VERSION,
            "enabled": bool(config.load().get("ENABLED", True)),
            "mode": str(config.load().get("MODE", "paper")),
            "liveArmed": armed,
            "liveReason": why,
            "universe": config.universe(),
            "universeSource": config.universe_source(),
            "store": stats,
            "reconcile": sweep,
            "lastScanTs": _last_scan["ts"],
            "serverTs": time.time(),
        })

    @bp.get("/config")
    def get_config():
        """The resolved configuration. Contains no credentials by design."""
        cfg = dict(config.load())
        return jsonify({"ok": True, "config": plain(cfg)})

    @bp.post("/scan")
    def scan():
        body = _payload()
        symbols = body.get("symbols") or None
        if symbols is not None and not isinstance(symbols, list):
            return _error("'symbols' must be a list")

        equity = body.get("equity")
        try:
            equity = float(equity) if equity is not None else None
        except (TypeError, ValueError):
            return _error("'equity' must be numeric")

        include_blocked = bool(body.get("includeBlocked", True))
        try:
            store = default_store()
        except Exception:  # noqa: BLE001
            store = None

        try:
            result = engine.scan(
                symbols=symbols, equity=equity,
                include_blocked=include_blocked, store=store,
            )
        except Exception as exc:  # noqa: BLE001
            return _error(f"scan failed: {type(exc).__name__}: {exc}", 500)

        payload = result.as_dict()
        _last_scan["result"] = payload
        _last_scan["ts"] = time.time()
        return jsonify({"ok": True, **payload})

    @bp.get("/signals")
    def signals():
        limit = min(int(request.args.get("limit", 60) or 60), 500)
        decision = request.args.get("decision") or None
        try:
            rows = default_store().recent_signals(limit=limit, decision=decision)
        except Exception as exc:  # noqa: BLE001
            return _error(f"store unavailable: {exc}", 500)
        return jsonify({"ok": True, "signals": rows, "count": len(rows)})

    @bp.post("/execute")
    def execute():
        """Submit one signal. Paper unless the caller explicitly asks for live
        AND both live switches are armed."""
        body = _payload()
        signal_id = str(body.get("signalId") or "").strip()
        if not signal_id:
            return _error("'signalId' is required")

        request_live = bool(body.get("live", False))
        units = body.get("units")
        try:
            units = float(units) if units is not None else None
        except (TypeError, ValueError):
            return _error("'units' must be numeric")

        # Re-derive the signal from a FRESH scan of that symbol rather than
        # trusting a client-supplied payload. A stored signal carries a stale
        # quote, and a client-supplied one could carry anything at all.
        symbol = str(body.get("symbol") or "").strip()
        if not symbol:
            return _error("'symbol' is required so the signal can be re-derived")

        try:
            store = default_store()
        except Exception:  # noqa: BLE001
            store = None

        try:
            fresh = engine.scan(symbols=[symbol], store=store)
        except Exception as exc:  # noqa: BLE001
            return _error(f"could not re-derive signal: {exc}", 500)

        match = next((s for s in fresh.signals if s.signal_id == signal_id), None)
        if match is None:
            return _error(
                "signal is no longer present on a fresh scan; it has expired "
                "or the setup has changed",
                409,
                availableSignalIds=[s.signal_id for s in fresh.signals],
            )

        result = router.submit(
            match, request_live=request_live, units=units, store=store
        )
        return jsonify({
            "ok": bool(result.ok),
            "order": result.as_dict(),
            "signal": match.as_dict(),
        }), (200 if result.ok else 422)

    @bp.post("/reconcile")
    def reconcile_orders():
        """Sweep working orders: fill, expire, or leave alone."""
        report = _sweep_if_due(force=True)
        if report is not None and "error" in report:
            return _error(f"reconcile failed: {report['error']}", 500)
        return jsonify({"ok": True, "report": report or {}})

    @bp.get("/orders")
    def orders():
        limit = min(int(request.args.get("limit", 100) or 100), 500)
        try:
            rows = default_store().orders(limit=limit)
        except Exception as exc:  # noqa: BLE001
            return _error(f"store unavailable: {exc}", 500)
        return jsonify({"ok": True, "orders": rows, "count": len(rows)})

    @bp.get("/account")
    def account():
        armed, why = router.live_enabled()
        from opus.execution.broker import PaperBroker

        accounts = [PaperBroker().account()]
        # Report the connected live account even when live is disarmed, so the
        # UI can show WHICH account would be traded before anything is armed.
        for venue in sorted({u["venue"] for u in config.universe()}):
            try:
                broker = router._broker_for(venue, True)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                continue
            if not broker.is_live:
                continue
            try:
                info = broker.account()
                info["accountKind"] = broker.account_kind()
                info["venue"] = venue
                accounts.append(info)
            except Exception as exc:  # noqa: BLE001
                accounts.append({
                    "broker": broker.name, "venue": venue,
                    "available": False, "error": str(exc),
                })
        return jsonify({
            "ok": True, "liveArmed": armed, "liveReason": why,
            "requireDemoAccount": bool(
                config.load()["EXECUTION"].get("require_demo_account", True)
            ),
            "accounts": accounts,
        })

    @bp.post("/resolve")
    def resolve():
        limit = int(_payload().get("limit", 200) or 200)
        try:
            store = default_store()
            report = resolver.resolve_pending(store, limit=limit)
            store.calibrators(force_refit=True)
        except Exception as exc:  # noqa: BLE001
            return _error(f"resolve failed: {exc}", 500)
        return jsonify({"ok": True, "report": report.as_dict()})

    @bp.get("/calibration")
    def calibration():
        try:
            store = default_store()
            calibrators = store.calibrators()
            validations = store.validations()
        except Exception as exc:  # noqa: BLE001
            return _error(f"store unavailable: {exc}", 500)
        return jsonify({
            "ok": True,
            "calibrators": {k: v.as_dict() for k, v in calibrators.items()},
            "validations": {k: v.as_dict() for k, v in validations.items()},
            "enforced": bool(config.load()["VALIDATION"].get("enforce", False)),
        })

    @bp.get("/indicators/<symbol>")
    def indicators(symbol: str):
        """Full indicator detail for one symbol - the 'why' behind a signal."""
        spec = config.universe_map().get(symbol.upper())
        if spec is None:
            return _error(f"'{symbol}' is not in the OPUS universe", 404)

        try:
            bundle = fetch_bundle(spec)
        except FeedError as exc:
            return _error(str(exc), 503)
        except Exception as exc:  # noqa: BLE001
            return _error(f"{type(exc).__name__}: {exc}", 500)

        from opus.indicators import regime as regime_mod, temporal
        from opus.indicators.base import MarketContext

        ctx = MarketContext.build(
            symbol=bundle.symbol, display=bundle.display,
            asset_class=bundle.asset_class, context=bundle.context,
            structure=bundle.structure, trigger=bundle.trigger,
            quote=bundle.quote, now=time.time(),
        )
        readings = engine.read_indicators(ctx, book=bundle.book)
        return jsonify({
            "ok": True,
            "symbol": bundle.symbol,
            "display": bundle.display,
            "assetClass": bundle.asset_class,
            "venue": bundle.venue,
            "timeframes": bundle.timeframes,
            "sigma": round(float(ctx.sigma), 10),
            "price": round(float(ctx.last_price), 10),
            "readings": [r.as_dict() for r in readings],
            "regime": regime_mod.compute(ctx).as_dict(),
            "temporal": temporal.compute(ctx).as_dict(),
            "bars": {
                "context": len(bundle.context),
                "structure": len(bundle.structure),
                "trigger": len(bundle.trigger),
            },
            "quote": (
                {
                    "bid": bundle.quote.bid, "ask": bundle.quote.ask,
                    "spreadPct": round(bundle.quote.spread_pct, 5),
                    "source": bundle.quote.source,
                } if bundle.quote else None
            ),
            "feedErrors": bundle.errors,
        })

    return bp


__all__ = ["create_opus_blueprint"]
