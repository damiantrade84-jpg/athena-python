"""Server-side chart rendering for Telegram and AI vision inputs."""

from __future__ import annotations

import base64
import io
import logging
from typing import Any, Optional

log = logging.getLogger("sentinel")


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _zone_bounds(zone: dict[str, Any]) -> tuple[float | None, float | None]:
    low = _to_float(
        zone.get("lower")
        or zone.get("bottom")
        or zone.get("low")
        or zone.get("min")
        or zone.get("price_low")
    )
    high = _to_float(
        zone.get("upper")
        or zone.get("top")
        or zone.get("high")
        or zone.get("max")
        or zone.get("price_high")
    )
    if low is None and high is not None:
        low = high
    if high is None and low is not None:
        high = low
    if low is None or high is None:
        return None, None
    if low > high:
        low, high = high, low
    return low, high


def _candle_parts(open_px: float, high_px: float, low_px: float, close_px: float) -> dict[str, float]:
    body = abs(close_px - open_px)
    full = max(high_px - low_px, 1e-9)
    upper = max(0.0, high_px - max(open_px, close_px))
    lower = max(0.0, min(open_px, close_px) - low_px)
    return {"body": body, "range": full, "upper": upper, "lower": lower}


def _is_doji(parts: dict[str, float]) -> bool:
    return parts["body"] <= (parts["range"] * 0.1)


def _is_hammer(parts: dict[str, float], bullish_bias: bool) -> bool:
    if parts["body"] <= 0:
        return False
    if parts["lower"] < parts["body"] * 2.0:
        return False
    if parts["upper"] > parts["body"] * 0.5:
        return False
    return bullish_bias


def _is_shooting_star(parts: dict[str, float], bearish_bias: bool) -> bool:
    if parts["body"] <= 0:
        return False
    if parts["upper"] < parts["body"] * 2.0:
        return False
    if parts["lower"] > parts["body"] * 0.5:
        return False
    return bearish_bias


def detect_recent_patterns(df, lookback: int = 10) -> list[dict[str, Any]]:
    """Detect simple deterministic patterns on the last N bars."""
    patterns: list[dict[str, Any]] = []
    if df is None or len(df) < 3:
        return patterns
    lookback = max(3, int(lookback))
    start = max(0, len(df) - lookback)
    tail = df.iloc[start:].copy()
    for i in range(1, len(tail)):
        row = tail.iloc[i]
        prev = tail.iloc[i - 1]
        idx = tail.index[i]
        p = _candle_parts(
            float(row["Open"]),
            float(row["High"]),
            float(row["Low"]),
            float(row["Close"]),
        )
        bullish = float(row["Close"]) > float(row["Open"])
        bearish = float(row["Close"]) < float(row["Open"])
        if _is_doji(p):
            patterns.append({"index": idx, "label": "Doji", "bias": "neutral"})
        if _is_hammer(p, bullish):
            patterns.append({"index": idx, "label": "Hammer", "bias": "bull"})
        if _is_shooting_star(p, bearish):
            patterns.append({"index": idx, "label": "Shooting Star", "bias": "bear"})

        prev_open = float(prev["Open"])
        prev_close = float(prev["Close"])
        cur_open = float(row["Open"])
        cur_close = float(row["Close"])
        prev_bear = prev_close < prev_open
        prev_bull = prev_close > prev_open
        cur_bull = cur_close > cur_open
        cur_bear = cur_close < cur_open
        if prev_bear and cur_bull and cur_open <= prev_close and cur_close >= prev_open:
            patterns.append({"index": idx, "label": "Bull Engulf", "bias": "bull"})
        if prev_bull and cur_bear and cur_open >= prev_close and cur_close <= prev_open:
            patterns.append({"index": idx, "label": "Bear Engulf", "bias": "bear"})

    if len(tail) >= 3:
        last3 = tail.iloc[-3:]
        soldiers = all(float(r["Close"]) > float(r["Open"]) for _, r in last3.iterrows())
        crows = all(float(r["Close"]) < float(r["Open"]) for _, r in last3.iterrows())
        if soldiers:
            patterns.append({"index": last3.index[-1], "label": "3 Soldiers", "bias": "bull"})
        if crows:
            patterns.append({"index": last3.index[-1], "label": "3 Crows", "bias": "bear"})
    return patterns


def render_chart_image(
    candles: list,
    entry: Optional[float] = None,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    direction: Optional[str] = None,
    title: str = "H4 Chart",
    width: int = 12,
    height: int = 6,
    poc: Optional[float] = None,
    vah: Optional[float] = None,
    val: Optional[float] = None,
    engine_b: dict | None = None,
    bars_window: int = 80,
    dpi: int = 200,
    show_pattern_labels: bool = True,
    pattern_lookback: int = 10,
) -> str:
    """Render a candle chart as base64 PNG string."""
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    import mplfinance as mpf
    import pandas as pd

    data = []
    for c in candles or []:
        try:
            t = c.get("t", c.get("time", c.get("datetime", "")))
            data.append(
                {
                    "Date": pd.Timestamp(str(t).replace(" ", "T")),
                    "Open": float(c.get("o", c.get("open", 0))),
                    "High": float(c.get("h", c.get("high", 0))),
                    "Low": float(c.get("l", c.get("low", 0))),
                    "Close": float(c.get("c", c.get("close", 0))),
                    "Volume": float(c.get("v", c.get("vol", c.get("volume", 0)))),
                }
            )
        except Exception:
            continue

    if not data:
        return ""

    df = pd.DataFrame(data).set_index("Date").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df[(df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0)]
    if df.empty:
        return ""
    bars_window = max(20, int(bars_window))
    if len(df) > bars_window:
        df = df.tail(bars_window)

    mc = mpf.make_marketcolors(
        up="#26a69a",
        down="#ef5350",
        wick={"up": "#26a69a", "down": "#ef5350"},
        edge={"up": "#26a69a", "down": "#ef5350"},
        volume={"up": "#1f8f7f", "down": "#b04744"},
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

    hlines = {"hlines": [], "colors": [], "linestyle": [], "linewidths": []}
    for price, color, ls, lw in (
        (entry, "#94a3b8", "--", 0.8),
        (sl, "#ef5350", "-", 1.2),
        (tp, "#26a69a", "-", 1.2),
        (poc, "#f59e0b", "-", 1.3),
        (vah, "#a78bfa", "--", 1.0),
        (val, "#a78bfa", "--", 1.0),
    ):
        p = _to_float(price)
        if p is not None and p > 0:
            hlines["hlines"].append(p)
            hlines["colors"].append(color)
            hlines["linestyle"].append(ls)
            hlines["linewidths"].append(lw)

    addplots = []
    if len(df) >= 21:
        ema21 = df["Close"].ewm(span=21).mean()
        addplots.append(mpf.make_addplot(ema21, color="#00bcd4", width=0.9))
        high_low = df["High"] - df["Low"]
        high_close = (df["High"] - df["Close"].shift()).abs()
        low_close = (df["Low"] - df["Close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr14 = tr.ewm(span=14, adjust=False).mean()
        kelt_upper = ema21 + (atr14 * 1.5)
        kelt_lower = ema21 - (atr14 * 1.5)
        addplots.append(
            mpf.make_addplot(kelt_upper, color="#00bcd4", width=0.6, linestyle="--")
        )
        addplots.append(
            mpf.make_addplot(kelt_lower, color="#00bcd4", width=0.6, linestyle="--")
        )
    if len(df) >= 50:
        ema50 = df["Close"].ewm(span=50).mean()
        addplots.append(mpf.make_addplot(ema50, color="#9575cd", width=0.9))
    if len(df) >= 200:
        ema200 = df["Close"].ewm(span=200).mean()
        addplots.append(mpf.make_addplot(ema200, color="#ffb74d", width=0.8, linestyle="--"))

    kwargs = {
        "type": "candle",
        "style": style,
        "title": title,
        "volume": True,
        "figsize": (width, height),
        "returnfig": True,
        "warn_too_much_data": 10000,
    }
    if addplots:
        kwargs["addplot"] = addplots
    if hlines["hlines"]:
        kwargs["hlines"] = hlines

    fig, axes = mpf.plot(df, **kwargs)
    ax = axes[0] if axes else None
    if ax is None:
        return ""

    x_right = max(0, len(df) - 1)
    x_by_idx = {idx: i for i, idx in enumerate(df.index)}

    try:
        if poc and poc > 0:
            ax.text(x_right + 0.2, float(poc), "POC", fontsize=6, color="#f59e0b", va="center")
        if vah and vah > 0:
            ax.text(x_right + 0.2, float(vah), "VAH", fontsize=6, color="#a78bfa", va="center")
        if val and val > 0:
            ax.text(x_right + 0.2, float(val), "VAL", fontsize=6, color="#a78bfa", va="center")
    except Exception:
        pass

    eb = engine_b or {}
    try:
        sr_zones = []
        if isinstance(eb.get("nearest_support_zone"), dict):
            sr_zones.append(("SUPPORT", eb.get("nearest_support_zone"), "#2e7d32", 0.10))
        if isinstance(eb.get("nearest_resistance_zone"), dict):
            sr_zones.append(("RESIST", eb.get("nearest_resistance_zone"), "#b71c1c", 0.10))
        for kind, zone, color, alpha in sr_zones:
            lo, hi = _zone_bounds(zone)
            if lo is None or hi is None:
                continue
            ax.axhspan(lo, hi, color=color, alpha=alpha, lw=0.0, zorder=0.1)
            ax.text(
                x_right - 0.2,
                (lo + hi) / 2.0,
                kind,
                fontsize=6,
                color=color,
                ha="right",
                va="center",
                alpha=0.9,
            )

        for fvg in eb.get("active_fvgs") or []:
            if bool(fvg.get("mitigated")):
                continue
            lo, hi = _zone_bounds(fvg)
            if lo is None or hi is None:
                continue
            kind = str(fvg.get("type") or "FVG").upper()
            ax.axhspan(lo, hi, color="#00d4ff", alpha=0.13, lw=0.0, zorder=0.15)
            ax.annotate(
                f"{kind} FVG",
                xy=(x_right, (lo + hi) / 2.0),
                xytext=(-4, 0),
                textcoords="offset points",
                ha="right",
                va="center",
                fontsize=6,
                color="#00d4ff",
            )

        for ob in eb.get("order_blocks") or []:
            lo, hi = _zone_bounds(ob)
            if lo is None or hi is None:
                level = _to_float(ob.get("level"))
                if level is None:
                    continue
                lo = hi = level
            ob_type = str(ob.get("type") or "OB").upper()
            is_bull = "BULL" in ob_type
            color = "#00897b" if is_bull else "#c62828"
            ax.axhspan(lo, hi, color=color, alpha=0.10, lw=0.0, zorder=0.2)
            ax.text(
                x_right - 0.2,
                (lo + hi) / 2.0,
                ob_type,
                fontsize=6,
                color=color,
                ha="right",
                va="center",
            )

        bos = eb.get("bos_data") or {}
        choch = eb.get("choch_data") or {}
        structure_lines = []
        for key, label, color in (
            ("bos_bull_level", "BOS BULL", "#ffb300"),
            ("bos_bear_level", "BOS BEAR", "#ffb300"),
            ("choch_bull_level", "CHoCH BULL", "#ab47bc"),
            ("choch_bear_level", "CHoCH BEAR", "#ab47bc"),
        ):
            price = _to_float((bos if key.startswith("bos") else choch).get(key))
            if price is not None:
                structure_lines.append((price, label, color))

        for key, flag, label, color in (
            ("bos_bull", bos.get("bos_bull"), "BOS BULL", "#ffb300"),
            ("bos_bear", bos.get("bos_bear"), "BOS BEAR", "#ffb300"),
            ("choch_bull", choch.get("choch_bull"), "CHoCH BULL", "#ab47bc"),
            ("choch_bear", choch.get("choch_bear"), "CHoCH BEAR", "#ab47bc"),
        ):
            if not flag:
                continue
            price = _to_float(
                (bos if key.startswith("bos") else choch).get("level")
                or (bos if key.startswith("bos") else choch).get("price")
            )
            if price is not None:
                structure_lines.append((price, label, color))

        seen = set()
        for price, label, color in structure_lines:
            key = (round(float(price), 6), label)
            if key in seen:
                continue
            seen.add(key)
            ax.axhline(price, color=color, linestyle="--", linewidth=1.0, alpha=0.9)
            ax.text(x_right + 0.2, price, label, fontsize=6, color=color, va="center")
    except Exception as exc:
        log.debug(f"[chart_renderer] overlay draw warning: {exc}")

    if show_pattern_labels:
        try:
            patterns = detect_recent_patterns(df, lookback=pattern_lookback)
            for item in patterns:
                idx = item.get("index")
                label = str(item.get("label") or "")
                bias = str(item.get("bias") or "neutral")
                if idx not in x_by_idx:
                    continue
                x = x_by_idx[idx]
                row = df.loc[idx]
                if bias == "bull":
                    y = float(row["Low"]) * 0.998
                    color = "#26a69a"
                    va = "top"
                elif bias == "bear":
                    y = float(row["High"]) * 1.002
                    color = "#ef5350"
                    va = "bottom"
                else:
                    y = float(row["High"]) * 1.001
                    color = "#fbc02d"
                    va = "bottom"
                ax.text(
                    x,
                    y,
                    label,
                    fontsize=6,
                    color=color,
                    ha="center",
                    va=va,
                    bbox={"boxstyle": "round,pad=0.12", "fc": "#101826", "ec": color, "lw": 0.6, "alpha": 0.8},
                )
        except Exception as exc:
            log.debug(f"[chart_renderer] pattern label warning: {exc}")

    ax.set_xlim(left=max(-0.5, -1.0), right=float(len(df)) + 2.0)

    buf = io.BytesIO()
    fig.savefig(buf, dpi=max(100, int(dpi)), bbox_inches="tight", facecolor="#0c1017")
    plt.close(fig)
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode("utf-8")
    buf.close()
    return img_base64
