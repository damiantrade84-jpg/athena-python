import etoro_executor


def test_signal_direction_to_is_buy_mapping():
    assert etoro_executor.signal_direction_to_is_buy("LONG") is True
    assert etoro_executor.signal_direction_to_is_buy("SHORT") is False


def test_signal_direction_to_is_buy_rejects_invalid():
    try:
        etoro_executor.signal_direction_to_is_buy("SIDEWAYS")
        assert False, "Expected ValueError for unsupported direction"
    except ValueError as exc:
        assert "Unsupported direction" in str(exc)


def test_build_market_open_payload_validates_inputs():
    payload = etoro_executor.build_market_open_by_amount_payload(
        instrument_id=1001,
        amount=250.0,
        is_buy=True,
    )
    assert payload["InstrumentId"] == 1001
    assert payload["Amount"] == 250.0
    assert payload["IsBuy"] is True
    assert payload["Leverage"] == 1

    for bad_instrument_id in (0, -1):
        try:
            etoro_executor.build_market_open_by_amount_payload(
                instrument_id=bad_instrument_id,
                amount=250.0,
                is_buy=True,
            )
            assert False, "Expected ValueError for instrument_id"
        except ValueError as exc:
            assert "instrument_id" in str(exc)

    for bad_amount in (0, -1):
        try:
            etoro_executor.build_market_open_by_amount_payload(
                instrument_id=1001,
                amount=bad_amount,
                is_buy=True,
            )
            assert False, "Expected ValueError for amount"
        except ValueError as exc:
            assert "amount" in str(exc)


def test_resolve_instrument_id_uses_exact_match(monkeypatch):
    def _fake_request_json(_method, _url, creds, timeout_sec=10.0):
        assert creds.api_key == "k"
        assert creds.user_key == "u"
        assert timeout_sec == 5
        return {
            "items": [
                {"internalSymbolFull": "AAP", "instrumentId": 99},
                {"internalSymbolFull": "AAPL", "instrumentId": 1001},
            ]
        }

    monkeypatch.setattr(etoro_executor, "_request_json", _fake_request_json)
    creds = etoro_executor.EtoroCredentials(api_key="k", user_key="u")

    assert (
        etoro_executor.resolve_instrument_id("AAPL", creds, timeout_sec=5) == 1001
    )


def test_resolve_instrument_id_raises_when_missing_exact_match(monkeypatch):
    monkeypatch.setattr(
        etoro_executor,
        "_request_json",
        lambda *_args, **_kwargs: {"items": [{"internalSymbolFull": "AAP"}]},
    )
    creds = etoro_executor.EtoroCredentials(api_key="k", user_key="u")

    try:
        etoro_executor.resolve_instrument_id("AAPL", creds)
        assert False, "Expected LookupError when exact symbol is absent"
    except LookupError as exc:
        assert "Instrument not found" in str(exc)

