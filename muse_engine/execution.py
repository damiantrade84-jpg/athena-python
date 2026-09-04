"""Fail-closed quote attestation, risk sizing, and broker coordination for MUSE."""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Protocol

from .config import MuseConfig
from .models import Quote, parse_iso
from .persistence import MuseRepository
from .scoring import REQUIRED_GATE_NAMES
from .sessions import market_is_closed, tide_state


class MuseExecutionError(RuntimeError):
    def __init__(self, code: str, *, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class BrokerGateway(Protocol):
    def quote(self, signal: dict[str, Any]) -> Quote: ...
    def account(self, venue: str) -> dict[str, Any]: ...
    def positions(self, venue: str) -> dict[str, Any]: ...
    def symbol_info(self, signal: dict[str, Any]) -> dict[str, Any]: ...
    def execute(self, venue: str, payload: dict[str, Any], approval: Any) -> dict[str, Any]: ...


def _asset_limit(mapping: dict[str, Any], asset_type: str) -> float:
    value = mapping.get(asset_type, mapping.get("default"))
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise MuseExecutionError("EXECUTION_LIMIT_INVALID", detail=asset_type) from exc
    if not math.isfinite(result) or result <= 0:
        raise MuseExecutionError("EXECUTION_LIMIT_INVALID", detail=asset_type)
    return result


class MuseExecutionCoordinator:
    def __init__(self, *, config: MuseConfig, repository: MuseRepository, gateway: BrokerGateway,
                 root_config: dict[str, Any], kill_switch_fn, now_fn) -> None:
        self.config = config
        self.repository = repository
        self.gateway = gateway
        self.root_config = root_config
        self.kill_switch_fn = kill_switch_fn
        self.now_fn = now_fn

    def capabilities(self) -> dict[str, Any]:
        execution = self.config.execution
        global_mode = str(self.root_config.get("EXECUTOR_MODE") or "paper").strip().lower()
        research_status = str(execution.get("research_status") or "UNVALIDATED").upper()
        live_static = (
            bool(execution.get("live_enabled"))
            and global_mode == "live"
            and bool(self.root_config.get("REAL_ORDERS_ALLOWED", False))
            and (research_status == "VALIDATED"
                 or not bool(execution.get("require_validated_research_for_live", True)))
        )
        return {
            "defaultMode": str(execution.get("default_mode") or "paper"),
            "globalExecutorMode": global_mode,
            "researchStatus": research_status,
            "modes": {
                "paper": {"enabled": bool(execution.get("paper_enabled", True)), "brokerOrder": False},
                "demo": {"enabled": bool(execution.get("demo_enabled", False)) and global_mode == "demo",
                         "brokerOrder": True, "requiresDemoAccount": True},
                "live": {"enabled": live_static, "brokerOrder": True,
                         "requiresRealAccount": True, "requiresServerConfirmation": True},
            },
        }

    def _assert_mode(self, mode: str, *, confirm_live: bool) -> None:
        if not self.config.enabled:
            raise MuseExecutionError("MUSE_ENGINE_DISABLED")
        normalized = mode.lower()
        capabilities = self.capabilities()["modes"]
        if normalized not in capabilities:
            raise MuseExecutionError("INVALID_EXECUTION_MODE")
        if not bool(capabilities[normalized]["enabled"]):
            raise MuseExecutionError(f"{normalized.upper()}_EXECUTION_DISABLED")
        if normalized == "live" and not confirm_live:
            raise MuseExecutionError("LIVE_CONFIRMATION_REQUIRED")

    def preview(self, signal: dict[str, Any]) -> dict[str, Any]:
        if not self.config.enabled:
            raise MuseExecutionError("MUSE_ENGINE_DISABLED")
        now = float(self.now_fn())
        execution = self.config.execution
        checks: list[dict[str, Any]] = []

        def check(name: str, passed: bool, reason: str | None) -> None:
            checks.append({"name": name, "passed": bool(passed),
                           "reason": None if passed else reason})

        if signal.get("decision") != "PRIME":
            check("signal_prime", False, "SIGNAL_NOT_PRIME")
        else:
            check("signal_prime", True, None)

        gates = {g.get("name"): g for g in signal.get("gates") or [] if isinstance(g, dict)}
        missing = [name for name in REQUIRED_GATE_NAMES if not (gates.get(name) or {}).get("passed")]
        check("signal_gates", not missing, None if not missing else f"GATES_FAILED:{','.join(sorted(missing))}")

        try:
            generated = parse_iso(signal.get("generatedAt"))
        except ValueError:
            check("signal_age", False, "SIGNAL_TIMESTAMP_INVALID")
            generated = 0.0
        else:
            age = now - generated
            max_age = float(execution["max_signal_age_sec"])
            check("signal_age", 0 <= age <= max_age,
                  None if 0 <= age <= max_age else f"SIGNAL_STALE:{age:.0f}s")

        try:
            kill = bool(self.kill_switch_fn())
        except Exception:
            kill = True
        check("kill_switch", not kill, None if not kill else "KILL_SWITCH_ACTIVE")

        asset_type = str(signal.get("assetType") or "unknown")
        closed, _ = market_is_closed(now, self.config, asset_type)
        check("session_open", not closed, None if not closed else "SESSION_CLOSED_AT_EXECUTE")

        try:
            quote = self.gateway.quote(signal)
        except MuseExecutionError as exc:
            check("quote_available", False, exc.code)
            return {"executable": False, "checks": checks, "quote": None, "capabilities": self.capabilities()}
        except Exception:
            check("quote_available", False, "QUOTE_UNAVAILABLE")
            return {"executable": False, "checks": checks, "quote": None, "capabilities": self.capabilities()}
        check("quote_available", True, None)

        quote_age = now - quote.timestamp
        max_quote_age = _asset_limit(execution["maximum_quote_age_sec"], asset_type)
        check("quote_fresh", 0 <= quote_age <= max_quote_age,
              None if 0 <= quote_age <= max_quote_age else "BROKER_QUOTE_STALE")
        if quote.timestamp > now + float(execution["maximum_clock_skew_sec"]):
            check("quote_clock", False, "BROKER_QUOTE_TIMESTAMP_INVALID")
        else:
            check("quote_clock", True, None)

        max_spread = _asset_limit(execution["maximum_spread_bps"], asset_type)
        check("spread_cap", quote.spread_bps <= max_spread,
              None if quote.spread_bps <= max_spread else f"SPREAD_TOO_WIDE:{quote.spread_bps:.1f}bps")

        atr = float(signal.get("atr") or 0.0)
        entry = signal.get("entry")
        try:
            drift_atr = abs(quote.mid - float(entry)) / atr if atr > 0 and entry is not None else 0.0
        except (TypeError, ValueError):
            drift_atr = math.inf
        check("quote_drift", drift_atr <= float(execution["max_quote_drift_atr"]),
              None if drift_atr <= float(execution["max_quote_drift_atr"]) else "QUOTE_DRIFTED")

        executable = all(c["passed"] for c in checks)
        return {"executable": executable, "checks": checks,
                "quote": quote.to_dict(now_epoch=now), "capabilities": self.capabilities()}

    def _size(self, signal: dict[str, Any], quote: Quote, account: dict[str, Any]) -> dict[str, Any]:
        entry = float(signal["entry"])
        stop = float(signal["stop"])
        risk_per_unit = abs(entry - stop)
        if risk_per_unit <= 0:
            raise MuseExecutionError("INVALID_LEVELS")
        equity = float(account.get("equity") or account.get("balance") or self.config.execution["paper_equity"])
        risk_budget = equity * float(self.config.execution["risk_fraction"])
        volume = risk_budget / risk_per_unit
        return {"volume": volume, "riskBudget": risk_budget, "equity": equity,
                "riskPerUnit": risk_per_unit}

    def execute(self, signal: dict[str, Any], *, mode: str, idempotency_key: str,
                confirm_live: bool = False) -> dict[str, Any]:
        self._assert_mode(mode, confirm_live=confirm_live)
        if not idempotency_key or len(idempotency_key) < 8:
            raise MuseExecutionError("IDEMPOTENCY_KEY_REQUIRED")
        preview = self.preview(signal)
        if not preview["executable"]:
            failed = next((c for c in preview["checks"] if not c["passed"]), {})
            raise MuseExecutionError(str(failed.get("reason") or "EXECUTION_BLOCKED"))
        quote_payload = preview["quote"]
        venue = str(signal.get("venue") or "mt5")
        execution_id, duplicate = self.repository.claim_execution(
            signal_id=signal["signalId"], idempotency_key=idempotency_key,
            mode=mode, venue=venue,
            request={"signal": signal["signalId"], "mode": mode, "quote": quote_payload})
        if duplicate:
            return {"status": "PENDING", "idempotent": True, "executionId": execution_id}
        try:
            account = self.gateway.account(venue)
            if not isinstance(account, dict) or account.get("error"):
                raise MuseExecutionError("ACCOUNT_UNAVAILABLE")
            if mode == "demo" and not (account.get("demo") or account.get("testnet")):
                raise MuseExecutionError("DEMO_ACCOUNT_REQUIRED")
            quote = self.gateway.quote(signal)
            sizing = self._size(signal, quote, account)
            direction = str(signal.get("direction") or "").upper()
            payload = {
                "symbol": signal.get("symbol"), "pair": signal.get("pair"),
                "direction": direction, "volume": sizing["volume"],
                "entry": signal.get("entry"), "stop": signal.get("stop"),
                "target": signal.get("target"), "mode": mode,
                "source": "muse_engine", "signalId": signal["signalId"],
                "executionId": execution_id,
            }
            if mode == "paper":
                result = {"paper": True, "sizing": sizing, "quote": quote.to_dict(),
                          "status": "FILLED_PAPER"}
                self.repository.finish_execution(execution_id, "SUCCESS", result)
                return {"status": "SUCCESS", "executionId": execution_id, "result": result}
            approval = {"confirmed": True, "mode": mode,
                        "confirmLive": True if mode == "live" else False,
                        "env": os.environ.get("ATHENA_REAL_ORDERS_CONFIRM", "")}
            broker_result = self.gateway.execute(venue, payload, approval)
            status = "SUCCESS" if isinstance(broker_result, dict) and not broker_result.get("error") else "REJECTED"
            self.repository.finish_execution(execution_id, status, broker_result if isinstance(broker_result, dict) else {})
            return {"status": status, "executionId": execution_id,
                    "result": broker_result if isinstance(broker_result, dict) else {}}
        except MuseExecutionError as exc:
            self.repository.finish_execution(execution_id, "REJECTED", {"error": exc.code})
            raise
        except Exception as exc:
            self.repository.finish_execution(execution_id, "FAILED", {"error": str(exc)})
            raise MuseExecutionError("EXECUTION_FAILED", detail=str(exc)) from exc
