"""Fail-closed Bybit demo/testnet adapter around Athena's shared executor."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse

from ..models import DemoVerificationStatus, GhostSignal, Venue
from .base import (
    DemoAttestation,
    DemoExecutionResult,
    ghost_executor_payload,
    result_from_shared,
)


def _urls(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for nested in value.values():
            yield from _urls(nested)
    elif isinstance(value, (tuple, list, set)):
        for nested in value:
            yield from _urls(nested)
    elif isinstance(value, str):
        yield value


class BybitDemoAdapter:
    def __init__(
        self,
        exchange: Any,
        *,
        execute_fn: Callable[[dict, Any], Any],
        close_fn: Callable[..., Any] | None = None,
    ):
        self._exchange = exchange
        self._execute = execute_fn
        self._close = close_fn

    def attest(self) -> DemoAttestation:
        if self._exchange is None:
            return DemoAttestation(
                Venue.BYBIT, False, DemoVerificationStatus.DEMO_NOT_VERIFIED,
                "unknown", "", "bybit_client_unavailable",
            )
        api_urls = list(_urls(getattr(self._exchange, "urls", {}).get("api", {})))
        hosts = {urlparse(url).hostname or "" for url in api_urls}
        production_present = "api.bybit.com" in hosts
        demo_present = "api-demo.bybit.com" in hosts
        testnet_present = "api-testnet.bybit.com" in hosts
        options = getattr(self._exchange, "options", {})
        demo_flag = isinstance(options, Mapping) and options.get("enableDemoTrading") is True
        demo_verified = demo_present and demo_flag and not production_present
        testnet_verified = testnet_present and not production_present
        verified = demo_verified or testnet_verified
        account_type = "demo" if demo_verified else "testnet" if testnet_verified else "unknown"
        return DemoAttestation(
            Venue.BYBIT,
            verified,
            DemoVerificationStatus.DEMO_VERIFIED if verified else DemoVerificationStatus.DEMO_NOT_VERIFIED,
            account_type,
            ",".join(sorted(hosts)),
            None if verified else "bybit_production_or_unknown_endpoint",
        )

    def submit(self, signal: GhostSignal, approval: Any) -> DemoExecutionResult:
        if not self.attest().verified:
            return DemoExecutionResult(False, "REJECTED", "bybit_demo_not_verified")
        if signal.instrument.venue is not Venue.BYBIT:
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
            self._execute(payload, approval), default_error="bybit_order_rejected"
        )

    def close(self, broker_position_id: str, *, direction: str, quantity: float) -> DemoExecutionResult:
        if not self.attest().verified:
            return DemoExecutionResult(False, "REJECTED", "bybit_demo_not_verified")
        if self._close is None:
            return DemoExecutionResult(False, "REJECTED", "bybit_close_unavailable")
        return result_from_shared(
            self._close(broker_position_id, direction, quantity),
            default_error="bybit_close_rejected",
        )
