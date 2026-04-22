import os
from pathlib import Path
import tempfile

import divergence_monitor as dm
import indicators
import scoring


def _candles() -> list[dict]:
    return [
        {
            "time": f"2026-04-22T{hour:02d}:00:00+00:00",
            "open": 1.0 + (hour * 0.01),
            "high": 1.1 + (hour * 0.01),
            "low": 0.9 + (hour * 0.01),
            "close": 1.0 + (hour * 0.01),
            "vol": 10 + hour,
        }
        for hour in range(1, 6)
    ]


def test_check_divergence_replays_live_style_and_context(monkeypatch):
    fd, audit_path = tempfile.mkstemp(
        suffix=".db",
        dir=Path(__file__).resolve().parents[1],
    )
    os.close(fd)
    audit_db = Path(audit_path)
    try:
        monkeypatch.setattr(dm, "_DB_PATH", str(audit_db))
        dm._init_tables()

        d1 = _candles()
        h4 = _candles()
        h1 = _candles()
        captured = {}

        def fake_calc_indicators_with_normalized(candles, _asset_type):
            return {"snap": {"close": candles[-1]["close"]}}

        def fake_calc_sma(values, _lookback):
            return [1.0 for _ in values]

        def fake_calc_stochastic(candles, *_args):
            src = "H1" if candles is h1 else "H4" if candles is h4 else "other"
            return {"k": [50.0], "d": [50.0], "src": src}

        def fake_calc_confluence(
            _d1i,
            _h4i,
            _h1i,
            _vr,
            stoch,
            _pair,
            _btc_bias,
            **kwargs,
        ):
            captured["stoch_src"] = stoch.get("src")
            captured["volume_threshold"] = kwargs.get("volume_threshold")
            captured["regime_context"] = kwargs.get("regime_context")
            captured["bar_time"] = kwargs.get("bar_time")
            return {"score": 1.5, "direction": "LONG", "regime": "TRENDING"}

        monkeypatch.setattr(indicators, "calc_indicators_with_normalized", fake_calc_indicators_with_normalized)
        monkeypatch.setattr(indicators, "calc_sma", fake_calc_sma)
        monkeypatch.setattr(indicators, "calc_stochastic", fake_calc_stochastic)
        monkeypatch.setattr(scoring, "calc_confluence", fake_calc_confluence)

        result = dm.check_divergence(
            {"display": "EUR/USD", "type": "forex"},
            {"confluenceScore": 1.5, "direction": "LONG", "regime": "TRENDING"},
            d1,
            h4,
            h1,
            style="intraday",
            volume_threshold=1.7,
            regime_context={"state": "TRENDING"},
        )

        assert captured["stoch_src"] == "H1"
        assert captured["volume_threshold"] == 1.7
        assert captured["regime_context"] == {"state": "TRENDING"}
        assert captured["bar_time"] is None
        assert result["severity"] == "ok"
        assert Path(audit_db).exists()
    finally:
        for extra in (audit_db, audit_db.with_suffix(".db-wal"), audit_db.with_suffix(".db-shm")):
            try:
                extra.unlink()
            except FileNotFoundError:
                pass
            except PermissionError:
                pass
