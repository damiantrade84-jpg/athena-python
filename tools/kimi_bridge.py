"""Kimi Code ↔ Athena HTTP Bridge.

Exposes Athena internals as REST endpoints that Kimi Code (or any agent)
can call via requests/httpx.

Usage in athena.py:
    from tools.kimi_bridge import register_kimi_routes
    register_kimi_routes(app)

Endpoints:
    GET  /api/kimi/health              → Server status
    POST /api/kimi/audit/query         → Read-only SQL on audit.db
    GET  /api/kimi/config/read         → Current config.yaml as JSON
    POST /api/kimi/config/update       → Safe config update (auto-backup)
    POST /api/kimi/backtest/run        → Trigger backtest
    POST /api/kimi/tests/run           → Run pytest suite
    GET  /api/kimi/signals/latest      → Latest signals
    GET  /api/kimi/trades/performance  → Aggregate trade stats
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

PROJECT_ROOT = Path(__file__).parent.parent
DB_AUDIT = PROJECT_ROOT / "audit.db"
DB_MICRO = PROJECT_ROOT / "microstructure.db"
DB_CANDLE = PROJECT_ROOT / "candle_cache.db"
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

kimi_bp = Blueprint("kimi", __name__, url_prefix="/api/kimi")


def _jsonify_row(rows: list[sqlite3.Row]) -> dict:
    if not rows:
        return {"columns": [], "rows": [], "count": 0}
    return {
        "columns": list(rows[0].keys()),
        "rows": [dict(r) for r in rows],
        "count": len(rows),
    }


@kimi_bp.route("/last", methods=["GET"])
def conductor_last():
    """Return the last Conductor decision for the dashboard widget."""
    try:
        from conductor import _LAST_CONDUCTOR_RESULT, _conductor_state_lock

        with _conductor_state_lock:
            last = _LAST_CONDUCTOR_RESULT
        if last:
            return jsonify({
                "conductor": last.get("routing", {}),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        return jsonify({"conductor": None, "message": "No conductor data yet"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Mirror route at /api/conductor/last for the dashboard widget
@kimi_bp.route("/conductor/last", methods=["GET"])
def conductor_last_alias():
    """Alias of /last — the dashboard widget calls /api/conductor/last."""
    return conductor_last()


@kimi_bp.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project": "athena-python",
        "version": "4.0",
    })


@kimi_bp.route("/audit/query", methods=["POST"])
def audit_query():
    """Execute read-only SQL on audit.db."""
    data = request.get_json() or {}
    sql = data.get("sql", "").strip()
    limit = min(data.get("limit", 100), 5000)

    if not sql:
        return jsonify({"error": "No SQL provided"}), 400

    # Safety: only SELECT allowed
    first_word = sql.split()[0].lower()
    if first_word not in ("select", "with", "pragma"):
        return jsonify({"error": f"Only SELECT/PRAGMA queries allowed (got: {first_word})"}), 403

    try:
        with sqlite3.connect(DB_AUDIT, timeout=10.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(f"{sql} LIMIT ?", (limit,)).fetchall()
            return jsonify(_jsonify_row(rows))
    except Exception as e:
        return jsonify({"error": str(e), "sql": sql}), 500


@kimi_bp.route("/micro/query", methods=["POST"])
def micro_query():
    """Execute read-only SQL on microstructure.db."""
    data = request.get_json() or {}
    sql = data.get("sql", "").strip()
    limit = min(data.get("limit", 100), 5000)

    if not sql:
        return jsonify({"error": "No SQL provided"}), 400

    first_word = sql.split()[0].lower()
    if first_word not in ("select", "with", "pragma"):
        return jsonify({"error": f"Only SELECT/PRAGMA queries allowed"}), 403

    try:
        with sqlite3.connect(DB_MICRO, timeout=10.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(f"{sql} LIMIT ?", (limit,)).fetchall()
            return jsonify(_jsonify_row(rows))
    except Exception as e:
        return jsonify({"error": str(e), "sql": sql}), 500


@kimi_bp.route("/config/read", methods=["GET"])
def config_read():
    """Return current config.yaml as structured data."""
    try:
        import yaml
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return jsonify(yaml.safe_load(f))
    except ImportError:
        return jsonify({"error": "PyYAML not installed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@kimi_bp.route("/config/update", methods=["POST"])
def config_update():
    """Update a single config key (safe: backups old config)."""
    try:
        import yaml
    except ImportError:
        return jsonify({"error": "PyYAML not installed"}), 500

    data = request.get_json() or {}
    key_path = data.get("key", "").strip()
    value = data.get("value")

    if not key_path or value is None:
        return jsonify({"error": "Need 'key' and 'value'"}), 400

    if not CONFIG_PATH.exists():
        return jsonify({"error": f"config.yaml not found at {CONFIG_PATH}"}), 404

    # Backup
    backup_name = f"config.yaml.bak.{datetime.now():%Y%m%d_%H%M%S}"
    backup_path = CONFIG_PATH.with_name(backup_name)

    try:
        import shutil
        shutil.copy2(CONFIG_PATH, backup_path)

        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # Navigate key path (e.g., "RISK_PCT" or "SCALP_ENGINE.MIN_RR")
        keys = key_path.split(".")
        target = config
        for k in keys[:-1]:
            if k not in target or not isinstance(target[k], dict):
                target[k] = {}
            target = target[k]

        # Preserve type where possible
        old_value = target.get(keys[-1])
        if old_value is not None:
            try:
                if isinstance(old_value, bool):
                    value = bool(value)
                elif isinstance(old_value, int):
                    value = int(value)
                elif isinstance(old_value, float):
                    value = float(value)
            except (ValueError, TypeError):
                pass

        target[keys[-1]] = value

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, sort_keys=False, default_flow_style=False, allow_unicode=True)

        return jsonify({
            "status": "updated",
            "key": key_path,
            "old_value": old_value,
            "new_value": value,
            "backup": str(backup_path),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@kimi_bp.route("/tests/run", methods=["POST"])
def tests_run():
    """Run pytest via the Kimi test runner and return structured results."""
    data = request.get_json() or {}
    engine = data.get("engine", "")
    pattern = data.get("pattern", "")
    coverage = data.get("coverage", False)
    parallel = data.get("parallel", False)

    cmd = [sys.executable, "tools/run_kimi_tests.py"]
    if engine:
        cmd += ["--engine", engine]
    elif pattern:
        cmd += ["--pattern", pattern]
    else:
        cmd += ["--all"]
    if coverage:
        cmd.append("--coverage")
    if parallel:
        cmd.append("--parallel")
    cmd += ["--json", "--timeout", str(data.get("timeout", 600))]

    try:
        result = subprocess.run(
            cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=data.get("timeout", 600) + 30
        )
        # Parse JSON output
        try:
            test_result = json.loads(result.stdout.split("\n")[-1]) if result.stdout else {}
        except json.JSONDecodeError:
            test_result = {
                "raw_stdout": result.stdout[-4000:],
                "raw_stderr": result.stderr[-2000:],
                "parse_error": True,
            }

        return jsonify({
            "passed": result.returncode == 0,
            "returncode": result.returncode,
            "result": test_result,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Tests timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@kimi_bp.route("/signals/latest", methods=["GET"])
def signals_latest():
    """Return latest signals from audit.db."""
    limit = min(request.args.get("limit", 20, type=int), 500)
    engine = request.args.get("engine", "").upper()

    try:
        with sqlite3.connect(DB_AUDIT, timeout=10.0) as con:
            con.row_factory = sqlite3.Row
            sql = "SELECT * FROM signals WHERE 1=1"
            params = []
            if engine:
                sql += " AND engine = ?"
                params.append(engine)
            sql += " ORDER BY ts DESC LIMIT ?"
            params.append(limit)
            rows = con.execute(sql, params).fetchall()
            return jsonify({"signals": [dict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@kimi_bp.route("/trades/performance", methods=["GET"])
def trades_performance():
    """Return aggregate trade performance."""
    days = min(request.args.get("days", 30, type=int), 365)
    pair = request.args.get("pair", "")

    try:
        with sqlite3.connect(DB_AUDIT, timeout=10.0) as con:
            sql = """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN pnl = 0 THEN 1 ELSE 0 END) as breakeven,
                    SUM(pnl) as net_pnl,
                    AVG(pnl) as avg_pnl,
                    AVG(r_multiple) as avg_r,
                    MAX(pnl) as best_trade,
                    MIN(pnl) as worst_trade
                FROM trades
                WHERE ts > datetime('now', '-' || ? || ' days')
            """
            params = [days]
            if pair:
                sql += " AND pair = ?"
                params.append(pair)
            row = con.execute(sql, params).fetchone()
            total = row[0] or 0
            return jsonify({
                "period_days": days,
                "pair_filter": pair or "all",
                "total_trades": total,
                "wins": row[1] or 0,
                "losses": row[2] or 0,
                "breakeven": row[3] or 0,
                "win_rate": round(row[1] / total * 100, 2) if total else 0,
                "net_pnl": row[4],
                "avg_pnl": row[5],
                "avg_r": row[6],
                "best_trade": row[7],
                "worst_trade": row[8],
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@kimi_bp.route("/trades/drawdown", methods=["GET"])
def trades_drawdown():
    """Return running drawdown analysis."""
    days = min(request.args.get("days", 90, type=int), 365)

    try:
        with sqlite3.connect(DB_AUDIT, timeout=10.0) as con:
            rows = con.execute(
                "SELECT ts, pnl, r_multiple FROM trades WHERE ts > datetime('now', '-' || ? || ' days') ORDER BY ts",
                (days,),
            ).fetchall()

        if not rows:
            return jsonify({"max_drawdown_pct": 0, "current_drawdown_pct": 0, "peak_equity": 0})

        equity = 0
        peak = 0
        max_dd = 0
        for _, pnl, _ in rows:
            equity += pnl or 0
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak else 0
            if dd > max_dd:
                max_dd = dd

        current_dd = (peak - equity) / peak * 100 if peak else 0
        return jsonify({
            "period_days": days,
            "trade_count": len(rows),
            "peak_equity": round(peak, 2),
            "current_equity": round(equity, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "current_drawdown_pct": round(current_dd, 2),
            "drawdown_status": "NORMAL" if current_dd < 10 else "REDUCED" if current_dd < 15 else "STOPPED",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def register_kimi_routes(app):
    app.register_blueprint(kimi_bp)
    # Dashboard widget calls /api/conductor/last — mirror it at root
    app.add_url_rule("/api/conductor/last", "conductor_last_mirror", conductor_last, methods=["GET"])
    print("[KIMI] Bridge routes registered at /api/kimi/*  (+ /api/conductor/last mirror)")
    # KIMI Engine (intraday quant terminal) — never break host startup
    try:
        register_kimi_engine(app)
    except Exception as exc:  # noqa: BLE001
        print(f"[KIMI] Engine routes unavailable: {exc}")


# ════════════════════════════════════════════════════════════════════════
# KIMI Engine — intraday quant engine embedded into Athena.
# Serves the terminal UI at /kimi/ and its API at /kimi/api/*.
# The engine code lives in <repo>/kimi_engine and is imported lazily.
# ════════════════════════════════════════════════════════════════════════

import threading
import time as _time

from flask import Response, send_from_directory, stream_with_context

KIMI_ENGINE_DIR = PROJECT_ROOT / "kimi_engine"
KIMI_WEB_DIR = KIMI_ENGINE_DIR / "web"

_engine = None
_engine_lock = threading.Lock()


def _get_engine():
    """Lazy singleton; starts the scan loop on first use. Never raises."""
    global _engine
    with _engine_lock:
        if _engine is None:
            if str(KIMI_ENGINE_DIR) not in sys.path:
                sys.path.insert(0, str(KIMI_ENGINE_DIR))
            from kimi.engine import Engine  # noqa: PLC0415
            _engine = Engine()
            _engine.start()
    return _engine


kimi_engine_bp = Blueprint("kimi_engine", __name__, url_prefix="/kimi")


@kimi_engine_bp.route("/", methods=["GET"])
def engine_home():
    html = (KIMI_WEB_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace('window.KIMI_BASE = window.KIMI_BASE || "";',
                        'window.KIMI_BASE = "/kimi";')
    return Response(html, mimetype="text/html")


@kimi_engine_bp.route("", methods=["GET"])
def engine_home_redirect():
    from flask import redirect
    return redirect("/kimi/", code=302)


@kimi_engine_bp.route("/<path:asset>", methods=["GET"])
def engine_assets(asset):
    if asset in ("app.js", "styles.css"):
        return send_from_directory(KIMI_WEB_DIR, asset)
    return jsonify({"error": "not found"}), 404


@kimi_engine_bp.route("/api/state", methods=["GET"])
def engine_state():
    return jsonify(_get_engine().snapshot())


@kimi_engine_bp.route("/api/chart", methods=["GET"])
def engine_chart():
    symbol = (request.args.get("symbol") or "BTCUSDT").upper()
    tf = request.args.get("tf") or "15m"
    try:
        return jsonify(_get_engine().chart(symbol, tf))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502


@kimi_engine_bp.route("/api/stream", methods=["GET"])
def engine_stream():
    eng = _get_engine()

    @stream_with_context
    def gen():
        for _ in range(1200):  # ~1h, client reconnects
            try:
                yield f"data: {json.dumps(eng.snapshot())}\n\n"
            except Exception:  # noqa: BLE001
                yield 'data: {"error":"snapshot"}\n\n'
            _time.sleep(3)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@kimi_engine_bp.route("/api/execute", methods=["POST"])
def engine_execute():
    data = request.get_json(silent=True) or {}
    return jsonify(_get_engine().execute(str(data.get("id", "")),
                                         str(data.get("venue", "auto"))))


@kimi_engine_bp.route("/api/close", methods=["POST"])
def engine_close():
    data = request.get_json(silent=True) or {}
    return jsonify(_get_engine().close_position(str(data.get("id", ""))))


@kimi_engine_bp.route("/api/kill", methods=["POST"])
def engine_kill():
    data = request.get_json(silent=True) or {}
    return jsonify(_get_engine().set_kill(bool(data.get("on"))))


@kimi_engine_bp.route("/api/config", methods=["POST"])
def engine_config():
    data = request.get_json(silent=True) or {}
    eng = _get_engine()
    if "minScore" in data:
        try:
            eng.min_score = float(data["minScore"])
        except (TypeError, ValueError):
            pass
    if "autoTrade" in data:
        eng.auto_trade = bool(data["autoTrade"])
    return jsonify({"ok": True, "minScore": eng.min_score, "autoTrade": eng.auto_trade})


def _portfolio_symbols() -> list[str]:
    """Athena's currently enabled pairs, in KIMI's naming scheme.

    Lets the picker offer "scan the whole portfolio" instead of ticking a
    hundred boxes by hand. Two filters are deliberate rather than incidental:

      * share CFDs are dropped, because KIMI's MT5 resolver skips '#'-prefixed
        equity symbols by design - including them would add a hundred symbols
        that error on every scan;
      * anything whose broker symbol is not plain alphanumeric is dropped,
        because set_symbols() rejects it anyway. Catalog ids like 'AAPL.US'
        and 'GC=F' fail this test, which is the point - they are not feed
        symbols.
    """
    import re

    try:
        import athena  # noqa: PLC0415 - the host module owns the pair list
        pairs = list(getattr(athena, "ACTIVE_PAIRS", []) or [])
    except Exception:  # noqa: BLE001 - no host, no portfolio; not an error
        return []

    try:
        from mt5_executor import mt5_map_symbol  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        mt5_map_symbol = None  # type: ignore[assignment]

    scannable = {"forex", "commodity", "index", "crypto"}
    out: list[str] = []
    for pair in pairs:
        if not isinstance(pair, dict) or not pair.get("enabled", True):
            continue
        if str(pair.get("type") or "").strip().lower() not in scannable:
            continue
        display = str(pair.get("display") or "").strip()
        if not display:
            continue
        candidate = ""
        if mt5_map_symbol is not None and str(pair.get("source")) == "mt5":
            try:
                candidate = str(mt5_map_symbol(display) or "").strip()
            except Exception:  # noqa: BLE001
                candidate = ""
        if not candidate:
            candidate = display.replace("/", "").replace(" ", "").replace("-", "")
        candidate = candidate.upper()
        if re.fullmatch(r"[A-Z0-9]{4,20}", candidate) and candidate not in out:
            out.append(candidate)
    return out


@kimi_engine_bp.route("/api/symbols", methods=["GET"])
def engine_symbols_get():
    eng = _get_engine()
    from kimi.config import Config  # noqa: PLC0415

    return jsonify({"active": list(eng.symbols),
                    "available": eng.feed.available_symbols(),
                    "portfolio": _portfolio_symbols(),
                    "maxSymbols": int(Config.MAX_SYMBOLS)})


@kimi_engine_bp.route("/api/symbols", methods=["POST"])
def engine_symbols_set():
    data = request.get_json(silent=True) or {}
    return jsonify(_get_engine().set_symbols(data.get("symbols", [])))


@kimi_engine_bp.route("/api/scan", methods=["POST"])
def engine_scan_now():
    return jsonify(_get_engine().scan_now())


def register_kimi_engine(app):
    """Register the KIMI Engine blueprint and warm the scan loop."""
    if "kimi_engine" not in app.blueprints:
        app.register_blueprint(kimi_engine_bp)
        print("[KIMI] Engine terminal at /kimi/  (api: /kimi/api/*)")
    # Warm the engine so the panel opens on live data.
    threading.Thread(target=_get_engine, daemon=True, name="kimi-engine-boot").start()
