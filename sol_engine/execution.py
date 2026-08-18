"""Fail-closed quote attestation, risk sizing, and broker coordination for SOL."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import math
import os
from typing import Any, Protocol

from .config import SolConfig
from .models import Quote
from .persistence import SolRepository


class SolExecutionError(RuntimeError):
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


def _parse_iso(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp missing")
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _asset_limit(mapping: dict[str, Any], asset_type: str) -> float:
    value = mapping.get(asset_type, mapping.get("default"))
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SolExecutionError("EXECUTION_LIMIT_INVALID", detail=asset_type) from exc
    if not math.isfinite(result) or result <= 0:
        raise SolExecutionError("EXECUTION_LIMIT_INVALID", detail=asset_type)
    return result


class SolExecutionCoordinator:
    def __init__(
        self,
        *,
        config: SolConfig,
        repository: SolRepository,
        gateway: BrokerGateway,
        root_config: dict[str, Any],
        kill_switch_fn,
        now_fn,
    ) -> None:
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
            and (
                research_status == "VALIDATED"
                or not bool(execution.get("require_validated_research_for_live", True))
            )
        )
        return {
            "defaultMode": str(execution.get("default_mode") or "paper"),
            "globalExecutorMode": global_mode,
            "researchStatus": research_status,
            "modes": {
                "paper": {
                    "enabled": bool(execution.get("paper_enabled", True)),
                    "brokerOrder": False,
                },
                "demo": {
                    "enabled": bool(execution.get("demo_enabled", False)) and global_mode == "demo",
                    "brokerOrder": True,
                    "requiresDemoAccount": True,
                },
                "live": {
                    "enabled": live_static,
                    "brokerOrder": True,
                    "requiresRealAccount": True,
                    "requiresServerConfirmation": True,
                },
            },
        }

    def _assert_mode(self, mode: str, *, confirm_live: bool) -> None:
        normalized = mode.lower()
        capabilities = self.capabilities()["modes"]
        if normalized not in capabilities:
            raise SolExecutionError("INVALID_EXECUTION_MODE")
        if not bool(capabilities[normalized]["enabled"]):
            raise SolExecutionError(f"{normalized.upper()}_EXECUTION_DISABLED")
        if normalized == "live":
            if not confirm_live:
                raise SolExecutionError("LIVE_CONFIRMATION_REQUIRED")
            if os.environ.get("ATHENA_REAL_ORDERS_CONFIRM", "") != "I_UNDERSTAND_REAL_ORDER_RISK":
                raise SolExecutionError("LIVE_SERVER_CONFIRMATION_MISSING")

    def _static_signal_gates(self, signal: dict[str, Any]) -> list[dict[str, Any]]:
        gates: list[dict[str, Any]] = []
        contract_matches = signal.get("contractVersion") == self.config.version
        gates.append(
            {
                "name": "contract_version",
                "passed": contract_matches,
                "reason": None if contract_matches else "SIGNAL_CONTRACT_STALE",
            }
        )
        gates.append(
            {
                "name": "signal_ready",
                "passed": signal.get("decision") == "READY",
                "reason": None if signal.get("decision") == "READY" else "SIGNAL_NOT_READY",
            }
        )
        score = signal.get("score")
        score_valid = (
            isinstance(score, (int, float))
            and not isinstance(score, bool)
            and math.isfinite(float(score))
            and float(score) >= float(self.config.scoring["ready_threshold"])
        )
        deterministic_gates = signal.get("gates")
        deterministic_gate_proof = (
            isinstance(deterministic_gates, list)
            and bool(deterministic_gates)
            and all(isinstance(gate, dict) and gate.get("passed") is True for gate in deterministic_gates)
            and signal.get("blockingReasons") == []
        )
        gates.extend(
            [
                {
                    "name": "score_threshold",
                    "passed": score_valid,
                    "reason": None if score_valid else "SIGNAL_SCORE_INVALID",
                },
                {
                    "name": "deterministic_gate_proof",
                    "passed": deterministic_gate_proof,
                    "reason": None if deterministic_gate_proof else "SIGNAL_GATE_PROOF_INVALID",
                },
            ]
        )
        try:
            raw_age = float(self.now_fn()) - _parse_iso(signal.get("generatedAt"))
        except (TypeError, ValueError):
            raw_age = math.inf
        clock_skew = float(self.config.execution["maximum_clock_skew_sec"])
        timestamp_valid = math.isfinite(raw_age) and raw_age >= -clock_skew
        gates.append(
            {
                "name": "signal_timestamp",
                "passed": timestamp_valid,
                "reason": None if timestamp_valid else "SIGNAL_TIMESTAMP_INVALID",
            }
        )
        age = max(0.0, raw_age) if timestamp_valid else math.inf
        max_age = float(self.config.execution["max_signal_age_sec"])
        gates.append(
            {
                "name": "signal_freshness",
                "passed": age <= max_age,
                "reason": None if age <= max_age else "SIGNAL_STALE",
                "ageSec": age if math.isfinite(age) else None,
                "maxAgeSec": max_age,
            }
        )
        kill_switch = bool(self.kill_switch_fn())
        gates.append(
            {
                "name": "kill_switch_clear",
                "passed": not kill_switch,
                "reason": "KILL_SWITCH_ACTIVE" if kill_switch else None,
            }
        )
        levels_valid = all(
            isinstance(signal.get(key), (int, float))
            and not isinstance(signal.get(key), bool)
            and math.isfinite(float(signal[key]))
            and float(signal[key]) > 0
            for key in ("entry", "stop", "target", "atr")
        )
        gates.append(
            {
                "name": "immutable_levels_valid",
                "passed": levels_valid,
                "reason": None if levels_valid else "SIGNAL_LEVELS_INVALID",
            }
        )
        direction_valid = str(signal.get("direction") or "").upper() in {"LONG", "SHORT"}
        venue_valid = str(signal.get("venue") or "").lower() in {"mt5", "bybit"}
        asset_type = str(signal.get("assetType") or "").lower()
        venue_matches_asset = (
            signal.get("venue") == "bybit" if asset_type == "crypto" else signal.get("venue") == "mt5"
        )
        gates.extend(
            [
                {
                    "name": "direction_valid",
                    "passed": direction_valid,
                    "reason": None if direction_valid else "SIGNAL_DIRECTION_INVALID",
                },
                {
                    "name": "venue_valid",
                    "passed": venue_valid,
                    "reason": None if venue_valid else "SIGNAL_VENUE_INVALID",
                },
                {
                    "name": "venue_asset_match",
                    "passed": venue_matches_asset,
                    "reason": None if venue_matches_asset else "SIGNAL_VENUE_ASSET_MISMATCH",
                },
            ]
        )
        return gates

    def preview(self, signal: dict[str, Any]) -> dict[str, Any]:
        gates = self._static_signal_gates(signal)
        if not all(gate["passed"] for gate in gates):
            return {
                "executable": False,
                "gates": gates,
                "error": next(gate["reason"] for gate in gates if not gate["passed"]),
            }
        try:
            quote = self.gateway.quote(signal)
        except Exception as exc:
            gates.append({"name": "quote_available", "passed": False, "reason": "QUOTE_UNAVAILABLE"})
            return {"executable": False, "gates": gates, "error": "QUOTE_UNAVAILABLE", "detail": str(exc)}

        now_epoch = float(self.now_fn())
        asset_type = str(signal.get("assetType") or "unknown").lower()
        direction = str(signal.get("direction") or "").upper()
        quote_integrity = (
            quote.venue == signal.get("venue")
            and all(math.isfinite(float(value)) and float(value) > 0 for value in (quote.bid, quote.ask))
            and quote.ask >= quote.bid
        )
        gates.append(
            {
                "name": "quote_integrity",
                "passed": quote_integrity,
                "reason": None if quote_integrity else "BROKER_QUOTE_INVALID",
            }
        )
        if not quote_integrity:
            return {
                "executable": False,
                "error": "BROKER_QUOTE_INVALID",
                "gates": gates,
            }
        entry = quote.executable_price(direction)
        raw_quote_age = now_epoch - quote.timestamp
        clock_skew = float(self.config.execution["maximum_clock_skew_sec"])
        quote_timestamp_valid = math.isfinite(raw_quote_age) and raw_quote_age >= -clock_skew
        gates.append(
            {
                "name": "quote_timestamp",
                "passed": quote_timestamp_valid,
                "reason": None if quote_timestamp_valid else "BROKER_QUOTE_TIMESTAMP_INVALID",
            }
        )
        quote_age = max(0.0, raw_quote_age) if quote_timestamp_valid else math.inf
        age_limit = _asset_limit(self.config.execution["maximum_quote_age_sec"], asset_type)
        quote_fresh = quote_age <= age_limit
        gates.append(
            {
                "name": "quote_freshness",
                "passed": quote_fresh,
                "reason": None if quote_fresh else "BROKER_QUOTE_STALE",
                "ageSec": round(quote_age, 3),
                "maxAgeSec": age_limit,
            }
        )
        spread_limit = _asset_limit(self.config.execution["maximum_spread_bps"], asset_type)
        spread_ok = math.isfinite(quote.spread_bps) and 0 <= quote.spread_bps <= spread_limit
        gates.append(
            {
                "name": "spread",
                "passed": spread_ok,
                "reason": None if spread_ok else "SPREAD_TOO_WIDE",
                "spreadBps": round(quote.spread_bps, 4) if math.isfinite(quote.spread_bps) else None,
                "maxSpreadBps": spread_limit,
            }
        )
        atr = float(signal["atr"])
        signal_entry = float(signal["entry"])
        drift_atr = abs(entry - signal_entry) / atr if atr > 0 else math.inf
        drift_limit = float(self.config.execution["max_quote_drift_atr"])
        drift_ok = drift_atr <= drift_limit
        gates.append(
            {
                "name": "quote_drift",
                "passed": drift_ok,
                "reason": None if drift_ok else "QUOTE_DRIFT_EXCEEDS_LIMIT",
                "driftAtr": round(drift_atr, 5) if math.isfinite(drift_atr) else None,
                "maxDriftAtr": drift_limit,
            }
        )
        stop = float(signal["stop"])
        target = float(signal["target"])
        if direction == "LONG":
            correct_side = stop < entry < target
            live_rr = (target - entry) / (entry - stop) if correct_side else 0.0
        else:
            correct_side = target < entry < stop
            live_rr = (entry - target) / (stop - entry) if correct_side else 0.0
        minimum_rr = float(self.config.levels["minimum_rr"])
        geometry_ok = correct_side and live_rr >= minimum_rr
        gates.append(
            {
                "name": "live_geometry",
                "passed": geometry_ok,
                "reason": None if geometry_ok else "LIVE_GEOMETRY_INVALID",
                "liveRr": round(live_rr, 4),
                "minimumRr": minimum_rr,
            }
        )
        executable = all(bool(gate["passed"]) for gate in gates)
        return {
            "executable": executable,
            "error": None if executable else next(gate["reason"] for gate in gates if not gate["passed"]),
            "gates": gates,
            "quote": quote.to_dict(now_epoch=now_epoch),
            "quoteEpoch": quote.timestamp,
            "executableEntry": entry,
            "liveRr": round(live_rr, 4),
        }

    def _broker_payload(self, signal: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any]:
        now_iso = datetime.fromtimestamp(float(self.now_fn()), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        candle_freshness: dict[str, dict[str, Any]] = {}
        provenance = signal.get("dataProvenance") if isinstance(signal.get("dataProvenance"), dict) else {}
        for gate in signal.get("gates") or []:
            if not isinstance(gate, dict):
                continue
            name = str(gate.get("name") or "")
            if not name.endswith("_freshness"):
                continue
            timeframe = name.split("_", 1)[0].upper()
            source_meta = provenance.get(timeframe)
            diag: dict[str, Any] = {
                "status": "FRESH" if gate.get("passed") is True else str(gate.get("reason") or "UNKNOWN"),
                "lastBarIso": gate.get("lastClosedAt"),
                "ageBuckets": gate.get("ageBuckets"),
                "source": source_meta.get("provider") if isinstance(source_meta, dict) else None,
            }
            if gate.get("passed") is not True:
                diag["stalenessSeverity"] = str(gate.get("reason") or "unknown")
            candle_freshness[timeframe] = diag
        return {
            "pair": signal["pair"],
            "symbol": signal.get("symbol") or signal["pair"],
            "display": signal["pair"],
            "type": signal["assetType"],
            "engine": "SOL",
            "source": "sol_engine",
            "solExecution": True,
            "direction": signal["direction"],
            "price": float(preview["executableEntry"]),
            "livePrice": float(preview["executableEntry"]),
            "sl": float(signal["stop"]),
            "tp1": float(signal["target"]),
            "tp2": float(signal["target"]),
            "rr": float(preview["liveRr"]),
            "confluenceScore": float(signal["score"]),
            "maxScore": 100.0,
            "executionConvictionEffective": max(0.25, min(1.0, float(signal["score"]) / 100.0)),
            "timestamp": now_iso,
            "quoteTimestamp": float(preview["quoteEpoch"]),
            "quoteAgeSec": float((preview.get("quote") or {}).get("ageSec") or 0.0),
            "candleFreshness": candle_freshness,
            "solCandleProvenance": provenance,
            "decision_state": "execute",
            "tier": "TRADE",
            "entryReadiness": "READY",
            "solSignalId": signal["signalId"],
            "solContractVersion": signal["contractVersion"],
            "solQuoteAttestation": preview,
        }

    @staticmethod
    def _positions_list(raw: dict[str, Any]) -> list[dict[str, Any]]:
        if raw.get("error"):
            raise SolExecutionError("POSITIONS_UNAVAILABLE", detail=str(raw.get("detail") or "unknown"))
        rows = raw.get("positions")
        if not isinstance(rows, list):
            raise SolExecutionError("POSITIONS_UNAVAILABLE", detail="invalid response")
        return rows

    def _validate_broker_environment(self, mode: str, account: dict[str, Any]) -> None:
        if account.get("error"):
            raise SolExecutionError("ACCOUNT_UNAVAILABLE", detail=str(account.get("detail") or "unknown"))
        if mode == "demo":
            is_demo = bool(account.get("demo")) or str(account.get("accountEnvironment") or "").lower() == "demo"
            if not is_demo:
                raise SolExecutionError("DEMO_ACCOUNT_ATTESTATION_FAILED")
        if mode == "live":
            is_real = (
                account.get("demo") is False and account.get("testnet") is False
                if "demo" in account
                else str(account.get("accountEnvironment") or "").lower() == "real"
            )
            if not is_real:
                raise SolExecutionError("REAL_ACCOUNT_ATTESTATION_FAILED")

    def _risk_approval(
        self,
        payload: dict[str, Any],
        *,
        account: dict[str, Any],
        positions_raw: dict[str, Any],
        symbol_info: dict[str, Any],
    ):
        positions = self._positions_list(positions_raw)
        import guardian
        import risk_engine

        risk_payload = deepcopy(payload)
        immutable_levels = {
            key: float(payload[key])
            for key in ("price", "livePrice", "sl", "tp1", "tp2")
        }
        guardian_ok, guardian_reason = guardian.pre_trade_check(
            risk_payload,
            positions,
            account,
            positions_raw=positions_raw,
        )
        if not guardian_ok:
            raise SolExecutionError("GUARDIAN_REJECTED", detail=guardian_reason)
        approval = risk_engine.risk_check(
            risk_payload,
            account_balance=account.get("balance"),
            account_equity=account.get("equity"),
            open_positions=positions,
            symbol_info=symbol_info,
            kill_switch=bool(self.kill_switch_fn()),
            account_domain=account.get("risk_domain"),
            volume_mode="calculated",
            execution_context="sol_engine",
        )
        levels_unchanged = all(
            isinstance(risk_payload.get(key), (int, float))
            and not isinstance(risk_payload.get(key), bool)
            and math.isclose(float(risk_payload[key]), expected, rel_tol=0.0, abs_tol=1e-12)
            for key, expected in immutable_levels.items()
        )
        if not levels_unchanged:
            raise SolExecutionError("IMMUTABLE_LEVELS_MUTATED_BY_RISK_GATE")
        if not approval.approved:
            raise SolExecutionError("RISK_REJECTED", detail=str(approval.reason))

        approval_values = (approval.volume, approval.risk_amount, approval.risk_pct)
        if (
            not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in approval_values)
            or float(approval.volume) <= 0
            or float(approval.risk_amount) <= 0
            or float(approval.risk_pct) < 0
        ):
            raise SolExecutionError("RISK_APPROVAL_INVALID")

        equity = float(account.get("equity") or 0.0)
        desired_risk = equity * float(self.config.execution["risk_fraction"])
        if approval.risk_amount > desired_risk > 0:
            ratio = desired_risk / approval.risk_amount
            step = float(symbol_info.get("volume_step") or (0.001 if payload["type"] == "crypto" else 0.01))
            minimum = float(symbol_info.get("volume_min") or step)
            downsized = math.floor((approval.volume * ratio) / step) * step if step > 0 else approval.volume * ratio
            decimals = max(0, min(8, len(str(step).split(".", 1)[1].rstrip("0")) if "." in str(step) else 0))
            downsized = round(downsized, decimals)
            if downsized < minimum or downsized <= 0:
                raise SolExecutionError("SOL_RISK_BUDGET_BELOW_BROKER_MINIMUM")
            actual_risk = approval.risk_amount * (downsized / approval.volume)
            approval = replace(
                approval,
                volume=downsized,
                risk_amount=actual_risk,
                risk_pct=actual_risk / equity if equity > 0 else 0.0,
                reason="OK_SOL_DOWNSIZED",
            )
        return approval

    def execute(
        self,
        signal: dict[str, Any],
        *,
        mode: str,
        idempotency_key: str,
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        normalized_mode = str(mode or self.config.execution["default_mode"]).strip().lower()
        if not idempotency_key or len(idempotency_key) > 128:
            raise SolExecutionError("IDEMPOTENCY_KEY_REQUIRED")
        self._assert_mode(normalized_mode, confirm_live=confirm_live)
        reservation, created = self.repository.reserve_execution(
            signal_id=signal["signalId"],
            idempotency_key=idempotency_key,
            mode=normalized_mode,
            venue=signal["venue"],
            request_payload={"confirmLive": bool(confirm_live), "signal": signal},
        )
        if not created:
            if reservation.get("signal_id") != signal.get("signalId"):
                raise SolExecutionError("IDEMPOTENCY_KEY_CONFLICT")
            return {"idempotent": True, **reservation}
        execution_id = reservation["execution_id"]
        try:
            preview = self.preview(signal)
            if not preview.get("executable"):
                raise SolExecutionError(str(preview.get("error") or "EXECUTION_PREVIEW_REJECTED"))
            if normalized_mode == "paper":
                risk_cash = float(self.config.execution["paper_equity"]) * float(self.config.execution["risk_fraction"])
                risk_per_unit = abs(float(preview["executableEntry"]) - float(signal["stop"]))
                quantity = risk_cash / risk_per_unit if risk_per_unit > 0 else 0.0
                result = {
                    "success": True,
                    "mode": "paper",
                    "ticket": "SOL-PAPER-" + execution_id[-12:].upper(),
                    "entryPrice": float(preview["executableEntry"]),
                    "quantity": quantity,
                    "riskCash": risk_cash,
                    "stop": signal["stop"],
                    "target": signal["target"],
                    "quote": preview["quote"],
                    "message": "Paper fill recorded; no broker order was placed.",
                }
            else:
                account = self.gateway.account(signal["venue"])
                self._validate_broker_environment(normalized_mode, account)
                positions_raw = self.gateway.positions(signal["venue"])
                symbol_info = self.gateway.symbol_info(signal)
                if symbol_info.get("error"):
                    raise SolExecutionError("SYMBOL_INFO_UNAVAILABLE", detail=str(symbol_info.get("detail") or "unknown"))
                payload = self._broker_payload(signal, preview)
                approval = self._risk_approval(
                    payload,
                    account=account,
                    positions_raw=positions_raw,
                    symbol_info=symbol_info,
                )
                result = self.gateway.execute(signal["venue"], payload, approval)
                if not isinstance(result, dict) or not result.get("success"):
                    detail = result.get("error") if isinstance(result, dict) else "invalid broker response"
                    raise SolExecutionError("BROKER_EXECUTION_REJECTED", detail=str(detail))
                result = {
                    **result,
                    "mode": normalized_mode,
                    "riskApproval": approval.to_dict(),
                    "quote": preview["quote"],
                }
            completed = self.repository.complete_execution(execution_id, "SUCCESS", result)
            return {"idempotent": False, **completed}
        except SolExecutionError as exc:
            rejected = {"success": False, "error": exc.code, "detail": exc.detail}
            completed = self.repository.complete_execution(execution_id, "REJECTED", rejected)
            return {"idempotent": False, **completed}
        except Exception as exc:
            failed = {"success": False, "error": "EXECUTION_INTERNAL_ERROR", "detail": str(exc)}
            completed = self.repository.complete_execution(execution_id, "FAILED", failed)
            return {"idempotent": False, **completed}
