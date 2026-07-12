"""Idempotent, gated orchestration for manual and automatic demo execution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from ..config import GhostConfig
from ..models import GhostMode, Venue
from ..persistence import GhostRepository, SignalConflictError
from ..position_sizing import SizeResult
from ..risk import GhostRiskService
from .base import DemoExecutionResult, ghost_executor_payload


@dataclass(frozen=True, slots=True)
class CoordinatorResult:
    success: bool
    status: str
    execution_id: str
    error_code: str | None = None
    broker_order_id: str | None = None
    broker_position_id: str | None = None


class GhostExecutionCoordinator:
    def __init__(
        self,
        *,
        config: GhostConfig,
        repository: GhostRepository,
        risk_service: GhostRiskService,
        adapters: Mapping[Venue, Any],
        account_provider: Callable[[Venue], Mapping[str, Any]],
        positions_provider: Callable[[Venue], list],
        symbol_info_provider: Callable[[Any], Mapping[str, Any]],
        size_fn: Callable[..., SizeResult],
        guardian_fn: Callable[..., tuple[bool, str]],
        approval_fn: Callable[..., Any],
        clock: Callable[[], datetime] | None = None,
    ):
        self.config = config
        self.repository = repository
        self.risk_service = risk_service
        self.adapters = dict(adapters)
        self._account_provider = account_provider
        self._positions_provider = positions_provider
        self._symbol_info_provider = symbol_info_provider
        self._size_fn = size_fn
        self._guardian = guardian_fn
        self._approval = approval_fn
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def attestations(self) -> dict[Venue, Any]:
        return {venue: adapter.attest() for venue, adapter in self.adapters.items()}

    def venue_verified(self, venue: Venue) -> bool:
        adapter = self.adapters.get(venue)
        return bool(adapter is not None and adapter.attest().verified)

    def execute(
        self,
        signal_id: str,
        *,
        expected_version: str,
        idempotency_key: str,
    ) -> CoordinatorResult:
        if self.config.mode is GhostMode.SHADOW:
            return CoordinatorResult(False, "REJECTED", "", "shadow_mode_execution_prohibited")
        signal = self.repository.get_signal(signal_id)
        if signal is None:
            return CoordinatorResult(False, "REJECTED", "", "signal_not_found")
        execution_id = str(uuid.uuid4())
        try:
            reserved_id = self.repository.reserve_execution(
                signal_id,
                expected_version=expected_version,
                execution_id=execution_id,
                idempotency_key=idempotency_key,
            )
        except SignalConflictError as exc:
            return CoordinatorResult(False, "REJECTED", execution_id, str(exc))
        existing = self.repository.get_execution_attempt(reserved_id)
        if reserved_id != execution_id or (existing and existing["status"] != "RESERVED"):
            response = (existing or {}).get("response") or {}
            return CoordinatorResult(
                bool(response.get("success")),
                str((existing or {}).get("status") or "UNKNOWN"),
                reserved_id,
                (existing or {}).get("error_code"),
                response.get("broker_order_id"),
                response.get("broker_position_id"),
            )
        adapter = self.adapters.get(signal.instrument.venue)
        if adapter is None or not adapter.attest().verified:
            return self._reject(execution_id, "demo_not_verified")
        open_positions = self.repository.list_positions(status="OPEN")
        risk_gate = self.risk_service.evaluate(signal, open_positions)
        if not risk_gate.approved:
            return self._reject(execution_id, f"ghost_risk_rejected:{','.join(risk_gate.reasons)}")
        account = dict(self._account_provider(signal.instrument.venue) or {})
        provider_positions = list(self._positions_provider(signal.instrument.venue) or [])
        symbol_info = dict(self._symbol_info_provider(signal) or {})
        size = self._size_fn(signal, account, symbol_info)
        if not size.approved or size.quantity is None:
            return self._reject(execution_id, f"sizing_rejected:{size.reason}")
        payload = ghost_executor_payload(signal)
        guardian_ok, guardian_reason = self._guardian(payload, provider_positions, account)
        if not guardian_ok:
            return self._reject(execution_id, f"guardian_rejected:{guardian_reason}")
        approval = self._approval(payload, account, provider_positions, symbol_info, size)
        if not bool(getattr(approval, "approved", False)):
            return self._reject(
                execution_id, f"risk_rejected:{getattr(approval, 'reason', 'unknown')}"
            )
        try:
            approved_volume = float(getattr(approval, "volume", 0) or 0)
        except (TypeError, ValueError):
            approved_volume = 0.0
        if approved_volume <= 0 or approved_volume > size.quantity + 1e-12:
            return self._reject(execution_id, "risk_approval_exceeds_ghost_size")
        result: DemoExecutionResult = adapter.submit(signal, approval)
        response = {
            "success": result.success,
            "status": result.status,
            "broker_order_id": result.broker_order_id,
            "broker_position_id": result.broker_position_id,
            "entry_price": result.entry_price,
            "raw": dict(result.raw_response),
        }
        self.repository.complete_execution(
            execution_id,
            status=result.status,
            response=response,
            error_code=result.error_code,
        )
        if (
            result.success
            and result.status == "SUBMITTED"
            and result.broker_position_id
        ):
            self.repository.open_demo_position(
                signal,
                execution_id=execution_id,
                broker_order_id=result.broker_order_id,
                broker_position_id=result.broker_position_id,
                entry_price=result.entry_price or float(signal.entry or 0),
                quantity=approved_volume,
                payload={"risk_fraction": self.config.risk_per_trade},
            )
        return CoordinatorResult(
            result.success,
            result.status,
            execution_id,
            result.error_code,
            result.broker_order_id,
            result.broker_position_id,
        )

    def _reject(self, execution_id: str, error_code: str) -> CoordinatorResult:
        self.repository.complete_execution(
            execution_id,
            status="REJECTED",
            response={"success": False, "error": error_code},
            error_code=error_code,
        )
        return CoordinatorResult(False, "REJECTED", execution_id, error_code)

    def close_position(self, position_id: str) -> CoordinatorResult:
        close_id = str(uuid.uuid4())
        position = self.repository.get_position(position_id)
        if position is None:
            return CoordinatorResult(False, "REJECTED", close_id, "position_not_found")
        if position.mode != "DEMO" or position.status not in {"OPEN", "RECONCILIATION_MISMATCH"}:
            return CoordinatorResult(False, "REJECTED", close_id, "position_not_closeable")
        adapter = self.adapters.get(position.venue)
        if adapter is None or not adapter.attest().verified:
            return CoordinatorResult(False, "REJECTED", close_id, "demo_not_verified")
        if not position.broker_position_id or not position.quantity:
            return CoordinatorResult(False, "REJECTED", close_id, "broker_position_identity_missing")
        result: DemoExecutionResult = adapter.close(
            position.broker_position_id,
            direction=position.direction.value,
            quantity=position.quantity,
        )
        if not result.success:
            return CoordinatorResult(
                False, result.status, close_id,
                result.error_code or "demo_close_rejected",
                result.broker_order_id, result.broker_position_id,
            )
        if result.entry_price is None:
            return CoordinatorResult(False, "RECONCILIATION_REQUIRED", close_id, "close_fill_price_unavailable")
        gross_r = (
            (result.entry_price - position.entry) / position.initial_risk
            if position.direction.value == "LONG"
            else (position.entry - result.entry_price) / position.initial_risk
        )
        self.repository.close_position(
            position,
            closed_at=self._clock(),
            exit_price=result.entry_price,
            gross_r=gross_r,
            exit_reason="MANUAL_CLOSE",
            payload={"broker": dict(result.raw_response), "close_id": close_id},
        )
        return CoordinatorResult(
            True, "CLOSED", close_id, None,
            result.broker_order_id, position.broker_position_id,
        )

    def close_all(self) -> list[CoordinatorResult]:
        return [
            self.close_position(position.position_id)
            for position in self.repository.list_positions()
            if position.mode == "DEMO"
            and position.status in {"OPEN", "RECONCILIATION_MISMATCH"}
        ]
