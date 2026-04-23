from types import SimpleNamespace

import mt5_executor


def test_mt5_position_fee_cost_sums_commission_and_fee():
    class _FakeMT5:
        @staticmethod
        def history_deals_get(*, position):
            assert position == 12345
            return [
                SimpleNamespace(commission=-1.25, fee=-0.10),
                SimpleNamespace(commission=-1.25, fee=0.0),
            ]

    result = mt5_executor._mt5_position_fee_cost(_FakeMT5(), 12345, retries=1, delay_s=0.0)

    assert result == 2.6


def test_mt5_total_fee_cost_deduplicates_position_tickets():
    class _FakeMT5:
        @staticmethod
        def history_deals_get(*, position):
            return [SimpleNamespace(commission=-0.5, fee=0.0)] if position == 11 else []

    result = mt5_executor._mt5_total_fee_cost(_FakeMT5(), [11, 11, None])

    assert result == 0.5


def test_mt5_position_fee_cost_clamps_retry_sleep(monkeypatch):
    sleeps = []

    class _FakeMT5:
        @staticmethod
        def history_deals_get(*, position):
            assert position == 77
            return []

    monkeypatch.setattr(mt5_executor.time, "sleep", lambda delay: sleeps.append(delay))

    result = mt5_executor._mt5_position_fee_cost(
        _FakeMT5(),
        77,
        retries=2,
        delay_s=300,
    )

    assert result is None
    assert sleeps == [5.0]
