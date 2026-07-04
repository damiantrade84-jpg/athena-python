"""Demo/paper-only inference gate (ASE v2.1 §13)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GateResult:
    ok: bool
    reason: str
    checks: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason, "checks": dict(self.checks)}


def assert_demo(
    *,
    executor_mode: str | None = None,
    mt5_trade_mode: int | None = None,
    bybit_base_url: str | None = None,
    config: dict[str, Any] | None = None,
) -> GateResult:
    cfg = config or {}
    if config is None and executor_mode is None:
        # Bare calls (inference path) previously checked an empty dict, whose
        # "paper" default made the gate pass regardless of the real executor
        # mode. Read the live CONFIG when available; research environments
        # without the app config keep the paper default.
        try:
            from config import CONFIG as _app_config

            cfg = _app_config
        except BaseException:  # config validation may sys.exit outside the app
            cfg = {}
    mode = executor_mode or str(cfg.get("EXECUTOR_MODE", "paper")).lower()
    ok_mode = mode in {"paper", "demo"}

    ok_mt5 = True
    if mt5_trade_mode is not None:
        ok_mt5 = int(mt5_trade_mode) == 0

    ok_bybit = True
    if bybit_base_url:
        ok_bybit = str(bybit_base_url).rstrip("/").endswith("api-testnet.bybit.com")

    checks = {"executor_mode": ok_mode, "mt5_demo": ok_mt5, "bybit_testnet": ok_bybit}
    ok = all(checks.values())
    if not ok_mode:
        reason = f"executor_mode={mode!r} not paper/demo"
    elif not ok_mt5:
        reason = "mt5 account is not demo"
    elif not ok_bybit:
        reason = "bybit base url is not testnet"
    else:
        reason = "ok"
    return GateResult(ok=ok, reason=reason, checks=checks)
