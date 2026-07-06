"""Legacy FX Factor validation.pass recompute on read."""

from __future__ import annotations

from flask import Flask

from athena_app.api.routes_forex_factor import register_forex_factor_routes


def test_validation_latest_enriches_legacy_pass_flag(monkeypatch) -> None:
    import athena_app.api.routes_forex_factor as routes

    monkeypatch.setattr(
        routes,
        "_latest_completed_backtest",
        lambda: {
            "run_id": "legacy-run",
            "status": "completed",
            "report": {
                "summary": {"trade_count": 110},
                "validation": {
                    "family_trade_counts": {"carry": 51, "momentum": 88, "value": 84},
                    "n_warning": False,
                },
            },
        },
    )

    app = Flask(__name__)
    register_forex_factor_routes(app)
    body = app.test_client().get("/api/forex/validation/latest").get_json()

    assert body["success"] is True
    assert body["data"]["validation"]["pass"] is True
    assert body["data"]["validation"]["family_trade_counts"]["momentum"] == 88
