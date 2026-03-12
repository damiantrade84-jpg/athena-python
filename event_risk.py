"""event_risk.py — EODHD Economic Calendar event risk filter for Sentinel Pro.

Before auto-executing a trade, checks if any HIGH-IMPACT economic events
(NFP, CPI, FOMC, Rate Decisions, etc.) are scheduled within the next N hours
for the currencies that make up the trading pair.

If a high-impact event is imminent, the trade is blocked to avoid
news-driven volatility spikes that frequently stop out positions.

Uses EODHD `get_economic_events_data()` from the Python library.
Results are cached for 60 minutes to limit API calls.
"""
import logging
import os
import time
from datetime import datetime, timezone, timedelta

log = logging.getLogger("sentinel.event_risk")

# Cache: { "US": { "events": [...], "ts": time.time() } }
_cache: dict = {}
_CACHE_TTL = 3600  # 60 minutes

# Map currency codes to EODHD country codes
_CURRENCY_TO_COUNTRY = {
    "USD": "US", "EUR": "EU", "GBP": "GB", "JPY": "JP", "AUD": "AU",
    "NZD": "NZ", "CAD": "CA", "CHF": "CH", "ZAR": "ZA", "MXN": "MX",
    "SGD": "SG", "HKD": "HK", "CNY": "CN", "SEK": "SE", "NOK": "NO",
    "BTC": None, "ETH": None,  # Crypto — no economic calendar events
}

# High-impact event keywords (normalized to lowercase for matching)
_HIGH_IMPACT_KEYWORDS = [
    "interest rate", "rate decision", "monetary policy", "fomc",
    "non-farm", "nonfarm", "nfp", "employment change",
    "cpi", "consumer price", "inflation",
    "gdp", "gross domestic",
    "retail sales", "trade balance",
    "pmi", "purchasing manager",
    "central bank", "ecb", "boe", "boj", "rba", "rbnz",
]


def _pair_to_countries(pair: str, asset_type: str) -> list[str]:
    """Extract relevant country codes from a trading pair."""
    if asset_type == "crypto":
        return []  # Crypto ignores economic calendar

    # Common pair patterns: EURUSD=X, EUR/USD, XAUUSD
    clean = pair.replace("=X", "").replace("/", "").replace("=F", "").upper()

    # Commodity mapping
    commodity_currencies = {
        "GC": ["US"], "SI": ["US"], "CL": ["US"], "BZ": ["US", "GB"],
        "NG": ["US"], "PL": ["US"], "PA": ["US"], "HG": ["US"],
    }
    for sym, countries in commodity_currencies.items():
        if clean.startswith(sym):
            return countries

    # Index mapping
    index_countries = {
        "SPX": ["US"], "NDX": ["US"], "DJI": ["US"], "GSPC": ["US"],
        "IXIC": ["US"], "FTSE": ["GB"], "GDAXI": ["EU"], "N225": ["JP"],
        "HSI": ["HK"], "STI": ["SG"],
    }
    for sym, countries in index_countries.items():
        if sym in clean:
            return countries

    # Stock — assume US
    if asset_type == "stock":
        return ["US"]

    # Forex — extract both currencies (e.g., EURUSD -> EU, US)
    countries = []
    for curr, country in _CURRENCY_TO_COUNTRY.items():
        if curr in clean and country:
            countries.append(country)
    return list(set(countries))  # deduplicate


def _is_high_impact(event: dict) -> bool:
    """Check if an event is high-impact based on importance field or keywords."""
    imp = str(event.get("importance", "")).lower()
    if imp in ("high", "3"):
        return True

    # Also check event name for known high-impact keywords
    name = str(event.get("event", "")).lower()
    return any(kw in name for kw in _HIGH_IMPACT_KEYWORDS)


def check_event_risk(pair: str, asset_type: str, lookahead_hours: int = 4) -> dict:
    """Check if high-impact economic events are scheduled soon.

    Args:
        pair: Trading pair symbol
        asset_type: "forex", "crypto", "commodity", "stock", "index"
        lookahead_hours: How many hours ahead to check (default: 4)

    Returns:
        {
            "allowed": bool,
            "events": [{"event": str, "country": str, "time": str}],
            "reason": str
        }
    """
    countries = _pair_to_countries(pair, asset_type)
    if not countries:
        return {"allowed": True, "events": [],
                "reason": f"No economic calendar for {asset_type}"}

    try:
        from eodhd import APIClient
        api_key = os.environ.get("EODHD_KEY", "")
        if not api_key:
            return {"allowed": True, "events": [],
                    "reason": "No EODHD_KEY — event risk skipped"}

        api = APIClient(api_key)
        now = datetime.now(timezone.utc)
        date_from = now.strftime("%Y-%m-%d")
        date_to = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        high_impact_events = []

        for country in countries:
            # Check cache
            cache_key = f"{country}_{date_from}"
            if cache_key in _cache and time.time() - _cache[cache_key]["ts"] < _CACHE_TTL:
                events = _cache[cache_key]["events"]
            else:
                try:
                    events = api.get_economic_events_data(
                        date_from=date_from,
                        date_to=date_to,
                        country=country,
                        limit="50"
                    )
                    if not isinstance(events, list):
                        events = []
                    _cache[cache_key] = {"events": events, "ts": time.time()}
                except Exception as e:
                    log.warning(f"[EVENT_RISK] API error for {country}: {e}")
                    continue

            # Filter for high-impact events within lookahead window
            for ev in events:
                if not _is_high_impact(ev):
                    continue

                # Parse event datetime
                ev_date = ev.get("date", "")
                try:
                    if "T" in str(ev_date):
                        ev_dt = datetime.fromisoformat(
                            str(ev_date).replace("Z", "+00:00"))
                    else:
                        ev_dt = datetime.strptime(str(ev_date), "%Y-%m-%d")
                        ev_dt = ev_dt.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue

                # Check if event is within lookahead window
                hours_until = (ev_dt - now).total_seconds() / 3600
                if -1 <= hours_until <= lookahead_hours:
                    high_impact_events.append({
                        "event": ev.get("event", "Unknown"),
                        "country": country,
                        "time": ev_date,
                        "hours_until": round(hours_until, 1),
                        "importance": ev.get("importance", ""),
                    })

        if high_impact_events:
            ev_names = ", ".join(e["event"][:40] for e in high_impact_events[:3])
            hours_min = min(e["hours_until"] for e in high_impact_events)
            reason = (f"EVENT RISK BLOCK: {len(high_impact_events)} high-impact event(s) "
                     f"in {hours_min:.1f}h — {ev_names}")
            log.warning(f"[EVENT_RISK] {pair}: {reason}")
            return {"allowed": False, "events": high_impact_events, "reason": reason}

        return {"allowed": True, "events": [],
                "reason": f"No high-impact events for {'/'.join(countries)} within {lookahead_hours}h"}

    except ImportError:
        log.warning("[EVENT_RISK] eodhd library not installed — gate skipped")
        return {"allowed": True, "events": [], "reason": "eodhd library not available"}
    except Exception as e:
        log.error(f"[EVENT_RISK] Unexpected error: {e}")
        return {"allowed": True, "events": [], "reason": f"Error: {e}"}
