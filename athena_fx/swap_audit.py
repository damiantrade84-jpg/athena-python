"""Phase 2 — broker swap audit for the athena_fx factor lane.

Reads MT5 symbol swap metadata, normalizes cash accrual into account currency,
and persists audit rows for carry-cost analysis. Research/backtest only.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Callable, Optional

from config import CONFIG

from athena_fx.models import (
    DIAG_MISSING_CONVERSION,
    DIAG_MISSING_SWAP,
    G8_CURRENCIES,
    G8_PAIRS,
    Diagnostic,
    SwapAuditRow,
    pair_legs,
)
from athena_fx import store as fx_store

log = logging.getLogger("athena_fx.swap_audit")

# MT5 SYMBOL_SWAP_MODE_* values (MetaTrader 5 documentation).
MT5_SWAP_MODES = {
    "DISABLED": 0,
    "POINTS": 1,
    "CURRENCY_SYMBOL": 2,
    "CURRENCY_MARGIN": 3,
    "CURRENCY_DEPOSIT": 4,
    "INTEREST_CURRENT": 5,
    "INTEREST_OPEN": 6,
    "REOPEN_CURRENT": 7,
    "REOPEN_BID": 8,
}
DISABLED = MT5_SWAP_MODES["DISABLED"]
POINTS = MT5_SWAP_MODES["POINTS"]
CURRENCY_SYMBOL = MT5_SWAP_MODES["CURRENCY_SYMBOL"]
CURRENCY_MARGIN = MT5_SWAP_MODES["CURRENCY_MARGIN"]
CURRENCY_DEPOSIT = MT5_SWAP_MODES["CURRENCY_DEPOSIT"]
INTEREST_CURRENT = MT5_SWAP_MODES["INTEREST_CURRENT"]
REOPEN_CURRENT = MT5_SWAP_MODES["REOPEN_CURRENT"]
REOPEN_BID = MT5_SWAP_MODES["REOPEN_BID"]


def _utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat()


def _lookup_conversion_rate(
    from_ccy: str,
    to_ccy: str,
    rates: dict[str, float] | None,
) -> Optional[float]:
    """Return multiplier to convert *from_ccy* notional into *to_ccy*."""
    src = from_ccy.upper()
    dst = to_ccy.upper()
    if src == dst:
        return 1.0
    if not rates:
        return None
    direct = f"{src}{dst}"
    if direct in rates:
        return float(rates[direct])
    inverse = f"{dst}{src}"
    if inverse in rates:
        inv = float(rates[inverse])
        return (1.0 / inv) if inv else None
    if src in rates:
        return float(rates[src])
    if dst in rates:
        return None  # ambiguous single-key lookup for destination
    return None


def g8_rate_symbol(from_ccy: str, to_ccy: str) -> Optional[str]:
    """Return a G8 pair symbol connecting *from_ccy* to *to_ccy* (either direction)."""
    src = from_ccy.upper()
    dst = to_ccy.upper()
    direct = f"{src}{dst}"
    if direct in G8_PAIRS:
        return direct
    inverse = f"{dst}{src}"
    if inverse in G8_PAIRS:
        return inverse
    return None


def _fetch_mt5_mid(display_symbol: str) -> Optional[float]:
    """Mid price for a 6-char FX symbol from MT5. Returns None when unavailable."""
    try:
        from mt5_executor import _get_mt5, mt5_connect, mt5_map_symbol

        mt5 = _get_mt5()
        if not mt5 or not mt5_connect():
            return None
        mt5_symbol = mt5_map_symbol(display_symbol.replace("/", "").upper())
        if not mt5_symbol:
            return None
        if not mt5.symbol_select(mt5_symbol, True):
            return None
        tick = mt5.symbol_info_tick(mt5_symbol)
        if tick is None:
            return None
        bid = float(getattr(tick, "bid", 0) or 0)
        ask = float(getattr(tick, "ask", 0) or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        last = float(getattr(tick, "last", 0) or 0)
        return last if last > 0 else None
    except Exception as exc:
        log.debug("_fetch_mt5_mid failed for %s: %s", display_symbol, exc)
        return None


def fetch_mt5_g8_conversion_rates(account_currency: str) -> dict[str, float]:
    """Live G8 FX mids vs *account_currency* for swap notional normalization."""
    account = str(account_currency or "").strip().upper()
    if not account:
        return {}
    rates: dict[str, float] = {}
    for ccy in G8_CURRENCIES:
        if ccy == account:
            continue
        sym = g8_rate_symbol(ccy, account)
        if not sym:
            continue
        mid = _fetch_mt5_mid(sym)
        if mid is not None and mid > 0:
            rates[sym] = mid
    return rates


_mt5_rates_cache: dict[str, dict[str, float]] = {}


def clear_mt5_conversion_rate_cache() -> None:
    """Test helper — reset cached MT5 conversion rates."""
    _mt5_rates_cache.clear()


def _default_conversion_rate_fn(_sym: str, meta: dict) -> dict[str, float] | None:
    account = str(meta.get("account_currency") or "").strip().upper()
    if not account:
        return None
    if account not in _mt5_rates_cache:
        _mt5_rates_cache[account] = fetch_mt5_g8_conversion_rates(account)
    cached = _mt5_rates_cache.get(account) or {}
    return cached or None


def fetch_mt5_swap_metadata(symbol: str) -> dict | None:
    """Read live MT5 swap metadata for *symbol*. Returns None when MT5 unavailable."""
    try:
        from mt5_executor import _get_mt5, mt5_connect, mt5_map_symbol

        mt5 = _get_mt5()
        if not mt5 or not mt5_connect():
            return None

        display = symbol.replace("/", "").upper()
        mt5_symbol = mt5_map_symbol(display)
        if not mt5_symbol:
            return None

        info = mt5.symbol_info(mt5_symbol)
        if info is None:
            if not mt5.symbol_select(mt5_symbol, True):
                return None
            info = mt5.symbol_info(mt5_symbol)
            if info is None:
                return None

        account = mt5.account_info()
        account_currency = str(getattr(account, "currency", "") or "") if account else ""

        return {
            "symbol": display,
            "base_currency": str(getattr(info, "currency_base", "") or ""),
            "quote_currency": str(getattr(info, "currency_profit", "") or ""),
            "account_currency": account_currency,
            "point": float(getattr(info, "point", 0) or 0),
            "trade_tick_value": float(getattr(info, "trade_tick_value", 0) or 0),
            "trade_tick_size": float(getattr(info, "trade_tick_size", 0) or 0),
            "trade_contract_size": float(getattr(info, "trade_contract_size", 0) or 0),
            "swap_mode": int(getattr(info, "swap_mode", 0) or 0),
            "swap_long": float(getattr(info, "swap_long", 0) or 0),
            "swap_short": float(getattr(info, "swap_short", 0) or 0),
            "swap_rollover3days": int(getattr(info, "swap_rollover3days", 3) or 3),
            "broker_timestamp": _utc_now_iso(),
        }
    except Exception as exc:
        log.debug("fetch_mt5_swap_metadata failed for %s: %s", symbol, exc)
        return None


def build_swap_audit_row(
    meta: dict,
    *,
    conversion_rates: dict[str, float] | None = None,
    theoretical_carry: float | None = None,
    generated_at: str | None = None,
) -> SwapAuditRow:
    """Pure swap-audit builder — fully testable without MT5."""
    diagnostics: list[Diagnostic] = []
    conversion_warning = False
    markup_warning = False

    symbol = str(meta.get("symbol", "")).upper()
    base_ccy = str(meta.get("base_currency", "") or "")
    quote_ccy = str(meta.get("quote_currency", "") or "")
    account_ccy = str(meta.get("account_currency", "") or "")
    point = float(meta.get("point", 0) or 0)
    trade_tick_value = float(meta.get("trade_tick_value", 0) or 0)
    trade_tick_size = float(meta.get("trade_tick_size", 0) or 0)
    trade_contract_size = float(meta.get("trade_contract_size", 0) or 0)
    swap_mode = int(meta.get("swap_mode", 0) or 0)
    swap_long = float(meta.get("swap_long", 0) or 0)
    swap_short = float(meta.get("swap_short", 0) or 0)
    swap_rollover3days = int(meta.get("swap_rollover3days", 3) or 3)
    broker_timestamp = meta.get("broker_timestamp")

    gen_at = generated_at or _utc_now_iso()
    status = "ok"

    carry_min = float(CONFIG.get("FX_FACTOR_CARRY_MIN_NET_ANNUAL", 0.005) or 0.005)
    markup_warn = float(CONFIG.get("FX_FACTOR_MARKUP_WARN_ANNUAL", 0.03) or 0.03)

    point_value_per_lot: Optional[float] = None
    swap_cash_long: Optional[float] = None
    swap_cash_short: Optional[float] = None
    weekly_long: Optional[float] = None
    weekly_short: Optional[float] = None
    notional: Optional[float] = None
    daily_long: Optional[float] = None
    daily_short: Optional[float] = None
    ann_long: Optional[float] = None
    ann_short: Optional[float] = None
    broker_markup_long: Optional[float] = None
    broker_markup_short: Optional[float] = None
    carry_long_eligible = False
    carry_short_eligible = False

    if swap_mode in (REOPEN_CURRENT, REOPEN_BID) or swap_mode not in (
        DISABLED, POINTS, CURRENCY_SYMBOL, CURRENCY_MARGIN, CURRENCY_DEPOSIT, INTEREST_CURRENT,
    ):
        diagnostics.append(Diagnostic(
            "unsupported_swap_mode", "error",
            f"swap mode {swap_mode} is not supported for audit normalization",
            {"symbol": symbol, "swap_mode": swap_mode},
        ))
        status = "invalid"
        return SwapAuditRow(
            symbol=symbol,
            base_currency=base_ccy,
            quote_currency=quote_ccy,
            account_currency=account_ccy,
            point=point,
            trade_tick_value=trade_tick_value,
            trade_tick_size=trade_tick_size,
            trade_contract_size=trade_contract_size,
            swap_mode=swap_mode,
            swap_long=swap_long,
            swap_short=swap_short,
            swap_rollover3days=swap_rollover3days,
            theoretical_carry=theoretical_carry,
            broker_timestamp=broker_timestamp,
            generated_at=gen_at,
            status=status,
            diagnostics=diagnostics,
        )

    if swap_mode == DISABLED:
        diagnostics.append(Diagnostic(
            "swap_disabled", "info", "broker swap accrual is disabled for symbol",
            {"symbol": symbol},
        ))
        return SwapAuditRow(
            symbol=symbol,
            base_currency=base_ccy,
            quote_currency=quote_ccy,
            account_currency=account_ccy,
            point=point,
            trade_tick_value=trade_tick_value,
            trade_tick_size=trade_tick_size,
            trade_contract_size=trade_contract_size,
            swap_mode=swap_mode,
            swap_long=0.0,
            swap_short=0.0,
            swap_rollover3days=swap_rollover3days,
            point_value_per_lot=0.0,
            swap_cash_long_per_lot=0.0,
            swap_cash_short_per_lot=0.0,
            weekly_swap_cash_long_per_lot=0.0,
            weekly_swap_cash_short_per_lot=0.0,
            notional_value_account_ccy=0.0,
            daily_swap_return_long=0.0,
            daily_swap_return_short=0.0,
            annualized_swap_return_long=0.0,
            annualized_swap_return_short=0.0,
            theoretical_carry=theoretical_carry,
            carry_long_eligible=False,
            carry_short_eligible=False,
            net_carry_long_annualized=0.0,
            net_carry_short_annualized=0.0,
            broker_timestamp=broker_timestamp,
            generated_at=gen_at,
            status="ok",
            diagnostics=diagnostics,
        )

    base_to_account = _lookup_conversion_rate(base_ccy, account_ccy, conversion_rates)
    if base_to_account is None:
        conversion_warning = True
        diagnostics.append(Diagnostic(
            DIAG_MISSING_CONVERSION, "warning",
            f"missing conversion rate from {base_ccy} to {account_ccy}",
            {"symbol": symbol, "from": base_ccy, "to": account_ccy},
        ))
        status = "invalid"
    else:
        notional = trade_contract_size * base_to_account

    if swap_mode == POINTS:
        if trade_tick_size:
            point_value_per_lot = trade_tick_value * (point / trade_tick_size)
        else:
            point_value_per_lot = 0.0
        if conversion_warning:
            swap_cash_long = None
            swap_cash_short = None
        else:
            swap_cash_long = swap_long * (point_value_per_lot or 0.0)
            swap_cash_short = swap_short * (point_value_per_lot or 0.0)

    elif swap_mode == CURRENCY_DEPOSIT:
        if conversion_warning:
            swap_cash_long = None
            swap_cash_short = None
        else:
            swap_cash_long = swap_long
            swap_cash_short = swap_short

    elif swap_mode in (CURRENCY_SYMBOL, CURRENCY_MARGIN):
        cash_ccy = base_ccy if swap_mode == CURRENCY_SYMBOL else str(
            meta.get("currency_margin", base_ccy) or base_ccy
        )
        cash_to_account = _lookup_conversion_rate(cash_ccy, account_ccy, conversion_rates)
        if cash_to_account is None:
            conversion_warning = True
            diagnostics.append(Diagnostic(
                DIAG_MISSING_CONVERSION, "warning",
                f"missing conversion rate from {cash_ccy} to {account_ccy}",
                {"symbol": symbol, "from": cash_ccy, "to": account_ccy},
            ))
            status = "invalid"
            swap_cash_long = None
            swap_cash_short = None
        else:
            swap_cash_long = swap_long * cash_to_account
            swap_cash_short = swap_short * cash_to_account

    elif swap_mode == INTEREST_CURRENT:
        if conversion_warning:
            daily_long = None
            daily_short = None
        else:
            daily_long = swap_long / 100.0 / 360.0
            daily_short = swap_short / 100.0 / 360.0
        ann_long = (daily_long * 365) if daily_long is not None else None
        ann_short = (daily_short * 365) if daily_short is not None else None

    if swap_mode != INTEREST_CURRENT and not conversion_warning:
        swap_cash_long = swap_cash_long if swap_cash_long is not None else 0.0
        swap_cash_short = swap_cash_short if swap_cash_short is not None else 0.0
        weekly_long = swap_cash_long * 7.0
        weekly_short = swap_cash_short * 7.0
        if notional and notional > 0:
            daily_long = swap_cash_long / notional
            daily_short = swap_cash_short / notional
            adjusted_daily_long = weekly_long / 7.0 / notional
            adjusted_daily_short = weekly_short / 7.0 / notional
            ann_long = adjusted_daily_long * 365.0
            ann_short = adjusted_daily_short * 365.0
        else:
            daily_long = None
            daily_short = None
            ann_long = None
            ann_short = None

    if theoretical_carry is not None and ann_long is not None:
        broker_markup_long = theoretical_carry - ann_long
        if abs(broker_markup_long) > markup_warn:
            markup_warning = True
    if theoretical_carry is not None and ann_short is not None:
        broker_markup_short = (-theoretical_carry) - ann_short
        if abs(broker_markup_short) > markup_warn:
            markup_warning = True

    if ann_long is not None:
        carry_long_eligible = ann_long > carry_min
    if ann_short is not None:
        carry_short_eligible = ann_short > carry_min

    return SwapAuditRow(
        symbol=symbol,
        base_currency=base_ccy,
        quote_currency=quote_ccy,
        account_currency=account_ccy,
        point=point,
        trade_tick_value=trade_tick_value,
        trade_tick_size=trade_tick_size,
        trade_contract_size=trade_contract_size,
        swap_mode=swap_mode,
        swap_long=swap_long,
        swap_short=swap_short,
        swap_rollover3days=swap_rollover3days,
        point_value_per_lot=point_value_per_lot,
        swap_cash_long_per_lot=swap_cash_long,
        swap_cash_short_per_lot=swap_cash_short,
        weekly_swap_cash_long_per_lot=weekly_long,
        weekly_swap_cash_short_per_lot=weekly_short,
        notional_value_account_ccy=notional,
        daily_swap_return_long=daily_long,
        daily_swap_return_short=daily_short,
        annualized_swap_return_long=ann_long,
        annualized_swap_return_short=ann_short,
        theoretical_carry=theoretical_carry,
        broker_markup_long=broker_markup_long,
        broker_markup_short=broker_markup_short,
        carry_long_eligible=carry_long_eligible,
        carry_short_eligible=carry_short_eligible,
        net_carry_long_annualized=ann_long,
        net_carry_short_annualized=ann_short,
        markup_warning=markup_warning,
        conversion_warning=conversion_warning,
        stale_swap_warning=False,
        broker_timestamp=broker_timestamp,
        generated_at=gen_at,
        status=status,
        diagnostics=diagnostics,
    )


def _default_theoretical_carry(symbol: str) -> Optional[float]:
    try:
        from carry_feed import get_carry_differential

        base, quote = pair_legs(symbol)
        display = f"{base}/{quote}"
        val = get_carry_differential(display)
        if val is None:
            return None
        return float(val) / 100.0
    except Exception:
        return None


def run_swap_audit(
    symbols: list[str],
    *,
    theoretical_carry_fn: Callable[[str], Optional[float]] | None = None,
    conversion_rate_fn: Callable[[str, dict], dict[str, float] | None] | None = None,
    fetch_fn: Callable[[str], dict | None] | None = None,
    db_path: str | None = None,
) -> list[SwapAuditRow]:
    """Fetch MT5 swap metadata for each symbol, build audit rows, and persist."""
    fetch = fetch_fn or fetch_mt5_swap_metadata
    carry_fn = theoretical_carry_fn or _default_theoretical_carry
    conv_fn = (
        conversion_rate_fn
        if conversion_rate_fn is not None
        else _default_conversion_rate_fn
    )
    rows: list[SwapAuditRow] = []

    for sym in symbols:
        meta = fetch(sym)
        if meta is None:
            rows.append(SwapAuditRow(
                symbol=sym.upper(),
                base_currency="",
                quote_currency="",
                account_currency="",
                point=0.0,
                trade_tick_value=0.0,
                trade_tick_size=0.0,
                trade_contract_size=0.0,
                swap_mode=0,
                swap_long=0.0,
                swap_short=0.0,
                swap_rollover3days=3,
                generated_at=_utc_now_iso(),
                status="invalid",
                diagnostics=[Diagnostic(
                    DIAG_MISSING_SWAP, "error",
                    "MT5 swap metadata unavailable",
                    {"symbol": sym},
                )],
            ))
            continue

        rates = conv_fn(sym, meta)
        theoretical = carry_fn(sym)
        rows.append(build_swap_audit_row(
            meta,
            conversion_rates=rates,
            theoretical_carry=theoretical,
        ))

    fx_store.save_swap_audit_rows(rows, db_path=db_path)
    return rows
