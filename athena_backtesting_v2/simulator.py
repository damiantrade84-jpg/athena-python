from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .models import CostModel, TradeRecord


@dataclass(frozen=True)
class SignalIntent:
    engine: str
    symbol: str
    asset_type: str
    style: str
    direction: str
    decision_time: datetime
    valid_until: datetime | None
    reference_price: float
    stop_price: float
    targets: tuple[float, ...]
    exit_policy: str
    order_type: str = "market"
    limit_price: float | None = None
    trailing_atr: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _utc(value: Any) -> datetime:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return parsed.to_pydatetime()


def _adverse_price(price: float, direction: str, bps: float, *, is_entry: bool) -> float:
    sign = 1.0 if (direction == "LONG") == is_entry else -1.0
    return float(price) * (1.0 + sign * float(bps) / 10_000.0)


def _r(direction: str, entry: float, exit_price: float, risk: float) -> float:
    signed = exit_price - entry if direction == "LONG" else entry - exit_price
    return signed / risk


class EventDrivenSimulator:
    """Conservative OHLC execution simulator owned exclusively by v2."""

    def __init__(self, cost_model: CostModel, *, max_hold_bars: int):
        self.cost_model = cost_model
        self.max_hold_bars = int(max_hold_bars)

    def _find_fill(self, intent: SignalIntent, bars: pd.DataFrame, start_index: int) -> tuple[int, float] | None:
        for index in range(start_index, len(bars)):
            row = bars.iloc[index]
            bar_time = _utc(row["time"])
            if intent.valid_until and bar_time > intent.valid_until:
                return None
            if intent.order_type == "limit":
                limit = float(intent.limit_price if intent.limit_price is not None else intent.reference_price)
                if float(row["low"]) <= limit <= float(row["high"]):
                    return index, limit
                continue
            return index, _adverse_price(float(row["open"]), intent.direction, self.cost_model.slippage_bps, is_entry=True)
        return None

    def simulate(self, run_id: str, intents: list[SignalIntent], execution_bars: pd.DataFrame) -> tuple[list[TradeRecord], dict[str, Any]]:
        bars = execution_bars.copy()
        bars["time"] = pd.to_datetime(bars["time"], utc=True)
        bars = bars.sort_values("time", kind="mergesort").reset_index(drop=True)
        opens = [value.value for value in bars["time"]]
        opens_index = pd.Index(opens)
        trades: list[TradeRecord] = []
        next_available_ns = -1
        ambiguity_count = 0
        expired_orders = 0
        invalid_intents = 0
        for intent in sorted(intents, key=lambda row: row.decision_time):
            decision_ns = pd.Timestamp(intent.decision_time).value
            if decision_ns < next_available_ns:
                continue
            direction = str(intent.direction).upper()
            if direction not in {"LONG", "SHORT"} or not intent.targets:
                invalid_intents += 1
                continue
            risk_reference = abs(float(intent.reference_price) - float(intent.stop_price))
            if risk_reference <= 0:
                invalid_intents += 1
                continue
            start_index = int(opens_index.searchsorted(decision_ns, side="left"))
            fill = self._find_fill(intent, bars, start_index)
            if fill is None:
                expired_orders += 1
                continue
            fill_index, entry_price = fill
            stop = float(intent.stop_price)
            risk = abs(entry_price - stop)
            if risk <= 0 or (direction == "LONG" and stop >= entry_price) or (direction == "SHORT" and stop <= entry_price):
                invalid_intents += 1
                continue
            targets = sorted((float(value) for value in intent.targets), reverse=direction == "SHORT")
            targets = [value for value in targets if (value > entry_price if direction == "LONG" else value < entry_price)]
            if not targets:
                invalid_intents += 1
                continue
            scale_out = str(intent.exit_policy).upper() in {"SPLIT_50_50", "SCALE_OUT"} and len(targets) >= 2
            weights = [0.5, 0.5] if scale_out else [1.0]
            active_targets = targets[:2] if scale_out else targets[:1]
            realized_gross_r = 0.0
            remaining = 1.0
            exit_price = entry_price
            exit_index = fill_index
            exit_reason = "MAX_HOLD"
            ambiguous = False
            trailing_stop = stop
            tp1_hit = False
            max_exit_index = min(len(bars) - 1, fill_index + self.max_hold_bars)
            for index in range(fill_index, max_exit_index + 1):
                row = bars.iloc[index]
                open_price = float(row["open"])
                high = float(row["high"])
                low = float(row["low"])
                stop_hit = low <= trailing_stop if direction == "LONG" else high >= trailing_stop
                target_hits = [high >= target if direction == "LONG" else low <= target for target in active_targets]
                any_target = any(target_hits)
                if stop_hit and any_target:
                    ambiguous = True
                    ambiguity_count += 1
                # Conservative ordering: stop is always resolved before a target
                # when OHLC cannot identify the intrabar path.
                if stop_hit:
                    raw_stop_fill = min(open_price, trailing_stop) if direction == "LONG" else max(open_price, trailing_stop)
                    stop_fill = _adverse_price(raw_stop_fill, direction, self.cost_model.slippage_bps, is_entry=False)
                    realized_gross_r += remaining * _r(direction, entry_price, stop_fill, risk)
                    exit_price = stop_fill
                    exit_index = index
                    exit_reason = "STOP"
                    remaining = 0.0
                    break
                if target_hits and target_hits[0]:
                    target = active_targets[0]
                    weight = min(remaining, weights[0])
                    target_fill = _adverse_price(target, direction, self.cost_model.slippage_bps, is_entry=False)
                    realized_gross_r += weight * _r(direction, entry_price, target_fill, risk)
                    remaining -= weight
                    exit_price = target_fill
                    exit_index = index
                    if scale_out and not tp1_hit and remaining > 0:
                        tp1_hit = True
                        active_targets = active_targets[1:]
                        weights = [remaining]
                        trailing_stop = entry_price
                        exit_reason = "TP1_SCALE_OUT"
                    else:
                        exit_reason = "TARGET"
                        remaining = 0.0
                        break
                if remaining > 0 and intent.trailing_atr and float(intent.trailing_atr) > 0:
                    if direction == "LONG":
                        trailing_stop = max(trailing_stop, high - float(intent.trailing_atr))
                    else:
                        trailing_stop = min(trailing_stop, low + float(intent.trailing_atr))
            if remaining > 0:
                row = bars.iloc[max_exit_index]
                timed_fill = _adverse_price(float(row["close"]), direction, self.cost_model.slippage_bps, is_entry=False)
                realized_gross_r += remaining * _r(direction, entry_price, timed_fill, risk)
                exit_price = timed_fill
                exit_index = max_exit_index
                exit_reason = "MAX_HOLD" if not tp1_hit else "TP1_THEN_MAX_HOLD"
            entry_time = _utc(bars.iloc[fill_index]["time"])
            exit_time = _utc(bars.iloc[exit_index]["time"])
            days = max(0.0, (exit_time - entry_time).total_seconds() / 86_400.0)
            spread_r = entry_price * self.cost_model.spread_bps / 10_000.0 / risk
            commission_r = entry_price * (2.0 * self.cost_model.commission_bps_per_side) / 10_000.0 / risk
            carry_bps = 0.0
            if intent.asset_type.lower() == "crypto":
                carry_bps = self.cost_model.funding_bps_per_day * days
            elif intent.asset_type.lower() == "forex":
                carry_bps = self.cost_model.swap_bps_per_day * days
            elif intent.asset_type.lower() == "commodity":
                carry_bps = self.cost_model.roll_bps
            carry_r = entry_price * carry_bps / 10_000.0 / risk
            # Slippage is embedded in fill prices and reported separately as an
            # assumption, not double-charged here.
            total_cost_r = spread_r + commission_r + carry_r
            net_r = realized_gross_r - total_cost_r
            trade = TradeRecord(
                run_id=run_id,
                engine=intent.engine,
                symbol=intent.symbol,
                direction=direction,
                style=intent.style,
                signal_time=intent.decision_time.isoformat(),
                order_time=intent.decision_time.isoformat(),
                entry_time=entry_time.isoformat(),
                exit_time=exit_time.isoformat(),
                entry_price=round(entry_price, 10),
                exit_price=round(exit_price, 10),
                stop_price=round(stop, 10),
                targets=targets,
                gross_r=round(realized_gross_r, 8),
                cost_r=round(total_cost_r, 8),
                net_r=round(net_r, 8),
                exit_reason=exit_reason,
                ambiguous_same_bar=ambiguous,
                duration_bars=max(1, exit_index - fill_index + 1),
                decision=intent.metadata,
                costs={
                    "spreadR": round(spread_r, 8),
                    "commissionR": round(commission_r, 8),
                    "carryR": round(carry_r, 8),
                    "slippageBpsPerFill": float(self.cost_model.slippage_bps),
                    "dividendMode": self.cost_model.dividend_mode,
                    "rollBpsPerTrade": float(self.cost_model.roll_bps),
                    "source": self.cost_model.source,
                },
            )
            trades.append(trade)
            next_available_ns = pd.Timestamp(exit_time).value
        diagnostics = {
            "signalIntentCount": len(intents),
            "tradeCount": len(trades),
            "expiredOrderCount": expired_orders,
            "invalidIntentCount": invalid_intents,
            "sameBarAmbiguityCount": ambiguity_count,
            "sameBarAmbiguityRate": ambiguity_count / len(trades) if trades else 0.0,
            "sameBarPolicy": "ADVERSE_STOP_FIRST",
        }
        return trades, diagnostics
