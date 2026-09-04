"""Fail-closed quote attestation, risk sizing and broker coordination for FABLE.

The coordinator never sizes its own order and never bypasses a shared gate:
paper fills are synthetic and stay inside this package; demo and live fills go
through account attestation -> guardian.pre_trade_check -> risk_engine.risk_check
-> the venue executor, with FABLE's own freshness, drift, spread and geometry
gates layered in front.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import math
import os
from typing import Any, Protocol

from .config import FableConfig
from .models import TIMEFRAME_SECONDS, Quote, parse_iso
from .persistence import FableRepository


class FableExecutionError(RuntimeError):
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
        raise FableExecutionError("EXECUTION_LIMIT_INVALID", detail=asset_type) from exc
    if not math.isfinite(result) or result <= 0:
        raise FableExecutionError("EXECUTION_LIMIT_INVALID", detail=asset_type)
    return result


def _finite_positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0


def _step_decimals(step: float) -> int:
    """Decimal places implied by a broker volume step (robust to scientific notation such as 1e-05)."""
    exponent = Decimal(repr(float(step))).normalize().as_tuple().exponent
    return max(0, min(8, -int(exponent)))


def _gate(name: str, passed: bool, reason: str | None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "passed": bool(passed), "reason": None if passed else reason}
    payload.update(extra)
    return payload


class FableExecutionCoordinator:
    def __init__(
        self,
        *,
        config: FableConfig,
        repository: FableRepository,
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

    # ── capabilities / modes ───────────────────────────────────────────

    def capabilities(self) -> dict[str, Any]:
        execution = self.config.execution
        global_mode = str(self.root_config.get("EXECUTOR_MODE") or "paper").strip().lower()
        research_status = str(execution.get("research_status") or "UNVALIDATED").upper()
        live_static = (
            bool(execution.get("live_enabled"))
            and global_mode == "live"
            and bool(self.root_config.get("REAL_ORDERS_ALLOWED", False))
            and (research_status == "VALIDATED" or not bool(execution.get("require_validated_research_for_live", True)))
        )
        configured_default = str(execution.get("default_mode") or "paper").strip().lower()
        follow_global = bool(execution.get("follow_global_executor_mode", True))
        if follow_global and global_mode == "demo" and bool(execution.get("demo_enabled")):
            default_mode = "demo"
        elif follow_global and live_static:
            default_mode = "live"
        elif bool(execution.get(f"{configured_default}_enabled", False)):
            default_mode = configured_default
        elif bool(execution.get("paper_enabled", True)):
            default_mode = "paper"
        else:
            default_mode = configured_default
        return {
            "defaultMode": default_mode,
            "globalExecutorMode": global_mode,
            "researchStatus": research_status,
            "followGlobalExecutorMode": follow_global,
            "riskFraction": float(execution["risk_fraction"]),
            "modes": {
                "paper": {"enabled": bool(execution.get("paper_enabled", True)), "brokerOrder": False},
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
        modes = self.capabilities()["modes"]
        if normalized not in modes:
            raise FableExecutionError("INVALID_EXECUTION_MODE")
        if not bool(modes[normalized]["enabled"]):
            raise FableExecutionError(f"{normalized.upper()}_EXECUTION_DISABLED")
        if normalized == "live":
            if not confirm_live:
                raise FableExecutionError("LIVE_CONFIRMATION_REQUIRED")
            if os.environ.get("ATHENA_REAL_ORDERS_CONFIRM", "") != "I_UNDERSTAND_REAL_ORDER_RISK":
                raise FableExecutionError("LIVE_SERVER_CONFIRMATION_MISSING")

    # ── static (signal-only) gates ─────────────────────────────────────

    def _identity_gates(self, signal: dict[str, Any]) -> list[dict[str, Any]]:
        gates: list[dict[str, Any]] = []
        gates.append(_gate("contract_version", signal.get("contractVersion") == self.config.version, "SIGNAL_CONTRACT_STALE"))
        attestable = str(signal.get("decision") or "").upper() in {"EXECUTE", "STAGE"}
        gates.append(_gate("signal_attestable", attestable, "SIGNAL_NOT_ATTESTABLE"))
        try:
            raw_age = float(self.now_fn()) - parse_iso(signal.get("generatedAt"))
        except (TypeError, ValueError):
            raw_age = math.inf
        clock_skew = float(self.config.execution["maximum_clock_skew_sec"])
        timestamp_valid = math.isfinite(raw_age) and raw_age >= -clock_skew
        gates.append(_gate("signal_timestamp", timestamp_valid, "SIGNAL_TIMESTAMP_INVALID"))
        age = max(0.0, raw_age) if timestamp_valid else math.inf
        max_age = float(self.config.execution["max_signal_age_sec"])
        gates.append(_gate("signal_freshness", age <= max_age, "SIGNAL_STALE", ageSec=age if math.isfinite(age) else None, maxAgeSec=max_age))
        # The narrative bar must still be the current one (or the one before it).
        try:
            bar_age = float(self.now_fn()) - parse_iso(signal.get("barClosedAt"))
            bar_buckets = bar_age / TIMEFRAME_SECONDS["M15"]
        except (TypeError, ValueError):
            bar_buckets = math.inf
        max_buckets = float(self.config.execution["maximum_narrative_bar_age_buckets"])
        gates.append(
            _gate(
                "narrative_bar_fresh",
                math.isfinite(bar_buckets) and bar_buckets <= max_buckets,
                "NARRATIVE_BAR_STALE",
                ageBuckets=round(bar_buckets, 3) if math.isfinite(bar_buckets) else None,
                maxAgeBuckets=max_buckets,
            )
        )
        kill_switch = bool(self.kill_switch_fn())
        gates.append(_gate("kill_switch_clear", not kill_switch, "KILL_SWITCH_ACTIVE"))
        direction_valid = str(signal.get("direction") or "").upper() in {"LONG", "SHORT"}
        venue = str(signal.get("venue") or "").lower()
        asset_type = str(signal.get("assetType") or "").lower()
        venue_matches_asset = venue == ("bybit" if asset_type == "crypto" else "mt5")
        gates.append(_gate("direction_valid", direction_valid, "SIGNAL_DIRECTION_INVALID"))
        gates.append(_gate("venue_valid", venue in {"mt5", "bybit"}, "SIGNAL_VENUE_INVALID"))
        gates.append(_gate("venue_asset_match", venue_matches_asset, "SIGNAL_VENUE_ASSET_MISMATCH"))
        return gates

    def _static_signal_gates(self, signal: dict[str, Any]) -> list[dict[str, Any]]:
        gates = self._identity_gates(signal)
        is_execute = signal.get("decision") == "EXECUTE"
        gates.append(_gate("decision_execute", is_execute, "SIGNAL_NOT_EXECUTE"))
        coherence = signal.get("coherence")
        threshold = float(self.config.scoring["execute_threshold"])
        coherence_ok = _finite_positive(coherence) and float(coherence) >= threshold
        gates.append(_gate("coherence_threshold", coherence_ok, "SIGNAL_COHERENCE_INVALID", coherence=coherence, threshold=threshold))
        # The generatedAt stamp resets on every re-scan; the narrative's own age
        # does not. Fail closed when the story-age stamp is missing or stale.
        narrative_age = signal.get("narrativeAge") if isinstance(signal.get("narrativeAge"), dict) else {}
        bars_since_reclaim = narrative_age.get("barsSinceReclaim")
        max_age_bars = float(self.config.structure["max_narrative_age_bars"])
        age_valid = isinstance(bars_since_reclaim, (int, float)) and not isinstance(bars_since_reclaim, bool) and math.isfinite(float(bars_since_reclaim))
        age_ok = age_valid and 0 <= float(bars_since_reclaim) <= max_age_bars
        gates.append(
            _gate(
                "narrative_age",
                age_ok,
                "NARRATIVE_TOO_OLD" if age_valid else "SIGNAL_NARRATIVE_AGE_MISSING",
                barsSinceReclaim=bars_since_reclaim if age_valid else None,
                maxAgeBars=max_age_bars,
            )
        )
        deterministic = signal.get("gates")
        proof = (
            isinstance(deterministic, list)
            and bool(deterministic)
            and all(isinstance(gate, dict) and gate.get("passed") is True for gate in deterministic)
            and signal.get("voidReasons") == []
        )
        gates.append(_gate("deterministic_gate_proof", proof, "SIGNAL_GATE_PROOF_INVALID"))
        levels_valid = all(_finite_positive(signal.get(key)) for key in ("entry", "stop", "target", "atr"))
        gates.append(_gate("immutable_levels_valid", levels_valid, "SIGNAL_LEVELS_INVALID"))
        return gates

    # ── preview (live quote attestation) ───────────────────────────────

    def preview(self, signal: dict[str, Any]) -> dict[str, Any]:
        identity = self._identity_gates(signal)
        if not all(gate["passed"] for gate in identity):
            return {
                "executable": False,
                "gates": identity,
                "error": next(gate["reason"] for gate in identity if not gate["passed"]),
            }
        try:
            quote = self.gateway.quote(signal)
        except Exception as exc:
            identity.append(_gate("quote_available", False, "QUOTE_UNAVAILABLE"))
            return {"executable": False, "gates": identity, "error": "QUOTE_UNAVAILABLE", "detail": str(exc)}
        gates = self._static_signal_gates(signal)
        gates.append(_gate("quote_available", True, None))

        now_epoch = float(self.now_fn())
        asset_type = str(signal.get("assetType") or "unknown").lower()
        direction = str(signal.get("direction") or "").upper()
        quote_integrity = (
            quote.venue == signal.get("venue")
            and all(math.isfinite(float(value)) and float(value) > 0 for value in (quote.bid, quote.ask))
            and quote.ask >= quote.bid
        )
        gates.append(_gate("quote_integrity", quote_integrity, "BROKER_QUOTE_INVALID"))
        if not quote_integrity:
            return {"executable": False, "error": "BROKER_QUOTE_INVALID", "gates": gates}
        entry = quote.executable_price(direction)
        raw_quote_age = now_epoch - quote.timestamp
        clock_skew = float(self.config.execution["maximum_clock_skew_sec"])
        quote_timestamp_valid = math.isfinite(raw_quote_age) and raw_quote_age >= -clock_skew
        gates.append(_gate("quote_timestamp", quote_timestamp_valid, "BROKER_QUOTE_TIMESTAMP_INVALID"))
        quote_age = max(0.0, raw_quote_age) if quote_timestamp_valid else math.inf
        age_limit = _asset_limit(self.config.execution["maximum_quote_age_sec"], asset_type)
        gates.append(_gate("quote_freshness", quote_age <= age_limit, "BROKER_QUOTE_STALE", ageSec=round(quote_age, 3) if math.isfinite(quote_age) else None, maxAgeSec=age_limit))
        spread_limit = _asset_limit(self.config.execution["maximum_spread_bps"], asset_type)
        spread_ok = math.isfinite(quote.spread_bps) and 0 <= quote.spread_bps <= spread_limit
        gates.append(_gate("spread", spread_ok, "SPREAD_TOO_WIDE", spreadBps=round(quote.spread_bps, 4) if math.isfinite(quote.spread_bps) else None, maxSpreadBps=spread_limit))

        levels_valid = all(_finite_positive(signal.get(key)) for key in ("entry", "stop", "target", "atr"))
        live_rr = 0.0
        live_target: float | None = None
        target_source = signal.get("targetSource")
        if levels_valid:
            atr = float(signal["atr"])
            scan_close = signal.get("scanClose")
            drift_ref = float(scan_close) if _finite_positive(scan_close) else float(signal["entry"])
            adverse = max(0.0, drift_ref - quote.mid) if direction == "LONG" else max(0.0, quote.mid - drift_ref)
            drift_atr = adverse / atr if atr > 0 else math.inf
            drift_limit = float(self.config.execution["max_quote_drift_atr"])
            gates.append(
                _gate(
                    "quote_drift",
                    drift_atr <= drift_limit,
                    "QUOTE_DRIFT_EXCEEDS_LIMIT",
                    driftAtr=round(drift_atr, 5) if math.isfinite(drift_atr) else None,
                    maxDriftAtr=drift_limit,
                    driftRef=drift_ref,
                )
            )
            # EXECUTE means "price is inside the imbalance"; the live entry must
            # still be inside it (favourable drift is not caught by the adverse
            # drift gate above).
            array = (signal.get("annotations") or {}).get("array") if isinstance(signal.get("annotations"), dict) else None
            tolerance = atr * float(self.config.structure["return_tolerance_atr"])
            inside = (
                isinstance(array, dict)
                and _finite_positive(array.get("low"))
                and _finite_positive(array.get("high"))
                and float(array["low"]) - tolerance <= entry <= float(array["high"]) + tolerance
            )
            gates.append(
                _gate(
                    "inside_imbalance",
                    inside,
                    "PRICE_LEFT_IMBALANCE",
                    arrayLow=array.get("low") if isinstance(array, dict) else None,
                    arrayHigh=array.get("high") if isinstance(array, dict) else None,
                    toleranceAtr=float(self.config.structure["return_tolerance_atr"]),
                )
            )
            # The stop is immutable. The live entry may move a little inside the
            # imbalance, so the reward is re-measured; target2 (the external draw)
            # is the only permitted fallback when target1 no longer clears min RR.
            stop = float(signal["stop"])
            risk = abs(entry - stop)
            levels = self.config.levels_for(asset_type)
            minimum_rr = float(levels["minimum_rr"])

            def rr_for(target: float | None) -> float:
                if target is None or risk <= 0:
                    return 0.0
                if direction == "LONG":
                    return (target - entry) / risk if stop < entry < target else 0.0
                return (entry - target) / risk if target < entry < stop else 0.0

            live_target = float(signal["target"])
            live_rr = rr_for(live_target)
            if live_rr < minimum_rr and _finite_positive(signal.get("target2")):
                fallback_rr = rr_for(float(signal["target2"]))
                if fallback_rr >= minimum_rr:
                    live_target = float(signal["target2"])
                    live_rr = fallback_rr
                    target_source = signal.get("target2Source")
            stop_still_beyond = (stop < entry) if direction == "LONG" else (stop > entry)
            geometry_ok = stop_still_beyond and live_rr >= minimum_rr
            gates.append(_gate("live_geometry", geometry_ok, "LIVE_GEOMETRY_INVALID", liveRr=round(live_rr, 4), minimumRr=minimum_rr, targetSource=target_source))
        executable = all(bool(gate["passed"]) for gate in gates)
        return {
            "executable": executable,
            "error": None if executable else next(gate["reason"] for gate in gates if not gate["passed"]),
            "gates": gates,
            "quote": quote.to_dict(now_epoch=now_epoch),
            "quoteEpoch": quote.timestamp,
            "executableEntry": entry,
            "liveRr": round(live_rr, 4),
            "liveStop": float(signal["stop"]) if levels_valid else None,
            "liveTarget": live_target,
            "liveTargetSource": target_source,
        }

    # ── broker payload ─────────────────────────────────────────────────

    def _broker_payload(self, signal: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any]:
        now_iso = datetime.fromtimestamp(float(self.now_fn()), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        freshness = signal.get("dataFreshness") if isinstance(signal.get("dataFreshness"), dict) else {}
        candle_freshness: dict[str, dict[str, Any]] = {}
        for timeframe, diag in freshness.items():
            if not isinstance(diag, dict):
                continue
            entry: dict[str, Any] = {
                "status": str(diag.get("status") or "UNKNOWN"),
                "lastBarIso": diag.get("lastBarIso"),
                "ageBuckets": diag.get("ageBuckets"),
                "source": diag.get("source"),
            }
            if diag.get("stalenessSeverity"):
                entry["stalenessSeverity"] = diag["stalenessSeverity"]
            candle_freshness[str(timeframe).upper()] = entry
        target = float(preview["liveTarget"] if preview.get("liveTarget") is not None else signal["target"])
        return {
            "pair": signal["pair"],
            "symbol": signal.get("symbol") or signal["pair"],
            "display": signal["pair"],
            "type": signal["assetType"],
            "engine": "FABLE",
            "source": "fable_engine",
            "fableExecution": True,
            "direction": signal["direction"],
            "price": float(preview["executableEntry"]),
            "livePrice": float(preview["executableEntry"]),
            "sl": float(signal["stop"]),
            "tp1": target,
            "tp2": target,
            "rr": float(preview["liveRr"]),
            "confluenceScore": float(signal["coherence"]),
            "maxScore": 100.0,
            "executionConvictionEffective": max(0.25, min(1.0, float(signal["coherence"]) / 100.0)),
            "timestamp": now_iso,
            "quoteTimestamp": float(preview["quoteEpoch"]),
            "quoteAgeSec": float((preview.get("quote") or {}).get("ageSec") or 0.0),
            "candleFreshness": candle_freshness,
            "fableCandleProvenance": signal.get("dataProvenance"),
            "decision_state": "execute",
            "tier": "TRADE",
            "entryReadiness": "READY",
            "fableSignalId": signal["signalId"],
            "fableContractVersion": signal["contractVersion"],
            "fableTier": signal.get("tier"),
            "fableQuoteAttestation": preview,
        }

    @staticmethod
    def _positions_list(raw: dict[str, Any]) -> list[dict[str, Any]]:
        if raw.get("error"):
            raise FableExecutionError("POSITIONS_UNAVAILABLE", detail=str(raw.get("detail") or "unknown"))
        rows = raw.get("positions")
        if not isinstance(rows, list):
            raise FableExecutionError("POSITIONS_UNAVAILABLE", detail="invalid response")
        return rows

    def _validate_broker_environment(self, mode: str, account: dict[str, Any]) -> None:
        if account.get("error"):
            raise FableExecutionError("ACCOUNT_UNAVAILABLE", detail=str(account.get("detail") or "unknown"))
        if mode == "demo":
            is_demo = bool(account.get("demo")) or str(account.get("accountEnvironment") or "").lower() == "demo"
            if not is_demo:
                raise FableExecutionError("DEMO_ACCOUNT_ATTESTATION_FAILED")
        if mode == "live":
            is_real = (
                account.get("demo") is False and account.get("testnet") is False
                if "demo" in account
                else str(account.get("accountEnvironment") or "").lower() == "real"
            )
            if not is_real:
                raise FableExecutionError("REAL_ACCOUNT_ATTESTATION_FAILED")

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
        immutable_levels = {key: float(payload[key]) for key in ("price", "livePrice", "sl", "tp1", "tp2")}
        guardian_ok, guardian_reason = guardian.pre_trade_check(
            risk_payload,
            positions,
            account,
            positions_raw=positions_raw,
        )
        if not guardian_ok:
            raise FableExecutionError("GUARDIAN_REJECTED", detail=guardian_reason)
        approval = risk_engine.risk_check(
            risk_payload,
            account_balance=account.get("balance"),
            account_equity=account.get("equity"),
            open_positions=positions,
            symbol_info=symbol_info,
            kill_switch=bool(self.kill_switch_fn()),
            account_domain=account.get("risk_domain"),
            volume_mode="calculated",
            execution_context="fable_engine",
        )
        levels_unchanged = all(
            _finite_positive(risk_payload.get(key)) and math.isclose(float(risk_payload[key]), expected, rel_tol=0.0, abs_tol=1e-12)
            for key, expected in immutable_levels.items()
        )
        if not levels_unchanged:
            raise FableExecutionError("IMMUTABLE_LEVELS_MUTATED_BY_RISK_GATE")
        if not approval.approved:
            raise FableExecutionError("RISK_REJECTED", detail=str(approval.reason))
        approval_values = (approval.volume, approval.risk_amount, approval.risk_pct)
        if (
            not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in approval_values)
            or float(approval.volume) <= 0
            or float(approval.risk_amount) <= 0
            or float(approval.risk_pct) < 0
        ):
            raise FableExecutionError("RISK_APPROVAL_INVALID")

        equity = float(account.get("equity") or 0.0)
        desired_risk = equity * float(self.config.execution["risk_fraction"])
        if approval.risk_amount > desired_risk > 0:
            ratio = desired_risk / approval.risk_amount
            step = float(symbol_info.get("volume_step") or (0.001 if payload["type"] == "crypto" else 0.01))
            minimum = float(symbol_info.get("volume_min") or step)
            downsized = math.floor((approval.volume * ratio) / step) * step if step > 0 else approval.volume * ratio
            decimals = _step_decimals(step)
            downsized = round(downsized, decimals)
            if downsized < minimum or downsized <= 0:
                raise FableExecutionError("FABLE_RISK_BUDGET_BELOW_BROKER_MINIMUM")
            actual_risk = approval.risk_amount * (downsized / approval.volume)
            approval = replace(
                approval,
                volume=downsized,
                risk_amount=actual_risk,
                risk_pct=actual_risk / equity if equity > 0 else 0.0,
                reason="OK_FABLE_DOWNSIZED",
            )
        return approval

    # ── execute ("seal" the narrative) ─────────────────────────────────

    def execute(
        self,
        signal: dict[str, Any],
        *,
        mode: str,
        idempotency_key: str,
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        normalized_mode = str(mode or self.capabilities()["defaultMode"] or self.config.execution["default_mode"]).strip().lower()
        if not idempotency_key or len(idempotency_key) > 128:
            raise FableExecutionError("IDEMPOTENCY_KEY_REQUIRED")
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
                raise FableExecutionError("IDEMPOTENCY_KEY_CONFLICT")
            return {"idempotent": True, **reservation}
        execution_id = reservation["execution_id"]
        try:
            preview = self.preview(signal)
            if not preview.get("executable"):
                raise FableExecutionError(str(preview.get("error") or "EXECUTION_PREVIEW_REJECTED"))
            if normalized_mode == "paper":
                risk_cash = float(self.config.execution["paper_equity"]) * float(self.config.execution["risk_fraction"])
                risk_per_unit = abs(float(preview["executableEntry"]) - float(signal["stop"]))
                quantity = risk_cash / risk_per_unit if risk_per_unit > 0 else 0.0
                result = {
                    "success": True,
                    "mode": "paper",
                    "ticket": "FABLE-PAPER-" + execution_id[-12:].upper(),
                    "entryPrice": float(preview["executableEntry"]),
                    "quantity": quantity,
                    "riskCash": risk_cash,
                    "stop": signal["stop"],
                    "target": preview["liveTarget"] if preview.get("liveTarget") is not None else signal["target"],
                    "quote": preview["quote"],
                    "message": "Paper fill recorded; no broker order was placed.",
                }
            else:
                account = self.gateway.account(signal["venue"])
                self._validate_broker_environment(normalized_mode, account)
                positions_raw = self.gateway.positions(signal["venue"])
                symbol_info = self.gateway.symbol_info(signal)
                if symbol_info.get("error"):
                    raise FableExecutionError("SYMBOL_INFO_UNAVAILABLE", detail=str(symbol_info.get("detail") or "unknown"))
                payload = self._broker_payload(signal, preview)
                approval = self._risk_approval(payload, account=account, positions_raw=positions_raw, symbol_info=symbol_info)
                result = self.gateway.execute(signal["venue"], payload, approval)
                if not isinstance(result, dict) or not result.get("success"):
                    detail = result.get("error") if isinstance(result, dict) else "invalid broker response"
                    raise FableExecutionError("BROKER_EXECUTION_REJECTED", detail=str(detail))
                result = {**result, "mode": normalized_mode, "riskApproval": approval.to_dict(), "quote": preview["quote"]}
            completed = self.repository.complete_execution(execution_id, "SUCCESS", result)
            return {"idempotent": False, **completed}
        except FableExecutionError as exc:
            rejected = {"success": False, "error": exc.code, "detail": exc.detail}
            completed = self.repository.complete_execution(execution_id, "REJECTED", rejected)
            return {"idempotent": False, **completed}
        except Exception as exc:  # broker/library faults are recorded, never raised to the route
            failed = {"success": False, "error": "EXECUTION_INTERNAL_ERROR", "detail": str(exc)}
            completed = self.repository.complete_execution(execution_id, "FAILED", failed)
            return {"idempotent": False, **completed}
