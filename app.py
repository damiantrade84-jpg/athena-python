"""Application composition entrypoint.

Provides a thin app factory around the legacy Flask app so route modules can be
composed from separated concerns while preserving runtime compatibility.
"""

from __future__ import annotations

from flask import Flask

from athena_legacy import load as _load_legacy
import execution


def create_app() -> Flask:
    """Return the configured Flask application instance."""
    _athena = _load_legacy()
    _athena.ensure_runtime_services_started()

    app = _athena.app

    # Optional modular health route (idempotent registration guard).
    endpoint = "execution_healthcheck"
    if endpoint not in app.view_functions:
        app.add_url_rule("/healthz", endpoint, execution.healthcheck, methods=["GET"])

    return app


app = create_app()

