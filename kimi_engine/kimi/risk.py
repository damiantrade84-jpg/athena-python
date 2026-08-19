"""Risk manager — hard, fail-closed. Sizing, exposure, daily loss, kill switch."""
from __future__ import annotations

import time
from dataclasses import dataclass

from .config import Config


@dataclass
class RiskVerdict:
    ok: bool
    reason: str = ""
    qty: float = 0.0


class RiskManager:
    def __init__(self) -> None:
        self.kill_switch = False
        self.day_stamp = self._today()
        self.day_start_equity = Config.START_EQUITY

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def roll_day(self, equity: float) -> None:
        today = self._today()
        if today != self.day_stamp:
            self.day_stamp = today
            self.day_start_equity = equity

    def daily_loss_pct(self, equity: float) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        return max(0.0, (self.day_start_equity - equity) / self.day_start_equity * 100.0)

    def approve(self, *, entry: float, sl: float, equity: float,
                open_positions: int) -> RiskVerdict:
        if self.kill_switch:
            return RiskVerdict(False, "kill switch engaged")
        risk = abs(entry - sl)
        if risk <= 0 or entry <= 0:
            return RiskVerdict(False, "invalid entry/SL geometry")
        if open_positions >= Config.MAX_POSITIONS:
            return RiskVerdict(False, f"max positions ({Config.MAX_POSITIONS}) reached")
        loss_pct = self.daily_loss_pct(equity)
        if loss_pct >= Config.MAX_DAILY_LOSS_PCT:
            return RiskVerdict(False, f"daily loss limit hit ({loss_pct:.2f}%)")
        risk_amt = equity * Config.RISK_PER_TRADE_PCT / 100.0
        qty = risk_amt / risk
        if qty <= 0:
            return RiskVerdict(False, "zero size")
        return RiskVerdict(True, qty=qty)
