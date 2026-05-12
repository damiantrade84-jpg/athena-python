import logging

import pytest

from athena.datafeeds import ws_ssl


def test_default_ssl_context_verifies_certificates_by_default(monkeypatch):
    monkeypatch.delenv("ATHENA_MARKET_DATA_WS_SSL_VERIFY", raising=False)
    monkeypatch.setattr(ws_ssl, "configure_process_ca_bundle", lambda: None)
    monkeypatch.setattr(
        ws_ssl.ssl,
        "create_default_context",
        lambda cafile=None: {"verified": True, "cafile": cafile},
    )
    monkeypatch.setattr(
        ws_ssl.ssl,
        "_create_unverified_context",
        lambda: pytest.fail("unverified TLS context should not be used by default"),
    )

    assert ws_ssl.default_ssl_context() == {"verified": True, "cafile": None}


def test_default_ssl_context_allows_explicit_diagnostic_disable(monkeypatch, caplog):
    monkeypatch.setenv("ATHENA_MARKET_DATA_WS_SSL_VERIFY", "false")
    monkeypatch.setattr(ws_ssl, "_warned_unverified", False)
    monkeypatch.setattr(
        ws_ssl.ssl,
        "_create_unverified_context",
        lambda: {"verified": False},
    )
    monkeypatch.setattr(
        ws_ssl.ssl,
        "create_default_context",
        lambda cafile=None: pytest.fail("verified TLS context should not be used"),
    )
    caplog.set_level(logging.WARNING, logger="sentinel")

    assert ws_ssl.default_ssl_context() == {"verified": False}
    assert ws_ssl.default_ssl_context() == {"verified": False}

    assert caplog.text.count("certificate verification is disabled") == 1
