"""Central session contract for Athena / Sentinel Pro v4.

Single source of truth for session classification across all asset classes.
All engines, execution paths, and backtests should route through classify_session().

Session-quality windows are defined in SAST (Africa/Johannesburg, UTC+2, no DST).
Market open/closed status uses the same SAST anchor for simplicity.

DST note: EU exchanges shift by ~1h SAST between CEST and CET.  US exchanges
shift similarly.  The SAST windows are pragmatic approximations of the user's
premium-liquidity periods, not exact exchange-clock replicas.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from zoneinfo import ZoneInfo

log = logging.getLogger("sentinel")

_TZ_SAST = ZoneInfo("Africa/Johannesburg")
_TZ_UTC = timezone.utc

# ── Quality constants ─────────────────────────────────────────────────────────

QUALITY_PREMIUM = "premium"
QUALITY_NORMAL = "normal"
QUALITY_WEAK = "weak"
QUALITY_AVOID = "avoid"
QUALITY_CLOSED = "closed"


# ── Return type ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SessionClassification:
    """Canonical session classification result — JSON-serialisable via to_dict()."""

    asset_class: str
    symbol: str
    session_name: str
    session_quality: str
    session_reason: str
    local_time_sast: str
    utc_time: str
    is_weekend: bool
    avoid_reason: str | None = None
    warnings: tuple[str, ...] = ()
    display_name: str = ""

    @property
    def is_premium(self) -> bool:
        return self.session_quality == QUALITY_PREMIUM

    @property
    def is_normal(self) -> bool:
        return self.session_quality == QUALITY_NORMAL

    @property
    def is_weak(self) -> bool:
        return self.session_quality == QUALITY_WEAK

    @property
    def is_avoid(self) -> bool:
        return self.session_quality == QUALITY_AVOID

    @property
    def is_closed(self) -> bool:
        return self.session_quality == QUALITY_CLOSED

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = asdict(self)
        d["is_premium"] = self.is_premium
        d["is_normal"] = self.is_normal
        d["is_weak"] = self.is_weak
        d["is_avoid"] = self.is_avoid
        d["is_closed"] = self.is_closed
        d["warnings"] = list(self.warnings)
        return d


# ── SAST helpers ──────────────────────────────────────────────────────────────

def _coerce_utc(timestamp: Any) -> tuple[datetime, list[str]]:
    """Coerce input to a UTC-aware datetime.  Returns (dt_utc, warnings)."""
    warnings: list[str] = []
    if timestamp is None:
        return datetime.now(_TZ_UTC), warnings
    if isinstance(timestamp, datetime):
        if timestamp.tzinfo is None:
            warnings.append("naive_timestamp_treated_as_utc")
            return timestamp.replace(tzinfo=_TZ_UTC), warnings
        return timestamp.astimezone(_TZ_UTC), warnings
    if isinstance(timestamp, (int, float)):
        try:
            return datetime.fromtimestamp(float(timestamp), tz=_TZ_UTC), warnings
        except (OverflowError, OSError, ValueError):
            warnings.append("invalid_numeric_timestamp_using_current_utc")
            return datetime.now(_TZ_UTC), warnings
    if isinstance(timestamp, str):
        raw = str(timestamp).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            warnings.append(f"unparseable_timestamp_using_current_utc")
            return datetime.now(_TZ_UTC), warnings
        if parsed.tzinfo is None:
            warnings.append("naive_timestamp_treated_as_utc")
            return parsed.replace(tzinfo=_TZ_UTC), warnings
        return parsed.astimezone(_TZ_UTC), warnings
    warnings.append(f"unsupported_timestamp_type_using_current_utc")
    return datetime.now(_TZ_UTC), warnings


def to_sast(dt_utc: datetime) -> datetime:
    """Convert UTC datetime to SAST (Africa/Johannesburg, UTC+2 fixed)."""
    return dt_utc.astimezone(_TZ_SAST)


def _sast_minutes(dt_sast: datetime) -> int:
    return dt_sast.hour * 60 + dt_sast.minute


def _in_sast_range(mins: int, sh: int, sm: int, eh: int, em: int) -> bool:
    """Check if *mins* (minutes from midnight) is in [start, end). Handles midnight crossing."""
    s = sh * 60 + sm
    e = eh * 60 + em
    if s < e:
        return s <= mins < e
    return mins >= s or mins < e


# ── Window definitions (SAST hours) ──────────────────────────────────────────
# Each tuple: (start_h, start_m, end_h, end_m, name, quality, reason)
# Checked in order — first match wins.

_W = tuple[int, int, int, int, str, str, str]

_FOREX_WINDOWS: list[_W] = [
    (8, 0, 11, 0, "london_open", QUALITY_PREMIUM,
     "London open — peak institutional flow"),
    (14, 0, 18, 0, "london_ny_overlap", QUALITY_PREMIUM,
     "London/NY overlap — highest liquidity"),
    (1, 0, 6, 0, "asian_session", QUALITY_NORMAL,
     "Asian session — moderate liquidity"),
    (6, 0, 8, 0, "pre_london", QUALITY_NORMAL,
     "Pre-London European activity"),
    (11, 0, 14, 0, "london_midday", QUALITY_NORMAL,
     "London midday — moderate flow"),
    (18, 0, 22, 0, "ny_afternoon", QUALITY_NORMAL,
     "New York afternoon"),
    (0, 0, 1, 0, "post_rollover", QUALITY_WEAK,
     "Post-rollover thin liquidity"),
    (22, 0, 23, 0, "late_ny", QUALITY_WEAK,
     "Late New York — thinning liquidity"),
]

_CRYPTO_WINDOWS: list[_W] = [
    (15, 30, 18, 0, "us_open_crypto", QUALITY_PREMIUM,
     "US open crypto — peak scalp window"),
    (14, 0, 15, 30, "eu_us_overlap_early", QUALITY_PREMIUM,
     "EU/US overlap — high crypto liquidity"),
    (18, 0, 19, 0, "eu_us_overlap_late", QUALITY_PREMIUM,
     "EU/US overlap tail — still elevated volume"),
    (1, 0, 5, 0, "asia_crypto", QUALITY_NORMAL,
     "Asia crypto session"),
    (5, 0, 9, 0, "asia_late", QUALITY_NORMAL,
     "Late Asia / early EU transition"),
    (9, 0, 14, 0, "eu_morning_crypto", QUALITY_NORMAL,
     "EU morning crypto activity"),
    (19, 0, 22, 0, "us_afternoon_crypto", QUALITY_NORMAL,
     "US afternoon crypto"),
    (22, 0, 1, 0, "dead_zone", QUALITY_WEAK,
     "Crypto dead zone — low volume"),
]

_US_EQUITY_WINDOWS: list[_W] = [
    (15, 30, 17, 30, "us_equity_open", QUALITY_PREMIUM,
     "US market open — highest volume"),
    (20, 30, 22, 0, "us_equity_close", QUALITY_PREMIUM,
     "US market close — high volume"),
    (17, 30, 18, 0, "post_open_transition", QUALITY_NORMAL,
     "Post-open transition"),
    (20, 0, 20, 30, "pre_close", QUALITY_NORMAL,
     "Pre-close positioning"),
    (18, 0, 20, 0, "us_equity_midday", QUALITY_WEAK,
     "US midday — choppy, low conviction"),
]

_JSE_WINDOWS: list[_W] = [
    (9, 0, 10, 30, "jse_open", QUALITY_PREMIUM,
     "JSE open — price discovery"),
    (10, 30, 15, 30, "jse_midday", QUALITY_NORMAL,
     "JSE midday session"),
    (15, 30, 16, 50, "jse_close", QUALITY_NORMAL,
     "JSE close approach"),
    (16, 50, 17, 0, "jse_auction", QUALITY_WEAK,
     "JSE closing auction — caution"),
]

# DST caveat: EU exchanges open 09:00 CEST (summer) = 09:00 SAST,
# but 09:00 CET (winter) = 10:00 SAST.  Windows approximate CEST alignment.
_EU_EQUITY_WINDOWS: list[_W] = [
    (9, 0, 11, 0, "eu_open", QUALITY_PREMIUM,
     "EU market open — institutional flow"),
    (15, 30, 17, 30, "eu_us_overlap", QUALITY_PREMIUM,
     "EU/US overlap — cross-market liquidity"),
    (11, 0, 15, 30, "eu_midday", QUALITY_NORMAL,
     "EU midday session"),
]

_METALS_WINDOWS: list[_W] = [
    (8, 0, 11, 0, "metals_london", QUALITY_PREMIUM,
     "London metals session — peak flow"),
    (14, 30, 18, 0, "metals_ny_overlap", QUALITY_PREMIUM,
     "NY overlap metals — high liquidity"),
    (11, 0, 14, 30, "metals_midday", QUALITY_NORMAL,
     "Metals midday transition"),
    (18, 0, 22, 0, "metals_ny_afternoon", QUALITY_NORMAL,
     "NY afternoon metals"),
    (6, 0, 8, 0, "metals_pre_london", QUALITY_NORMAL,
     "Pre-London metals"),
    (0, 0, 6, 0, "metals_off_hours", QUALITY_WEAK,
     "Off-hours metals — thin"),
    (22, 0, 23, 0, "metals_late", QUALITY_WEAK,
     "Late session metals"),
]

_ENERGY_WINDOWS: list[_W] = [
    (15, 0, 19, 30, "energy_us", QUALITY_PREMIUM,
     "US energy session — peak volume"),
    (8, 0, 15, 0, "energy_london_europe", QUALITY_NORMAL,
     "London/EU energy session"),
    (19, 30, 22, 0, "energy_late_us", QUALITY_NORMAL,
     "Late US energy"),
    (6, 0, 8, 0, "energy_pre_london", QUALITY_NORMAL,
     "Pre-London energy"),
    (0, 0, 6, 0, "energy_off_hours", QUALITY_WEAK,
     "Off-hours energy — thin"),
    (22, 0, 23, 0, "energy_late", QUALITY_WEAK,
     "Late session energy"),
]

_COPPER_WINDOWS: list[_W] = [
    (8, 0, 11, 0, "copper_london", QUALITY_PREMIUM,
     "London copper session"),
    (14, 30, 18, 0, "copper_ny_overlap", QUALITY_PREMIUM,
     "NY overlap copper"),
    (11, 0, 14, 30, "copper_midday", QUALITY_NORMAL,
     "Copper midday"),
    (18, 0, 22, 0, "copper_ny_afternoon", QUALITY_NORMAL,
     "NY afternoon copper"),
    (6, 0, 8, 0, "copper_pre_london", QUALITY_NORMAL,
     "Pre-London copper"),
    (0, 0, 6, 0, "copper_off_hours", QUALITY_WEAK,
     "Off-hours copper — thin"),
    (22, 0, 23, 0, "copper_late", QUALITY_WEAK,
     "Late session copper"),
]


# ── Display names ─────────────────────────────────────────────────────────────

_DISPLAY_NAMES: dict[str, str] = {
    "london_open": "London Open",
    "london_ny_overlap": "London/NY Overlap",
    "asian_session": "Asian Session",
    "pre_london": "Pre-London",
    "london_midday": "London",
    "ny_afternoon": "New York",
    "post_rollover": "Post-Rollover",
    "late_ny": "Late NY",
    "rollover": "Rollover",
    "sunday_open": "Sunday Open",
    "late_friday": "Late Friday",
    "weekend_closed": "Weekend",
    "off_hours": "Off-Hours",
    # Crypto
    "us_open_crypto": "US Open (Crypto)",
    "eu_us_overlap_early": "EU/US Overlap",
    "eu_us_overlap_late": "EU/US Overlap",
    "asia_crypto": "Asia (Crypto)",
    "asia_late": "Late Asia",
    "eu_morning_crypto": "EU Morning",
    "us_afternoon_crypto": "US Afternoon",
    "dead_zone": "Dead Zone",
    "crypto_transition": "Transition",
    # US equity
    "us_equity_open": "US Open",
    "us_equity_close": "US Close",
    "post_open_transition": "Post-Open",
    "pre_close": "Pre-Close",
    "us_equity_midday": "US Midday",
    "us_after_hours": "After Hours",
    "us_equity_closed": "Market Closed",
    # JSE
    "jse_open": "JSE Open",
    "jse_midday": "JSE Midday",
    "jse_close": "JSE Close",
    "jse_auction": "JSE Auction",
    "jse_closed": "JSE Closed",
    # EU
    "eu_open": "EU Open",
    "eu_us_overlap": "EU/US Overlap",
    "eu_midday": "EU Midday",
    "eu_equity_closed": "EU Closed",
    # Commodities
    "metals_london": "London Metals",
    "metals_ny_overlap": "NY Metals Overlap",
    "metals_midday": "Metals Midday",
    "metals_ny_afternoon": "NY Metals",
    "metals_pre_london": "Pre-London Metals",
    "metals_off_hours": "Metals Off-Hours",
    "metals_late": "Late Metals",
    "energy_us": "US Energy",
    "energy_london_europe": "London/EU Energy",
    "energy_late_us": "Late US Energy",
    "energy_pre_london": "Pre-London Energy",
    "energy_off_hours": "Energy Off-Hours",
    "energy_late": "Late Energy",
    "copper_london": "London Copper",
    "copper_ny_overlap": "NY Copper Overlap",
    "copper_midday": "Copper Midday",
    "copper_ny_afternoon": "NY Copper",
    "copper_pre_london": "Pre-London Copper",
    "copper_off_hours": "Copper Off-Hours",
    "copper_late": "Late Copper",
    "commodity_maintenance": "Maintenance",
    "commodity_off_hours": "Commodity Off-Hours",
}


# ── Symbol heuristics ─────────────────────────────────────────────────────────

_PRECIOUS_PATTERNS = frozenset({
    "XAU", "XAG", "GOLD", "SILVER", "GLD", "SLV", "GDX", "XPT", "XPD",
})
_ENERGY_PATTERNS = frozenset({
    "WTI", "BRENT", "OIL", "USOIL", "CL", "NATGAS", "NG", "USO", "XLE",
})
_COPPER_PATTERNS = frozenset({"COPPER", "HG", "XCU", "XCUUSD"})


def _commodity_sub_type(symbol: str) -> str:
    sym = str(symbol or "").upper().replace("/", "").replace(" ", "")
    for pat in _PRECIOUS_PATTERNS:
        if pat in sym:
            return "precious"
    for pat in _ENERGY_PATTERNS:
        if pat in sym:
            return "energy"
    for pat in _COPPER_PATTERNS:
        if pat in sym:
            return "copper"
    return "unknown"


def _is_jse_symbol(symbol: str, venue: str | None) -> bool:
    if venue and str(venue).upper() in {"JSE", "JOHANNESBURG"}:
        return True
    sym = str(symbol or "").upper()
    return sym.endswith(".JO") or sym.endswith(".JSE") or ".JO:" in sym


def _is_eu_symbol(symbol: str, venue: str | None) -> bool:
    if venue and str(venue).upper() in {
        "LSE", "DAX", "EURONEXT", "XETRA", "FTSE", "AMS", "PAR",
    }:
        return True
    sym = str(symbol or "").upper()
    eu_suffixes = (".L", ".DE", ".PA", ".AS", ".MI", ".MC", ".BR", ".HE", ".OL", ".ST", ".CO")
    return any(sym.endswith(s) for s in eu_suffixes)


# ── Window matcher ────────────────────────────────────────────────────────────

def _match_window(sast_mins: int, windows: list[_W]) -> _W | None:
    for w in windows:
        if _in_sast_range(sast_mins, w[0], w[1], w[2], w[3]):
            return w
    return None


# ── Per-asset classifiers ─────────────────────────────────────────────────────
# Each returns (session_name, quality, reason, avoid_reason | None).

def _classify_forex(
    symbol: str, dt_sast: datetime, sast_mins: int, warnings: list[str],
) -> tuple[str, str, str, str | None]:
    wd = dt_sast.weekday()  # 0=Mon .. 6=Sun

    if wd in (5, 6):
        return "weekend_closed", QUALITY_CLOSED, "Forex market closed (weekend)", None

    if wd == 0 and sast_mins < 60:
        return "sunday_open", QUALITY_AVOID, "Sunday open — thin liquidity, gaps", "sunday_open_gaps"

    if wd == 4 and sast_mins >= 23 * 60:
        return "late_friday", QUALITY_AVOID, "Late Friday — spreads widening", "late_friday_close"

    if wd <= 3 and sast_mins >= 23 * 60:
        return "rollover", QUALITY_AVOID, "Forex rollover — wide spreads", "rollover_spread_risk"

    m = _match_window(sast_mins, _FOREX_WINDOWS)
    if m:
        return m[4], m[5], m[6], None

    return "off_hours", QUALITY_WEAK, "Forex off-hours — low liquidity", None


def _classify_crypto(
    symbol: str, dt_sast: datetime, sast_mins: int, warnings: list[str],
) -> tuple[str, str, str, str | None]:
    m = _match_window(sast_mins, _CRYPTO_WINDOWS)
    if m:
        return m[4], m[5], m[6], None
    return "crypto_transition", QUALITY_NORMAL, "Crypto transition period", None


def _classify_us_equity(
    symbol: str, dt_sast: datetime, sast_mins: int, warnings: list[str],
) -> tuple[str, str, str, str | None]:
    if dt_sast.weekday() in (5, 6):
        return "weekend_closed", QUALITY_CLOSED, "US market closed (weekend)", None

    m = _match_window(sast_mins, _US_EQUITY_WINDOWS)
    if m:
        return m[4], m[5], m[6], None

    if 22 * 60 <= sast_mins:
        return "us_after_hours", QUALITY_AVOID, "US after-hours — low liquidity", "after_hours_risk"

    return "us_equity_closed", QUALITY_CLOSED, "US market closed", None


def _classify_jse(
    symbol: str, dt_sast: datetime, sast_mins: int, warnings: list[str],
) -> tuple[str, str, str, str | None]:
    if dt_sast.weekday() in (5, 6):
        return "weekend_closed", QUALITY_CLOSED, "JSE closed (weekend)", None

    m = _match_window(sast_mins, _JSE_WINDOWS)
    if m:
        return m[4], m[5], m[6], None

    return "jse_closed", QUALITY_CLOSED, "JSE closed — outside 09:00-17:00 SAST", None


def _classify_eu_equity(
    symbol: str, dt_sast: datetime, sast_mins: int, warnings: list[str],
) -> tuple[str, str, str, str | None]:
    if dt_sast.weekday() in (5, 6):
        return "weekend_closed", QUALITY_CLOSED, "EU market closed (weekend)", None

    m = _match_window(sast_mins, _EU_EQUITY_WINDOWS)
    if m:
        return m[4], m[5], m[6], None

    return "eu_equity_closed", QUALITY_CLOSED, "EU market closed", None


def _classify_commodity(
    symbol: str, dt_sast: datetime, sast_mins: int, warnings: list[str],
) -> tuple[str, str, str, str | None]:
    if dt_sast.weekday() in (5, 6):
        return "weekend_closed", QUALITY_CLOSED, "Commodity market closed (weekend)", None

    if sast_mins >= 23 * 60:
        return ("commodity_maintenance", QUALITY_AVOID,
                "Daily maintenance/rollover — avoid trading", "maintenance_spread_risk")

    sub = _commodity_sub_type(symbol)
    if sub == "precious":
        windows = _METALS_WINDOWS
    elif sub == "energy":
        windows = _ENERGY_WINDOWS
    elif sub == "copper":
        windows = _COPPER_WINDOWS
    else:
        windows = _METALS_WINDOWS
        if symbol:
            warnings.append("unknown_commodity_using_metals_default")

    m = _match_window(sast_mins, windows)
    if m:
        return m[4], m[5], m[6], None

    return "commodity_off_hours", QUALITY_WEAK, "Commodity off-hours", None


# ── Main contract ─────────────────────────────────────────────────────────────

def classify_session(
    symbol: str = "",
    asset_class: str = "forex",
    timestamp_utc: Any = None,
    *,
    provider: str | None = None,
    venue: str | None = None,
    timeframe: str | None = None,
    mode: str | None = None,
) -> SessionClassification:
    """Canonical session classifier for all asset classes.

    Parameters
    ----------
    symbol : Trading symbol (e.g. "EUR/USD", "BTC/USDT", "AAPL", "XAUUSD").
    asset_class : forex | crypto | stock | index | etf | etf_bond | commodity.
    timestamp_utc : Point in time to classify.  Naive → treated as UTC + warning.
        For backtests, pass the trade entry timestamp — never current time.
    provider : Data provider hint (optional, reserved).
    venue : Exchange hint for routing (e.g. "JSE", "LSE", "NYSE").
    timeframe : Timeframe hint (optional, reserved).
    mode : Override mode (optional, backward compat).
    """
    dt_utc, warnings = _coerce_utc(timestamp_utc)
    dt_sast = to_sast(dt_utc)
    sast_mins = _sast_minutes(dt_sast)
    asset = str(asset_class or "forex").strip().lower()
    sym = str(symbol or "")
    is_weekend = dt_sast.weekday() in (5, 6)

    if asset == "forex":
        name, quality, reason, avoid = _classify_forex(sym, dt_sast, sast_mins, warnings)
    elif asset == "crypto":
        name, quality, reason, avoid = _classify_crypto(sym, dt_sast, sast_mins, warnings)
    elif asset in ("stock", "index", "etf", "etf_bond"):
        if _is_jse_symbol(sym, venue):
            name, quality, reason, avoid = _classify_jse(sym, dt_sast, sast_mins, warnings)
        elif _is_eu_symbol(sym, venue):
            name, quality, reason, avoid = _classify_eu_equity(sym, dt_sast, sast_mins, warnings)
        else:
            name, quality, reason, avoid = _classify_us_equity(sym, dt_sast, sast_mins, warnings)
    elif asset == "commodity":
        name, quality, reason, avoid = _classify_commodity(sym, dt_sast, sast_mins, warnings)
    else:
        warnings.append(f"unknown_asset_class_{asset}_defaulting_to_forex")
        name, quality, reason, avoid = _classify_forex(sym, dt_sast, sast_mins, warnings)

    display = _DISPLAY_NAMES.get(name, name.replace("_", " ").title())

    return SessionClassification(
        asset_class=asset,
        symbol=sym,
        session_name=name,
        session_quality=quality,
        session_reason=reason,
        local_time_sast=dt_sast.strftime("%Y-%m-%d %H:%M:%S SAST"),
        utc_time=dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        is_weekend=is_weekend,
        avoid_reason=avoid,
        warnings=tuple(warnings),
        display_name=display,
    )


# ── Legacy helpers (for wrappers in other modules) ────────────────────────────

def session_quality_to_legacy(quality: str) -> str:
    """Map canonical quality to legacy high/medium/low labels."""
    return {"premium": "high", "normal": "medium"}.get(quality, "low")


_LEGACY_COLORS = {"high": "#22c55e", "medium": "#3b82f6", "low": "#f59e0b"}


def session_to_legacy_dict(sc: SessionClassification) -> dict[str, Any]:
    """Build the legacy ``{name, quality, color}`` dict from a classification."""
    q = session_quality_to_legacy(sc.session_quality)
    return {
        "name": sc.display_name,
        "quality": q,
        "color": _LEGACY_COLORS.get(q, "#f59e0b"),
    }
