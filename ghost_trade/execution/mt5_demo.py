"""Fail-closed MT5 demo/contest adapter around Athena's shared executor."""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..models import DemoVerificationStatus, GhostSignal, Venue
from .base import (
    DemoAttestation,
    DemoExecutionResult,
    ghost_executor_payload,
    result_from_shared,
)


class MT5DemoAdapter:
    def __init__(
        self,
        client: Any,
        *,
        connect_fn: Callable[[], bool],
        execute_fn: Callable[[dict, Any], Any],
        close_fn: Callable[..., Any] | None = None,
        logger: logging.Logger | None = None,
    ):
        self._client = client
        self._connect = connect_fn
        self._execute = execute_fn
        self._close = close_fn
        self._log = logger or logging.getLogger("sentinel")

    def attest(self) -> DemoAttestation:
        if not self._connect():
            return DemoAttestation(
                Venue.MT5, False, DemoVerificationStatus.DEMO_NOT_VERIFIED,
                "unknown", "", "mt5_not_connected",
            )
        try:
            account = self._client.account_info()
        except Exception as exc:
            return DemoAttestation(
                Venue.MT5, False, DemoVerificationStatus.DEMO_NOT_VERIFIED,
                "unknown", "", f"mt5_account_unavailable:{exc}",
            )
        if account is None:
            return DemoAttestation(
                Venue.MT5, False, DemoVerificationStatus.DEMO_NOT_VERIFIED,
                "unknown", "", "mt5_account_unavailable",
            )
        mode = getattr(account, "trade_mode", None)
        demo_mode = getattr(self._client, "ACCOUNT_TRADE_MODE_DEMO", object())
        contest_mode = getattr(self._client, "ACCOUNT_TRADE_MODE_CONTEST", object())
        if mode == demo_mode:
            account_type, verified = "demo", True
        elif mode == contest_mode:
            account_type, verified = "contest", True
        elif mode == getattr(self._client, "ACCOUNT_TRADE_MODE_REAL", object()):
            account_type, verified = "real", False
        else:
            account_type, verified = "unknown", False
        server = str(getattr(account, "server", "") or "")
        self._log.info(
            "[GHOST][MT5] demo attestation server=%s account_type=%s verified=%s",
            server, account_type, verified,
        )
        return DemoAttestation(
            Venue.MT5,
            verified,
            DemoVerificationStatus.DEMO_VERIFIED if verified else DemoVerificationStatus.DEMO_NOT_VERIFIED,
            account_type,
            server,
            None if verified else "mt5_real_or_unknown_account",
        )

    def submit(self, signal: GhostSignal, approval: Any) -> DemoExecutionResult:
        attestation = self.attest()
        if not attestation.verified:
            return DemoExecutionResult(False, "REJECTED", "mt5_demo_not_verified")
        if signal.instrument.venue is not Venue.MT5:
            return DemoExecutionResult(False, "REJECTED", "venue_mismatch")
        if not signal.can_execute or not bool(getattr(approval, "approved", False)):
            return DemoExecutionResult(False, "REJECTED", "risk_not_approved")
        try:
            if float(getattr(approval, "volume", 0) or 0) <= 0:
                return DemoExecutionResult(False, "REJECTED", "invalid_approved_quantity")
            payload = ghost_executor_payload(signal)
        except (TypeError, ValueError) as exc:
            return DemoExecutionResult(False, "REJECTED", str(exc))
        return result_from_shared(
            self._execute(payload, approval), default_error="mt5_order_rejected"
        )

    def close(self, broker_position_id: str, *, direction: str, quantity: float) -> DemoExecutionResult:
        del direction
        if not self.attest().verified:
            return DemoExecutionResult(False, "REJECTED", "mt5_demo_not_verified")
        if self._close is None:
            return DemoExecutionResult(False, "REJECTED", "mt5_close_unavailable")
        try:
            ticket = int(broker_position_id)
        except (TypeError, ValueError):
            return DemoExecutionResult(False, "REJECTED", "invalid_mt5_position_id")
        return result_from_shared(
            self._close(ticket, volume=quantity), default_error="mt5_close_rejected"
        )
