"""
chart_renderer.py — Server-side chart rendering for Telegram + AI Vision

Renders H4 candle charts with annotations as PNG images (base64).
Used by Telegram bot for sending chart photos and by Engine C
for headless AI Vision analysis.
"""

import base64
import io
import logging
from typing import Optional, List

log = logging.getLogger("sentinel")


def render_chart_image(
    candles: list,
    entry: Optional[float] = None,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    direction: Optional[str] = None,
    title: str = "H4 Chart",
    width: int = 12,
    height: int = 6,
) -> str:
    """Render a candle chart as base64 PNG string.
    
    Args:
        candles: list of dicts with t, o, h, l, c, v keys
        entry/sl/tp: price levels to draw
        direction: LONG/SHORT for arrow annotation
        title: chart title
    
    Returns:
        base64-encoded PNG string
    """
    import mplfinance as mpf
    import pandas as pd
    
    # Convert candles to DataFrame
    data = []
    for c in candles:
        try:
            t = c.get("t", c.get("time", ""))
            data.append({
                "Date": pd.Timestamp(str(t).replace(" ", "T")),
                "Open": float(c.get("o", c.get("open", 0))),
                "High": float(c.get("h", c.get("high", 0))),
                "Low": float(c.get("l", c.get("low", 0))),
                "Close": float(c.get("c", c.get("close", 0))),
                "Volume": float(c.get("v", c.get("vol", 0))),
            })
        except Exception:
            continue
    
    if not data:
        return ""
    
    df = pd.DataFrame(data)
    df.set_index("Date", inplace=True)
    df.sort_index(inplace=True)
    
    # Remove duplicates and invalid rows
    df = df[~df.index.duplicated(keep="first")]
    df = df[(df["Open"] > 0) & (df["High"] > 0)]
    
    # Dark theme matching Athena UI
    mc = mpf.make_marketcolors(
        up="#26a69a", down="#ef5350",
        wick={"up": "#26a69a", "down": "#ef5350"},
        edge={"up": "#26a69a", "down": "#ef5350"},
        volume={"up": "rgba(38,166,154,0.3)", "down": "rgba(239,83,80,0.3)"},
    )
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mc,
        facecolor="#0c1017",
        edgecolor="#1a2332",
        gridcolor="#1a2332",
        gridstyle=":",
        rc={"font.size": 8},
    )
    
    # Build horizontal lines for levels
    hlines = {"hlines": [], "colors": [], "linestyle": [], "linewidths": []}
    
    if entry and entry > 0:
        hlines["hlines"].append(entry)
        hlines["colors"].append("#94a3b8")
        hlines["linestyle"].append("--")
        hlines["linewidths"].append(0.8)
    
    if sl and sl > 0:
        hlines["hlines"].append(sl)
        hlines["colors"].append("#ef5350")
        hlines["linestyle"].append("-")
        hlines["linewidths"].append(1.2)
    
    if tp and tp > 0:
        hlines["hlines"].append(tp)
        hlines["colors"].append("#26a69a")
        hlines["linestyle"].append("-")
        hlines["linewidths"].append(1.2)
    
    # Calculate EMAs
    addplots = []
    if len(df) >= 21:
        ema21 = df["Close"].ewm(span=21).mean()
        addplots.append(mpf.make_addplot(ema21, color="#00bcd4", width=0.8))
        high_low = df["High"] - df["Low"]
        high_close = (df["High"] - df["Close"].shift()).abs()
        low_close = (df["Low"] - df["Close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr14 = tr.ewm(span=14, adjust=False).mean()
        kelt_upper = ema21 + (atr14 * 1.5)
        kelt_lower = ema21 - (atr14 * 1.5)
        addplots.append(
            mpf.make_addplot(
                kelt_upper,
                color="rgba(0,188,212,0.25)",
                width=0.5,
                linestyle="--",
            )
        )
        addplots.append(
            mpf.make_addplot(
                kelt_lower,
                color="rgba(0,188,212,0.25)",
                width=0.5,
                linestyle="--",
            )
        )
    if len(df) >= 50:
        ema50 = df["Close"].ewm(span=50).mean()
        addplots.append(mpf.make_addplot(ema50, color="#9575cd", width=0.8))
    if len(df) >= 200:
        ema200 = df["Close"].ewm(span=200).mean()
        addplots.append(mpf.make_addplot(ema200, color="#ffb74d", width=0.6, linestyle="--"))
    
    # Render to buffer
    buf = io.BytesIO()
    
    kwargs = {
        "type": "candle",
        "style": style,
        "title": title,
        "volume": True,
        "figsize": (width, height),
        "savefig": dict(fname=buf, dpi=150, bbox_inches="tight", facecolor="#0c1017"),
    }
    
    if addplots:
        kwargs["addplot"] = addplots
    
    if hlines["hlines"]:
        kwargs["hlines"] = hlines
    
    mpf.plot(df, **kwargs)
    
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode("utf-8")
    buf.close()
    
    return img_base64
