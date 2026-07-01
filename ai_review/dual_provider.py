"""ai_review.dual_provider — parallel dual-provider reconciler for chart review.

Phase 0 of the Athena AI Edge Program. Calls two providers in parallel and
reconciles their responses. Config-gated by `ALLOW_DUAL_PROVIDER` (existing)
and `AI_DUAL_PROVIDER_PRIMARY` / `AI_DUAL_PROVIDER_SECONDARY`.

Reconciliation rules:
- If both providers succeed and agree on `decision`, return the primary
  response with `dualProviderAgreement: true`.
- If both succeed but disagree, return the primary response with
  `dualProviderAgreement: false` and `secondaryDecision` attached. Primary
  wins on disagreement (configurable tie-breaker via
  `AI_DUAL_PROVIDER_TIEBREAKER`: "primary" | "secondary" | "fail_closed").
- If only one succeeds, return that one with `dualProviderFallback: true`.
- If both fail, raise the primary's ProviderChartReviewError.

Safety:
- Advisory-only. Reconciler never upgrades execution permission.
- If either provider returns `entryAllowedNow: true` and the other returns
  `false`, the reconciler uses the MORE CONSERVATIVE value (false). This
  matches the system-wide fail-closed / downgrade-only policy.
- Never bypasses deterministic gates.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any

from ai_review.provider_meta import ProviderChartReviewError

log = logging.getLogger("sentinel.ai_review.dual")


def _extract_decision(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    return str(response.get("decision") or response.get("verdict") or "").strip().lower() or None


def _extract_entry_allowed(response: Any) -> bool | None:
    if not isinstance(response, dict):
        return None
    val = response.get("entryAllowedNow")
    if val is None:
        # Fall back to legacy verdict field
        verdict = str(response.get("verdict") or "").upper()
        if verdict in ("VALID", "CONFIRM"):
            return True
        if verdict in ("INVALID", "NO_TRADE", "CAUTION"):
            return False
        return None
    return bool(val)


def _reconcile_agreement(
    primary: dict[str, Any],
    secondary: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Return (reconciled_payload, agreement_bool)."""
    p_dec = _extract_decision(primary)
    s_dec = _extract_decision(secondary)
    agree = (p_dec is not None and s_dec is not None and p_dec == s_dec)
    return primary, agree


def _apply_conservative_entry_allowed(
    primary: dict[str, Any],
    secondary: dict[str, Any],
) -> dict[str, Any]:
    """When entryAllowedNow differs, prefer the more conservative (False).

    Advisory-only: never upgrades execution permission. If either provider
    says no, the reconciled response says no.
    """
    p = _extract_entry_allowed(primary)
    s = _extract_entry_allowed(secondary)
    if p is None and s is None:
        return primary
    conservative = False if (p is False or s is False) else True
    if not conservative and primary.get("entryAllowedNow") is True:
        primary = dict(primary)
        primary["entryAllowedNow"] = False
        primary["dualProviderDowngradedEntryAllowed"] = True
    return primary


def reconcile_dual_provider(
    *,
    primary_response: dict[str, Any] | None,
    primary_error: Exception | None,
    secondary_response: dict[str, Any] | None,
    secondary_error: Exception | None,
    primary_name: str,
    secondary_name: str,
    tiebreaker: str = "primary",
) -> dict[str, Any]:
    """Reconcile parallel dual-provider results.

    See module docstring for rules. Never raises except when both providers
    fail (in which case the primary's error is re-raised).
    """
    if primary_response is None and secondary_response is None:
        # Both failed — re-raise the primary error (or a generic error)
        err = primary_error or secondary_error or RuntimeError("both providers failed")
        if isinstance(err, ProviderChartReviewError):
            raise err
        raise err

    if primary_response is not None and secondary_response is not None:
        reconciled, agree = _reconcile_agreement(primary_response, secondary_response)
        reconciled = _apply_conservative_entry_allowed(primary_response, secondary_response)
        reconciled["dualProvider"] = True
        reconciled["dualProviderPrimary"] = primary_name
        reconciled["dualProviderSecondary"] = secondary_name
        reconciled["dualProviderAgreement"] = agree
        if not agree:
            reconciled["secondaryDecision"] = _extract_decision(secondary_response)
        # If tiebreaker is "secondary" and they disagree, swap to secondary
        if not agree and tiebreaker == "secondary":
            reconciled = dict(secondary_response)
            reconciled["dualProvider"] = True
            reconciled["dualProviderPrimary"] = primary_name
            reconciled["dualProviderSecondary"] = secondary_name
            reconciled["dualProviderAgreement"] = False
            reconciled["dualProviderTiebreakerUsed"] = "secondary"
            reconciled = _apply_conservative_entry_allowed(reconciled, primary_response)
        elif not agree and tiebreaker == "fail_closed":
            # Refuse to pick — return a fail-closed CAUTION/INVALID response
            reconciled = {
                "decision": "NO_TRADE",
                "entryAllowedNow": False,
                "dualProvider": True,
                "dualProviderPrimary": primary_name,
                "dualProviderSecondary": secondary_name,
                "dualProviderAgreement": False,
                "dualProviderTiebreakerUsed": "fail_closed",
                "dualProviderFailClosedReason": "providers_disagree_and_tiebreaker_is_fail_closed",
                "primaryDecision": _extract_decision(primary_response),
                "secondaryDecision": _extract_decision(secondary_response),
            }
        return reconciled

    # Only one succeeded
    if primary_response is not None:
        out = dict(primary_response)
        out["dualProvider"] = True
        out["dualProviderPrimary"] = primary_name
        out["dualProviderSecondary"] = secondary_name
        out["dualProviderFallback"] = True
        if secondary_error is not None:
            out["dualProviderSecondaryError"] = str(secondary_error)
        return out

    # Only secondary succeeded
    out = dict(secondary_response)  # type: ignore[arg-type]
    out["dualProvider"] = True
    out["dualProviderPrimary"] = primary_name
    out["dualProviderSecondary"] = secondary_name
    out["dualProviderFallback"] = True
    out["dualProviderPrimaryFailed"] = True
    if primary_error is not None:
        out["dualProviderPrimaryError"] = str(primary_error)
    return out


def run_dual_provider(
    *,
    primary_fn,
    secondary_fn,
    primary_name: str,
    secondary_name: str,
    tiebreaker: str = "primary",
    timeout_sec: float = 60.0,
) -> dict[str, Any]:
    """Run two provider callables in parallel and reconcile.

    Each callable takes no arguments and returns a dict[str, Any] (the
    provider response). Exceptions are caught and routed to the reconciler.
    """
    with ThreadPoolExecutor(max_workers=2) as ex:
        p_future = ex.submit(primary_fn)
        s_future = ex.submit(secondary_fn)
        try:
            primary_response = p_future.result(timeout=timeout_sec)
            primary_error: Exception | None = None
        except Exception as exc:
            primary_response = None
            primary_error = exc
        try:
            secondary_response = s_future.result(timeout=timeout_sec)
            secondary_error: Exception | None = None
        except Exception as exc:
            secondary_response = None
            secondary_error = exc
    return reconcile_dual_provider(
        primary_response=primary_response,
        primary_error=primary_error,
        secondary_response=secondary_response,
        secondary_error=secondary_error,
        primary_name=primary_name,
        secondary_name=secondary_name,
        tiebreaker=tiebreaker,
    )
