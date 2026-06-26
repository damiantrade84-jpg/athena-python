"""Config + time helpers for the macro FOMC layer.

Resolution order for every key: ``os.environ`` (string, task-style ATHENA_FOMC_*)
→ ``config.CONFIG`` (config.yaml) → built-in default. CONFIG is imported lazily so
importing the store/guard never forces a full config load (keeps unit tests light).

All default windows / release times live here, not buried in ingestion logic.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

# ── Official Fed sources (overridable, but never required to be reachable) ──────
DEFAULT_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
DEFAULT_RSS_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
DEFAULT_PRATES_RSS_URL = "https://www.federalreserve.gov/feeds/prates.xml"

# ── Built-in defaults (mirror config.yaml ATHENA_FOMC_* block) ─────────────────
_DEFAULTS: dict[str, object] = {
    "ATHENA_FOMC_ENABLED": True,
    "ATHENA_FOMC_SCHEDULER_ENABLED": True,
    "ATHENA_FOMC_DEFAULT_RELEASE_TIME_ET": "14:00",
    "ATHENA_FOMC_DEFAULT_PRESSER_TIME_ET": "14:30",
    "ATHENA_FOMC_CALENDAR_REFRESH_HOURS": 24,
    "ATHENA_FOMC_PRE_LOCKOUT_MIN": 30,
    "ATHENA_FOMC_POST_LOCKOUT_MIN": 60,
    "ATHENA_FOMC_EXTENDED_CAUTION_MIN": 120,
    "ATHENA_FOMC_PRESSER_PRE_LOCKOUT_MIN": 15,
    "ATHENA_FOMC_PRESSER_POST_LOCKOUT_MIN": 45,
    # How far ahead a scheduled event is flagged UPCOMING (informational, no block).
    "ATHENA_FOMC_UPCOMING_HORIZON_MIN": 1440,
    # RSS poll cadence (seconds): slow normally, fast on an FOMC-window day.
    "ATHENA_FOMC_RSS_POLL_NORMAL_SEC": 1800,
    "ATHENA_FOMC_RSS_POLL_FOMC_SEC": 60,
    # When true, a hard macro lockout sets majorEventRisk.blocksAutoExecution=True
    # on affected candidates (reuses the existing, unchanged auto_trader gate).
    "ATHENA_FOMC_BLOCK_AUTO_EXECUTION": True,
    # When true, USD/USDT crypto majors are treated as macro-affected.
    "ATHENA_FOMC_AFFECTED_CRYPTO": True,
    # URLs
    "ATHENA_FOMC_CALENDAR_URL": DEFAULT_CALENDAR_URL,
    "ATHENA_FOMC_RSS_URL": DEFAULT_RSS_URL,
    "ATHENA_FOMC_PRATES_RSS_URL": DEFAULT_PRATES_RSS_URL,
}


# None = not yet attempted; False = config unavailable in this process; dict = loaded.
# Cached so a cold/standalone process (CLI, tests) where config.py exits fatally on a
# live-order safety gate degrades once to env/defaults instead of re-triggering it.
_CONFIG_CACHE: "dict | bool | None" = None


def _config_dict() -> dict | None:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        try:
            from config import CONFIG  # lazy: avoid forcing config load on import

            _CONFIG_CACHE = CONFIG if isinstance(CONFIG, dict) else False
        except (Exception, SystemExit):
            # config.py may sys.exit() on a real-order safety gate when imported cold.
            _CONFIG_CACHE = False
    return _CONFIG_CACHE if isinstance(_CONFIG_CACHE, dict) else None


def _config_get(name: str):
    cfg = _config_dict()
    if cfg is not None and name in cfg:
        return cfg.get(name)
    return None


def _raw(name: str):
    env = os.environ.get(name)
    if env is not None and str(env).strip() != "":
        return str(env).strip()
    cfg = _config_get(name)
    if cfg is not None:
        return cfg
    return _DEFAULTS.get(name)


def get_bool(name: str) -> bool:
    val = _raw(name)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def get_int(name: str) -> int:
    val = _raw(name)
    try:
        return int(float(str(val).strip()))
    except (TypeError, ValueError):
        default = _DEFAULTS.get(name, 0)
        return int(default) if isinstance(default, (int, float)) else 0


def get_str(name: str) -> str:
    val = _raw(name)
    return str(val) if val is not None else ""


# ── Typed accessors ────────────────────────────────────────────────────────────
def enabled() -> bool:
    return get_bool("ATHENA_FOMC_ENABLED")


def scheduler_enabled() -> bool:
    return get_bool("ATHENA_FOMC_SCHEDULER_ENABLED")


def block_auto_execution() -> bool:
    return get_bool("ATHENA_FOMC_BLOCK_AUTO_EXECUTION")


def affected_crypto() -> bool:
    return get_bool("ATHENA_FOMC_AFFECTED_CRYPTO")


def release_time_et() -> tuple[int, int]:
    return parse_hhmm(get_str("ATHENA_FOMC_DEFAULT_RELEASE_TIME_ET"), 14, 0)


def presser_time_et() -> tuple[int, int]:
    return parse_hhmm(get_str("ATHENA_FOMC_DEFAULT_PRESSER_TIME_ET"), 14, 30)


def calendar_refresh_hours() -> int:
    return max(1, get_int("ATHENA_FOMC_CALENDAR_REFRESH_HOURS"))


def pre_lockout_min() -> int:
    return max(0, get_int("ATHENA_FOMC_PRE_LOCKOUT_MIN"))


def post_lockout_min() -> int:
    return max(0, get_int("ATHENA_FOMC_POST_LOCKOUT_MIN"))


def extended_caution_min() -> int:
    return max(0, get_int("ATHENA_FOMC_EXTENDED_CAUTION_MIN"))


def presser_pre_lockout_min() -> int:
    return max(0, get_int("ATHENA_FOMC_PRESSER_PRE_LOCKOUT_MIN"))


def presser_post_lockout_min() -> int:
    return max(0, get_int("ATHENA_FOMC_PRESSER_POST_LOCKOUT_MIN"))


def upcoming_horizon_min() -> int:
    return max(0, get_int("ATHENA_FOMC_UPCOMING_HORIZON_MIN"))


def rss_poll_normal_sec() -> int:
    return max(30, get_int("ATHENA_FOMC_RSS_POLL_NORMAL_SEC"))


def rss_poll_fomc_sec() -> int:
    return max(15, get_int("ATHENA_FOMC_RSS_POLL_FOMC_SEC"))


def calendar_url() -> str:
    return get_str("ATHENA_FOMC_CALENDAR_URL") or DEFAULT_CALENDAR_URL


def rss_url() -> str:
    return get_str("ATHENA_FOMC_RSS_URL") or DEFAULT_RSS_URL


def prates_rss_url() -> str:
    return get_str("ATHENA_FOMC_PRATES_RSS_URL") or DEFAULT_PRATES_RSS_URL


# ── Time helpers ────────────────────────────────────────────────────────────────
def parse_hhmm(text: str, default_h: int = 14, default_m: int = 0) -> tuple[int, int]:
    try:
        h, m = str(text).strip().split(":", 1)
        return int(h), int(m)
    except (ValueError, AttributeError):
        return default_h, default_m


try:  # prefer the real tz database when present
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - exercised only when tzdata is missing
    _ET = None


def _is_us_eastern_dst(dt_naive: datetime) -> bool:
    """US DST: 02:00 second Sunday of March → 02:00 first Sunday of November."""
    year = dt_naive.year
    march = datetime(year, 3, 1)
    first_sun_mar = 1 + (6 - march.weekday()) % 7
    dst_start = datetime(year, 3, first_sun_mar + 7, 2, 0)
    nov = datetime(year, 11, 1)
    first_sun_nov = 1 + (6 - nov.weekday()) % 7
    dst_end = datetime(year, 11, first_sun_nov, 2, 0)
    return dst_start <= dt_naive < dst_end


def eastern_to_utc(naive_et: datetime) -> datetime:
    """Convert a naive America/New_York wall-clock datetime to aware UTC."""
    if _ET is not None:
        try:
            return naive_et.replace(tzinfo=_ET).astimezone(timezone.utc)
        except Exception:
            pass
    offset_h = 4 if _is_us_eastern_dst(naive_et) else 5  # EDT=-4, EST=-5
    return (naive_et + timedelta(hours=offset_h)).replace(tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
