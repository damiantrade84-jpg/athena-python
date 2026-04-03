import base64
from datetime import datetime, timedelta, timezone

import pandas as pd

from chart_renderer import detect_recent_patterns, render_chart_image


def _candles(n: int = 120) -> list[dict]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    out = []
    price = 100.0
    for i in range(n):
        t = start + timedelta(hours=i)
        o = price
        c = price + (0.4 if i % 2 == 0 else -0.2)
        h = max(o, c) + 0.5
        l = min(o, c) - 0.4
        v = 1000 + i * 3
        out.append({"t": t.isoformat(), "o": o, "h": h, "l": l, "c": c, "v": v})
        price = c
    return out


def test_render_chart_image_engine_b_overlays_smoke():
    image_b64 = render_chart_image(
        candles=_candles(140),
        entry=101.0,
        sl=99.5,
        tp=104.0,
        title="TEST",
        bars_window=80,
        dpi=200,
        engine_b={
            "nearest_support_zone": {"lower": 99.6, "upper": 100.1},
            "nearest_resistance_zone": {"lower": 103.4, "upper": 103.9},
            "active_fvgs": [{"type": "bull", "bottom": 100.8, "top": 101.1, "mitigated": False}],
            "order_blocks": [{"type": "bull_ob", "lower": 99.8, "upper": 100.2}],
            "bos_data": {"bos_bull": True, "level": 101.7},
            "choch_data": {"choch_bear": True, "level": 100.4},
        },
    )
    raw = base64.b64decode(image_b64)
    assert len(raw) > 1000
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_detect_recent_patterns_finds_known_shapes():
    idx = pd.date_range("2026-01-01", periods=6, freq="h", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [10.0, 9.8, 9.7, 9.4, 9.2, 9.9],
            "High": [10.2, 10.0, 9.9, 9.5, 10.3, 10.4],
            "Low": [9.7, 9.6, 9.1, 9.0, 9.1, 9.7],
            "Close": [9.8, 9.7, 9.4, 9.2, 10.2, 10.0],
            "Volume": [100, 120, 130, 140, 160, 170],
        },
        index=idx,
    )
    patterns = detect_recent_patterns(df, lookback=6)
    labels = {p["label"] for p in patterns}
    assert "Hammer" in labels or "Bull Engulf" in labels

