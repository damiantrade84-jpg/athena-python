"""Ingest fresh confirmed bars directly from the MT5 terminal into PTIS.

The backtest-cache ingest (`mt5.py`) only sees bars as of the last cache
refresh; this source tops up the same `MT5:` series from the live terminal so
PTIS can pass the execution freshness gate. Forming bars are never written —
PTIS stores confirmed bars only.
"""

from __future__ import annotations

import logging
import time

from athena_ase.data.availability import AvailabilityRuleId
from athena_ase.data.ingest.common import append_ptis_rows, price_series_id, row_from_rule
from athena_ase.data.ptis import PTISStore
from athena_ase.universe import UNIVERSE, Instrument
from athena_app.services.mt5_time_alignment import infer_mt5_rates_time_shift_seconds

log = logging.getLogger("ase.ingest.mt5_live")

_FIELDS = ("close", "open", "high", "low", "volume")
_TIMEFRAMES = ("H1", "H4", "D1")
_TF_SECONDS = {"H1": 3600, "H4": 14400, "D1": 86400}
# Enough history to warm sigma EWMAs for series with no backtest-cache backfill.
_BAR_COUNTS = {"H1": 1500, "H4": 600, "D1": 400}


def _broker_offset_hours() -> int:
    try:
        from config import CONFIG

        return int(CONFIG.get("MT5_BROKER_UTC_OFFSET", 3) or 3)
    except Exception:
        return 3


def _connect_mt5():
    """Return the MetaTrader5 module with an initialized terminal, or None."""
    try:
        from mt5_executor import mt5_connect
    except ImportError:
        log.warning("mt5_executor unavailable; mt5_live ingest skipped")
        return None
    if not mt5_connect():
        log.warning("MT5 terminal not connected; mt5_live ingest skipped")
        return None
    import MetaTrader5 as mt5

    return mt5


def ingest_instrument_tf(
    store: PTISStore,
    mt5,
    inst: Instrument,
    mt5_symbol: str,
    tf: str,
    *,
    broker_offset_hours: int,
    now_s: float | None = None,
) -> dict[str, int]:
    tf_const = {"H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1}[tf]
    rates = mt5.copy_rates_from_pos(mt5_symbol, tf_const, 0, _BAR_COUNTS[tf])
    if rates is None or len(rates) == 0:
        return {}
    now = float(now_s if now_s is not None else time.time())

    tick = mt5.symbol_info_tick(mt5_symbol)
    tick_epoch = float(tick.time) if tick is not None and getattr(tick, "time", 0) else None
    shift_s, _reason = infer_mt5_rates_time_shift_seconds(
        tf,
        now,
        float(rates[-1]["time"]),
        broker_offset_hours,
        tick_epoch,
    )

    tf_sec = _TF_SECONDS[tf]
    field_rows: dict[str, list[dict]] = {field: [] for field in _FIELDS}
    for bar in rates:
        open_s = int(bar["time"]) - int(shift_s)
        close_ms = (open_s + tf_sec) * 1000
        if close_ms > now * 1000:
            continue  # forming bar — confirmed bars only
        for field in _FIELDS:
            raw = bar["tick_volume"] if field == "volume" else bar[field]
            sid = price_series_id("MT5", inst.symbol, tf, field)
            field_rows[field].append(
                row_from_rule(
                    sid,
                    AvailabilityRuleId.MT5_BAR,
                    value_time_ms=close_ms,
                    value=float(raw),
                    bar_close_ms=close_ms,
                )
            )

    counts: dict[str, int] = {}
    for field, rows in field_rows.items():
        sid = price_series_id("MT5", inst.symbol, tf, field)
        counts[sid] = append_ptis_rows(store, sid, "MT5", rows)
    return counts


def ingest_all(
    store: PTISStore,
    *,
    timeframes: tuple[str, ...] = _TIMEFRAMES,
    instruments: tuple[Instrument, ...] | None = None,
) -> dict[str, int]:
    mt5 = _connect_mt5()
    if mt5 is None:
        return {}
    from mt5_executor import mt5_map_symbol

    offset = _broker_offset_hours()
    totals: dict[str, int] = {}
    skipped: list[str] = []
    for inst in instruments or UNIVERSE:
        if inst.family == "crypto":
            continue  # crypto PTIS comes from the Bybit/Binance ingests
        mt5_symbol = mt5_map_symbol(inst.display) or mt5_map_symbol(inst.symbol)
        if not mt5_symbol:
            skipped.append(inst.symbol)
            continue
        try:
            mt5.symbol_select(mt5_symbol, True)
        except Exception:
            pass
        got_any = False
        for tf in timeframes:
            try:
                counts = ingest_instrument_tf(
                    store, mt5, inst, mt5_symbol, tf, broker_offset_hours=offset
                )
                if counts:
                    got_any = True
                totals.update(counts)
            except Exception as exc:
                log.warning("mt5_live ingest failed %s %s: %s", inst.symbol, tf, exc)
        if not got_any:
            skipped.append(inst.symbol)
    if skipped:
        log.info("mt5_live ingest: no terminal data for %s", ", ".join(sorted(set(skipped))))
    log.info("mt5_live ingest complete: %d series touched", len(totals))
    return totals
