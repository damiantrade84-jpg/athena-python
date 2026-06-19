from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable

from athena_research.commodity_data_audit.metadata_policy import (
    sanitize_broker_metadata,
    sanitize_symbol_metadata,
)


class Mt5ReadError(RuntimeError):
    """MT5 read-only fetch failure."""


_MT5_TIMEFRAME: dict[str, str] = {
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


def import_mt5_module():
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise Mt5ReadError(
            "MetaTrader5 package not installed; use Python 3.13+ environment with MT5 terminal"
        ) from exc
    return mt5


def connect_mt5_readonly(*, terminal_path: str | None = None) -> Any:
    """Initialize MT5 for market-data reads only. Does not place orders."""
    mt5 = import_mt5_module()
    kwargs: dict[str, Any] = {}
    path = (terminal_path or os.environ.get("MT5_PATH", "")).strip()
    if path:
        if not os.path.exists(path):
            raise Mt5ReadError(f"MT5_PATH does not exist: {path}")
        kwargs["path"] = path
    if not mt5.initialize(**kwargs):
        code, message = mt5.last_error()
        raise Mt5ReadError(f"MT5 initialize failed: ({code}) {message}")
    return mt5


def shutdown_mt5(mt5: Any) -> None:
    try:
        mt5.shutdown()
    except Exception:
        pass


def mt5_timeframe_constant(mt5: Any, timeframe: str) -> int:
    key = _MT5_TIMEFRAME.get(timeframe.upper())
    if not key or not hasattr(mt5, key):
        raise Mt5ReadError(f"unsupported MT5 timeframe: {timeframe}")
    return int(getattr(mt5, key))


def rate_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "time": int(row["time"]),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "tick_volume": int(row["tick_volume"]),
        "spread": int(row["spread"]),
        "real_volume": int(row["real_volume"]),
    }


def copy_rates_range_utc(
    mt5: Any,
    terminal_symbol: str,
    timeframe: str,
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict[str, Any]]:
    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=timezone.utc)
    else:
        start_utc = start_utc.astimezone(timezone.utc)
    if end_utc.tzinfo is None:
        end_utc = end_utc.replace(tzinfo=timezone.utc)
    else:
        end_utc = end_utc.astimezone(timezone.utc)

    if not mt5.symbol_select(terminal_symbol, True):
        code, message = mt5.last_error()
        raise Mt5ReadError(f"symbol_select failed for {terminal_symbol}: ({code}) {message}")

    tf = mt5_timeframe_constant(mt5, timeframe)
    rates = mt5.copy_rates_range(terminal_symbol, tf, start_utc, end_utc)
    if rates is None:
        code, message = mt5.last_error()
        raise Mt5ReadError(
            f"copy_rates_range failed for {terminal_symbol} {timeframe}: ({code}) {message}"
        )
    return [rate_row_to_dict(row) for row in rates]


CopyRatesFn = Callable[[str, str, datetime, datetime], list[dict[str, Any]]]


def fetch_symbol_metadata(mt5: Any, terminal_symbol: str) -> dict[str, Any]:
    if not mt5.symbol_select(terminal_symbol, True):
        code, message = mt5.last_error()
        raise Mt5ReadError(f"symbol_select failed for {terminal_symbol}: ({code}) {message}")
    info = mt5.symbol_info(terminal_symbol)
    if info is None:
        code, message = mt5.last_error()
        raise Mt5ReadError(f"symbol_info failed for {terminal_symbol}: ({code}) {message}")

    sessions: list[dict[str, Any]] = []
    if hasattr(mt5, "symbol_info_session"):
        try:
            session_rows = mt5.symbol_info_session(terminal_symbol, mt5.DAY_OF_WEEK_MONDAY)
            if session_rows is not None:
                for row in session_rows:
                    sessions.append(
                        {
                            "from": int(getattr(row, "from", 0) or row[0]),
                            "to": int(getattr(row, "to", 0) or row[1]),
                        }
                    )
        except Exception:
            sessions = []

    return sanitize_symbol_metadata(
        {
            "terminal_symbol": terminal_symbol,
            "digits": int(getattr(info, "digits", 0) or 0),
            "point": float(getattr(info, "point", 0) or 0),
            "trade_contract_size": float(getattr(info, "trade_contract_size", 0) or 0),
            "currency_base": str(getattr(info, "currency_base", "") or ""),
            "currency_profit": str(getattr(info, "currency_profit", "") or ""),
            "currency_margin": str(getattr(info, "currency_margin", "") or ""),
            "swap_long": float(getattr(info, "swap_long", 0) or 0),
            "swap_short": float(getattr(info, "swap_short", 0) or 0),
            "swap_mode": int(getattr(info, "swap_mode", 0) or 0),
            "session_sample_monday": sessions,
            "metadata_note": "swap values are current terminal metadata only, not historical financing",
        }
    )


def broker_identity(mt5: Any) -> dict[str, Any]:
    terminal = mt5.terminal_info()
    account = mt5.account_info()
    raw: dict[str, Any] = {
        "terminal_name": getattr(terminal, "name", None) if terminal else None,
        "terminal_company": getattr(terminal, "company", None) if terminal else None,
        "terminal_path": getattr(terminal, "path", None) if terminal else None,
        "terminal_build": getattr(terminal, "build", None) if terminal else None,
    }
    if account:
        raw.update(
            {
                "account_login": int(getattr(account, "login", 0) or 0),
                "account_server": str(getattr(account, "server", "") or ""),
                "account_currency": str(getattr(account, "currency", "") or ""),
                "balance": float(getattr(account, "balance", 0) or 0),
                "equity": float(getattr(account, "equity", 0) or 0),
                "margin": float(getattr(account, "margin", 0) or 0),
                "account_name": str(getattr(account, "name", "") or ""),
            }
        )
    return sanitize_broker_metadata(raw)
