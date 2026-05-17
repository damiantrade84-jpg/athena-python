"""Read-only audit log API route handlers and registration."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from flask import jsonify, request


_RUNTIME = SimpleNamespace()


def api_audit():
    """Return last N audit log entries from SQLite."""
    limit = min(int(request.args.get("limit", 50)), 500)

    try:
        con = sqlite3.connect(_RUNTIME.AUDIT_DB, timeout=15.0)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        con.close()
        return jsonify([dict(r) for r in rows])

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def register_audit_routes(app, runtime: SimpleNamespace) -> None:
    """Register read-only audit routes using athena.py runtime state."""
    global _RUNTIME

    _RUNTIME = runtime

    app.add_url_rule("/api/audit", "api_audit", api_audit)
