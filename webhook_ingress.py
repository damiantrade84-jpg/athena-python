"""TradingView / external webhook payload validation (ingress only, no execution)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from pydantic import BaseModel, Field, ValidationError, field_validator
except ImportError:  # pragma: no cover - pydantic is a transitive dep in production venv
    BaseModel = None  # type: ignore[misc, assignment]
    Field = None  # type: ignore[misc, assignment]
    ValidationError = Exception  # type: ignore[misc, assignment]
    field_validator = None  # type: ignore[misc, assignment]


_REQUIRED_FIELDS_ERROR = "Missing required fields: pair, direction, price, sl, tp1"
_VALID_DIRECTIONS = frozenset({"LONG", "SHORT"})


if BaseModel is not None:

    class WebhookPayloadModel(BaseModel):
        pair: str = ""
        direction: str | None = None
        side: str | None = None
        type: str = ""
        price: float | str | int | None = None
        entry: float | str | int | None = None
        sl: float | str | int | None = None
        stop: float | str | int | None = None
        tp1: float | str | int | None = None
        tp: float | str | int | None = None
        tp2: float | str | int = 0
        tp_partial: float | str | int | None = None
        score: float | str | int = 0.5
        maxScore: float | str | int = 3.0
        symbol: str | None = None
        trendState: str = "DEVELOPING"
        secret: str = ""

        model_config = {"extra": "ignore"}

        @field_validator("pair")
        @classmethod
        def _strip_pair(cls, v: str) -> str:
            return str(v or "").strip()

        @field_validator("direction", "side", mode="before")
        @classmethod
        def _norm_direction(cls, v: Any) -> str | None:
            if v is None:
                return None
            return str(v).upper().strip()


def validate_webhook_secret(payload: dict[str, Any], expected_secret: str) -> str | None:
    """Return error message when secret is configured and payload secret mismatches."""
    if not expected_secret:
        return None
    if payload.get("secret", "") != expected_secret:
        return "Unauthorized - invalid webhook secret"
    return None


def _build_signal_from_fields(
    *,
    pair_name: str,
    direction: str,
    sig_type: Any,
    price_f: float,
    sl_f: float,
    tp1_f: float,
    tp2_f: float,
    tp_partial_f: float | None,
    score_f: float,
    max_score_f: float,
    symbol: str | None,
    trend_state: str,
) -> dict[str, Any]:
    return {
        "pair": pair_name,
        "display": pair_name,
        "symbol": symbol or pair_name.replace("/", ""),
        "type": sig_type,
        "direction": direction,
        "price": price_f,
        "sl": sl_f,
        "tp1": tp1_f,
        "tp2": tp2_f,
        "tp_partial": tp_partial_f,
        "confluenceScore": score_f,
        "maxScore": max_score_f,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trendState": trend_state,
    }


def _parse_with_pydantic(data: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        model = WebhookPayloadModel.model_validate(data)
    except ValidationError:
        return None, "Invalid webhook payload structure"

    direction = model.direction or model.side or ""
    if direction not in _VALID_DIRECTIONS:
        return None, _REQUIRED_FIELDS_ERROR

    price_raw = model.price if model.price is not None else model.entry
    sl_raw = model.sl if model.sl is not None else model.stop
    tp1_raw = model.tp1 if model.tp1 is not None else model.tp
    if not model.pair or not price_raw or not sl_raw or not tp1_raw:
        return None, _REQUIRED_FIELDS_ERROR

    try:
        price_f = float(price_raw)
        sl_f = float(sl_raw)
        tp1_f = float(tp1_raw)
        tp2_f = float(model.tp2) if model.tp2 else tp1_f
        tp_partial_f = float(model.tp_partial) if model.tp_partial else None
        score_f = float(model.score)
        max_score_f = float(model.maxScore)
    except (TypeError, ValueError):
        return None, "Invalid numeric fields in webhook payload"

    if price_f <= 0 or sl_f <= 0 or tp1_f <= 0:
        return None, _REQUIRED_FIELDS_ERROR

    return (
        _build_signal_from_fields(
            pair_name=model.pair,
            direction=direction,
            sig_type=model.type,
            price_f=price_f,
            sl_f=sl_f,
            tp1_f=tp1_f,
            tp2_f=tp2_f,
            tp_partial_f=tp_partial_f,
            score_f=score_f,
            max_score_f=max_score_f,
            symbol=model.symbol,
            trend_state=model.trendState,
        ),
        None,
    )


def parse_webhook_payload(payload: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
    """Normalize a webhook JSON body into a signal dict.

    Returns (signal, error_message). error_message is set for malformed or incomplete payloads.
    """
    data = payload if isinstance(payload, dict) else {}
    if BaseModel is not None:
        return _parse_with_pydantic(data)

    pair_name = str(data.get("pair") or "").strip()
    direction = str(data.get("direction") or data.get("side") or "").upper()
    sig_type = data.get("type", "")
    price = data.get("price") or data.get("entry") or 0
    sl = data.get("sl") or data.get("stop") or 0
    tp1 = data.get("tp1") or data.get("tp") or 0
    tp2 = data.get("tp2", 0)
    tp_partial = data.get("tp_partial", 0)

    if not pair_name or direction not in _VALID_DIRECTIONS or not price or not sl or not tp1:
        return None, _REQUIRED_FIELDS_ERROR

    try:
        price_f = float(price)
        sl_f = float(sl)
        tp1_f = float(tp1)
        tp2_f = float(tp2) if tp2 else tp1_f
        tp_partial_f = float(tp_partial) if tp_partial else None
        score_f = float(data.get("score", 0.5))
        max_score_f = float(data.get("maxScore", 3.0))
    except (TypeError, ValueError):
        return None, "Invalid numeric fields in webhook payload"

    if price_f <= 0 or sl_f <= 0 or tp1_f <= 0:
        return None, _REQUIRED_FIELDS_ERROR

    return (
        _build_signal_from_fields(
            pair_name=pair_name,
            direction=direction,
            sig_type=sig_type,
            price_f=price_f,
            sl_f=sl_f,
            tp1_f=tp1_f,
            tp2_f=tp2_f,
            tp_partial_f=tp_partial_f,
            score_f=score_f,
            max_score_f=max_score_f,
            symbol=data.get("symbol"),
            trend_state=data.get("trendState", "DEVELOPING"),
        ),
        None,
    )


def webhook_duplicate_key(pair: str, direction: str, *, minute_bucket: str | None = None) -> str:
    """Minute-bucket dedup key shared with /api/webhook."""
    bucket = minute_bucket or datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    return f"wh_{pair}_{direction}_{bucket}"
