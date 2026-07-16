from __future__ import annotations

import datetime
import threading

import duka_volume


def test_duka_hour_fetch_sends_provider_accepted_headers(monkeypatch):
    captured = {}

    class _Response:
        status_code = 200
        content = b"duka"

    def _get(url: str, *, headers: dict, timeout: int):
        captured.update({"url": url, "headers": headers, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(duka_volume.requests, "get", _get)

    result = duka_volume._fetch_hour_raw("EURUSD", datetime.date(2026, 7, 15), 12)

    assert result == b"duka"
    assert captured["headers"]["User-Agent"] == "Athena-Sentinel/4.0"
    assert captured["headers"]["Accept"].startswith("application/octet-stream")
    assert captured["timeout"] == duka_volume._TIMEOUT_S


def test_force_refresh_schedules_duka_update_without_sync_fetch(monkeypatch):
    scheduled = []

    monkeypatch.setattr(
        duka_volume,
        "refresh_forex_volume_background",
        lambda display, days=60: scheduled.append((display, days)) or True,
    )
    monkeypatch.setattr(
        duka_volume,
        "get_forex_volume_bars",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        duka_volume,
        "fetch_and_cache",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("force refresh must not hydrate synchronously")
        ),
    )

    assert duka_volume.get_forex_vr("EUR/USD", force_refresh=True) == 1.0
    assert scheduled == [("EUR/USD", 60)]


def test_duka_background_refresh_is_single_flight(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def _blocking_fetch(_display: str, days: int = 90):
        assert days == 7
        started.set()
        release.wait(timeout=2.0)

    monkeypatch.setattr(duka_volume, "fetch_and_cache", _blocking_fetch)

    assert duka_volume.refresh_forex_volume_background("EUR/USD", days=7) is True
    assert started.wait(timeout=1.0)
    assert duka_volume.refresh_forex_volume_background("EUR/USD", days=7) is False

    release.set()
