from indicators import calc_usd_relative_strength_context


def _candles(closes):
    return [{"close": close} for close in closes]


def test_calc_usd_relative_strength_context_asset_stronger_inverse():
    ctx = calc_usd_relative_strength_context(
        _candles([2300 + i * 4 for i in range(40)]),
        _candles([106 - i * 0.08 for i in range(40)]),
        asset_label="XAU",
        proxy_label="DXY",
        tf="H4",
    )

    assert ctx is not None
    assert ctx["bias"] == "asset_stronger"
    assert ctx["relationship"] == "inverse"
    assert ctx["assetChangePct"] > 0
    assert ctx["usdProxyChangePct"] < 0
    assert ctx["spreadPct"] > 0
    assert ctx["score"] > 0
    assert "XAU stronger" in ctx["summary"]


def test_calc_usd_relative_strength_context_usd_stronger_inverse():
    ctx = calc_usd_relative_strength_context(
        _candles([2440 - i * 3 for i in range(40)]),
        _candles([100 + i * 0.12 for i in range(40)]),
        asset_label="XAU",
        proxy_label="DXY",
        tf="H4",
    )

    assert ctx is not None
    assert ctx["bias"] == "usd_stronger"
    assert ctx["relationship"] == "inverse"
    assert ctx["assetChangePct"] < 0
    assert ctx["usdProxyChangePct"] > 0
    assert ctx["spreadPct"] < 0
    assert ctx["score"] < 0
    assert "USD stronger" in ctx["summary"]


def test_calc_usd_relative_strength_context_requires_min_history():
    ctx = calc_usd_relative_strength_context(
        _candles([1, 2, 3, 4, 5]),
        _candles([5, 4, 3, 2, 1]),
        asset_label="XAU",
        proxy_label="DXY",
        tf="H4",
    )

    assert ctx is None
