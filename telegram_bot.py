"""
telegram_bot.py — Interactive Telegram command centre for Sentinel Pro

Commands:
  /scan [class]    — Engine A full scan (crypto/forex/commodity/stock/index)
  /signal <pair>   — Analyse a specific pair
  /positions       — All open positions (MT5 + Bybit) with P&L
  /close <pair>    — Close a position (with confirmation)
  /status          — System health: connections, auto-trader, kill switch
  /balance         — MT5 + Bybit balances
  /pnl             — Performance stats from completed trades
  /auto on|off     — Toggle auto-trader
  /kill            — Emergency kill switch
  /resume          — Resume (lift kill switch)
  /decay           — Score decay for open positions
  /bt <pair>       — Quick backtest result
  /help            — This message
"""

import os
import logging
import threading
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from telegram_notify import send_signal_alert

log = logging.getLogger("sentinel")

# Pending signals: signal_key -> {signal_dict, ts}
_pending_signals: dict = {}
_pending_confirmations: dict = {}  # pair -> {exchange, direction, volume, ticket}

_BASE = "http://127.0.0.1:5000"
_TIMEOUT_FAST = 10   # status/balance calls
_TIMEOUT_SCAN = 120  # scan calls
_TIMEOUT_EXEC = 30   # execution calls

# ── Pair name fuzzy matching ──────────────────────────────────────────────────

_PAIR_ALIASES = {
    "btc": "BTC/USDT", "eth": "ETH/USDT", "sol": "SOL/USDT",
    "xrp": "XRP/USDT", "ada": "ADA/USDT", "doge": "DOGE/USDT",
    "link": "LINK/USDT", "ltc": "LTC/USDT", "bnb": "BNB/USDT",
    "sui": "SUI/USDT", "apt": "APT/USDT", "near": "NEAR/USDT",
    "inj": "INJ/USDT", "render": "RENDER/USDT", "matic": "MATIC/USDT",
    "avax": "AVAX/USDT", "dot": "DOT/USDT",
    "eurusd": "EUR/USD", "gbpusd": "GBP/USD", "usdjpy": "USD/JPY",
    "audusd": "AUD/USD", "nzdusd": "NZD/USD", "usdcad": "USD/CAD",
    "usdchf": "USD/CHF", "eurjpy": "EUR/JPY", "gbpjpy": "GBP/JPY",
    "audjpy": "AUD/JPY", "eurgbp": "EUR/GBP", "euraud": "EUR/AUD",
    "gbpaud": "GBP/AUD", "usdzar": "USD/ZAR",
    "gold": "XAU/USD", "xauusd": "XAU/USD", "silver": "XAG/USD",
    "oil": "WTI Oil", "wti": "WTI Oil",
    "spx": "S&P 500", "sp500": "S&P 500", "nas": "Nasdaq",
    "nasdaq": "Nasdaq", "dow": "Dow Jones",
    "aapl": "AAPL", "tsla": "TSLA", "nvda": "NVDA", "msft": "MSFT",
}


def _resolve_pair(raw: str) -> Optional[str]:
    """Fuzzy match user input to Athena display name."""
    s = raw.strip().upper()
    # Try direct match first (e.g. "ETH/USDT")
    if "/" in s or " " in s:
        return s
    # Try lowercase alias table
    lower = raw.strip().lower()
    if lower in _PAIR_ALIASES:
        return _PAIR_ALIASES[lower]
    # Try constructing USDT pair
    if not s.endswith("USDT") and not s.endswith("USD"):
        candidate = f"{s}/USDT"
        return candidate
    return s


def _prune_pending():
    """Remove signal entries older than 30 minutes."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    stale = [k for k, v in _pending_signals.items()
             if v.get("ts", datetime.now(timezone.utc)) < cutoff]
    for k in stale:
        del _pending_signals[k]


def _safe_json(resp) -> dict:
    try:
        data = resp.json()
    except Exception:
        text = (getattr(resp, "text", "") or "")[:200]
        sc = getattr(resp, "status_code", "?")
        return {"error": f"Bad response: {text or 'HTTP ' + str(sc)}"}
    if not getattr(resp, "ok", True):
        if isinstance(data, dict):
            data.setdefault("error", f"HTTP {resp.status_code}")
        else:
            data = {"error": f"HTTP {resp.status_code}"}
    return data


def _fmt_signal_card(sig: dict) -> str:
    """Format a signal dict as a clean Telegram card."""
    pair = sig.get("pair", sig.get("display", "?"))
    direction = sig.get("direction", "?")
    score = sig.get("confluenceScore", 0)
    max_score = sig.get("maxScore", 3.0)
    pct = round(score / max_score * 100) if max_score else 0
    regime = sig.get("trendState", sig.get("regimeName", "?"))
    price = sig.get("price", 0)
    sl = sig.get("sl", 0)
    tp1 = sig.get("tp1", 0)
    rr = sig.get("rr1", 0)
    dir_emoji = "🟢" if direction == "LONG" else "🔴"

    sl_pct = round(abs(price - sl) / price * 100, 2) if price and sl else 0
    tp_pct = round(abs(tp1 - price) / price * 100, 2) if price and tp1 else 0

    lines = [
        f"{dir_emoji} *{pair} — {direction}*",
        f"━━━━━━━━━━━━━━━",
        f"Score:  `{score:.2f} / {max_score:.1f}`  ({pct}%)",
        f"Regime: `{regime}`",
        f"Entry:  `{price}`",
        f"SL:     `{sl}`  (-{sl_pct}%)",
        f"TP1:    `{tp1}`  (+{tp_pct}%, RR 1:{rr:.1f})" if rr else f"TP1:    `{tp1}`",
    ]
    btc_bias = sig.get("btcBias")
    if btc_bias and btc_bias != "n/a":
        b_emoji = "✓" if (
            (direction == "LONG" and btc_bias == "bullish") or
            (direction == "SHORT" and btc_bias == "bearish")
        ) else "✗"
        lines.append(f"BTC:    `{btc_bias.capitalize()}` {b_emoji}")
    return "\n".join(lines)


def _fmt_position_card(pos: dict) -> str:
    """Format an open position as a Telegram card."""
    pair = pos.get("pair", "?")
    direction = pos.get("direction", "?")
    entry = pos.get("entry", pos.get("entryPrice", 0))
    profit = pos.get("profit", pos.get("unrealizedPnl", 0))
    sl = pos.get("sl", 0)
    tp = pos.get("tp", 0)
    vol = pos.get("volume", pos.get("contracts", 0))
    dir_emoji = "🟢" if direction == "LONG" else "🔴"
    pnl_emoji = "📈" if profit >= 0 else "📉"
    pnl_sign = "+" if profit >= 0 else ""

    lines = [
        f"{dir_emoji} *{pair}* — {direction}",
        f"Entry: `{entry}` | Vol: `{vol}`",
        f"{pnl_emoji} P&L: `{pnl_sign}{profit:.2f} USDT`",
    ]
    if sl:
        lines.append(f"SL: `{sl}` | TP: `{tp}`")
    return "\n".join(lines)


def start_telegram_bot():
    """Start the Telegram bot in a background thread."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.warning("[TELEGRAM] Bot token or chat_id not set — bot disabled")
        return
    t = threading.Thread(target=_run_bot, args=(token, chat_id), daemon=True)
    t.start()
    log.warning("[TELEGRAM] Bot started in background thread")


def _run_bot(token: str, chat_id: str):
    import traceback
    try:
        _build_and_run(token, chat_id)
    except Exception as e:
        log.error(f"[TELEGRAM] Bot failed: {e}\n{traceback.format_exc()}")


def _build_and_run(token: str, chat_id: str):
    import requests as req
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

    app = Application.builder().token(token).build()
    app.bot_data["chat_id"] = chat_id

    def _guard(update: Update) -> bool:
        """Reject messages from unknown chat IDs silently."""
        return str(update.effective_chat.id) == str(chat_id)

    async def _run_in_thread(fn):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn)

    # ── /help ─────────────────────────────────────────────────────────────────

    async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        text = (
            "🤖 *Sentinel Pro — Commands*\n\n"
            "`/scan crypto`       Engine A scan by class\n"
            "`/signal ETH/USDT`   Analyse a single pair\n"
            "`/positions`         Open positions + P&L\n"
            "`/close ETH/USDT`    Close a position\n"
            "`/status`            System health\n"
            "`/balance`           MT5 + Bybit balances\n"
            "`/pnl`               Performance stats\n"
            "`/auto on|off`       Toggle auto-trader\n"
            "`/decay`             Score decay for open trades\n"
            "`/bt EUR/USD`        Quick backtest result\n"
            "`/kill`              Emergency stop\n"
            "`/resume`            Lift kill switch\n"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    # ── /status ───────────────────────────────────────────────────────────────

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            mt5 = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/mt5-status", timeout=_TIMEOUT_FAST)))
            bybit = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/bybit-status", timeout=_TIMEOUT_FAST)))
            auto = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/auto-trade", timeout=_TIMEOUT_FAST)))

            mt5_icon = "🟢" if mt5.get("connected") else "🔴"
            bybit_icon = "🟢" if bybit.get("connected") else "🔴"
            auto_icon = "🟢 ON" if auto.get("enabled") else "⚫ OFF"
            mt5_pos = mt5.get("openPositions", 0)
            bybit_pos = bybit.get("openPositions", 0)
            trades_today = auto.get("tradesToday", 0)
            max_daily = auto.get("maxDaily", 3)
            health = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/health", timeout=_TIMEOUT_FAST)))
            ks = health.get("killSwitch") if isinstance(health, dict) else None
            ks_note = f"\n🛑 Kill switch: `{'ON' if ks else 'OFF'}`" if ks is not None else ""

            mt5_bal = (mt5.get("account") or {}).get("balance", 0)
            bybit_bal = (bybit.get("account") or {}).get("balance", 0)

            text = (
                f"📊 *Sentinel Pro Status*\n\n"
                f"{mt5_icon} *MT5*: `{mt5_bal:,.2f}` | {mt5_pos} pos\n"
                f"{bybit_icon} *Bybit*: `{bybit_bal:,.2f} USDT` | {bybit_pos} pos\n\n"
                f"🤖 Auto-trader: *{auto_icon}*\n"
                f"📅 Trades today: `{trades_today}/{max_daily}`{ks_note}"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"⚠️ Athena offline — check server\n`{str(e)[:100]}`", parse_mode="Markdown")

    # ── /balance ──────────────────────────────────────────────────────────────

    async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            mt5 = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/mt5-status", timeout=_TIMEOUT_FAST)))
            bybit = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/bybit-status", timeout=_TIMEOUT_FAST)))
            mt5_acc = mt5.get("account", {})
            bybit_acc = bybit.get("account", {})
            text = (
                f"💰 *Balance*\n\n"
                f"*MT5:*  `${mt5_acc.get('balance', 0):,.2f}` (equity `${mt5_acc.get('equity', 0):,.2f}`)\n"
                f"*Bybit:* `${bybit_acc.get('balance', 0):,.2f} USDT`"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server", parse_mode="Markdown")

    # ── /positions ────────────────────────────────────────────────────────────

    async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            mt5_resp = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/mt5-positions", timeout=_TIMEOUT_FAST)))
            bybit_resp = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/bybit-status", timeout=_TIMEOUT_FAST)))

            mt5_pos = mt5_resp.get("positions", [])
            bybit_pos = bybit_resp.get("positions", [])
            all_pos = [(p, "mt5") for p in mt5_pos] + [(p, "bybit") for p in bybit_pos]

            if not all_pos:
                await update.message.reply_text("📭 No open positions")
                return

            total_pnl = sum(float(p.get("profit", 0) or 0) for p, _ in all_pos)
            pnl_emoji = "📈" if total_pnl >= 0 else "📉"
            await update.message.reply_text(
                f"{pnl_emoji} *{len(all_pos)} open position(s) | Total P&L: `{'+' if total_pnl >= 0 else ''}{total_pnl:.2f}`*",
                parse_mode="Markdown"
            )

            for pos, exchange in all_pos:
                card = _fmt_position_card(pos)
                pair = pos.get("pair", "?")
                direction = pos.get("direction", "")
                vol = pos.get("volume", pos.get("contracts", 0))
                ticket = pos.get("ticket", "")

                close_key = f"close_{exchange}_{pair}_{direction}_{vol}_{ticket}"
                _pending_confirmations[close_key] = {
                    "exchange": exchange, "pair": pair,
                    "direction": direction, "volume": vol, "ticket": ticket
                }

                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📉 Close Position", callback_data=f"close_confirm:{close_key}"),
                    InlineKeyboardButton("📊 Decay", callback_data=f"decay_pair:{pair}"),
                ]])
                await update.message.reply_text(card, parse_mode="Markdown", reply_markup=keyboard)

        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server")

    # ── /scan ─────────────────────────────────────────────────────────────────

    async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        args = context.args
        asset_class = args[0].lower() if args else ""
        valid = {"crypto", "forex", "stock", "stocks", "commodity", "index"}
        if asset_class not in valid:
            await update.message.reply_text(
                "Usage: `/scan crypto|forex|commodity|stock|index`",
                parse_mode="Markdown"
            )
            return
        if asset_class == "stocks":
            asset_class = "stock"

        msg = await update.message.reply_text(f"⏳ Scanning *{asset_class}*...", parse_mode="Markdown")

        try:
            resp = await _run_in_thread(lambda: _safe_json(req.post(
                f"{_BASE}/api/scan",
                json={"style": "auto", "asset_class": asset_class},
                timeout=_TIMEOUT_SCAN,
            )))

            if resp.get("error"):
                await msg.edit_text(f"❌ Scan error: {resp['error']}")
                return

            signals = resp.get("tradeSignals", resp.get("signals", []))
            watchlist = resp.get("watchlist", [])

            await msg.edit_text(
                f"📊 *Scan — {asset_class.upper()}*\n"
                f"✅ Signals: {len(signals)} | 👁 Watchlist: {len(watchlist)}",
                parse_mode="Markdown"
            )

            if not signals:
                if watchlist:
                    top = watchlist[0]
                    await update.message.reply_text(
                        f"Best near-miss: *{top.get('pair')}* {top.get('direction')} score={top.get('confluenceScore', 0):.2f}",
                        parse_mode="Markdown"
                    )
                return

            for sig in signals[:3]:
                await _send_signal_card(update.effective_chat.id, sig, context)

        except Exception:
            await msg.edit_text("⚠️ Athena offline — check server")

    # ── /signal ───────────────────────────────────────────────────────────────

    async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        if not context.args:
            await update.message.reply_text("Usage: `/signal ETH/USDT`", parse_mode="Markdown")
            return

        raw = " ".join(context.args)
        pair = _resolve_pair(raw)

        msg = await update.message.reply_text(f"⏳ Analysing *{pair}*...", parse_mode="Markdown")

        try:
            resp = await _run_in_thread(lambda: _safe_json(req.post(
                f"{_BASE}/api/scan",
                json={"style": "auto"},
                timeout=_TIMEOUT_SCAN,
            )))

            if resp.get("error"):
                await msg.edit_text(f"❌ Error: {resp['error']}")
                return

            all_sigs = resp.get("tradeSignals", []) + resp.get("watchlist", [])

            def _sig_disp(s):
                return (s.get("pair") or s.get("display") or "").upper()

            want = (pair or "").upper()
            match = next((s for s in all_sigs if _sig_disp(s) == want), None)

            if not match:
                await msg.edit_text(f"⚠️ No signal found for *{pair}* in this scan cycle.", parse_mode="Markdown")
                return

            await msg.delete()
            await _send_signal_card(update.effective_chat.id, match, context)

        except Exception:
            await msg.edit_text("⚠️ Athena offline — check server")

    # ── /close ────────────────────────────────────────────────────────────────

    async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        if not context.args:
            await update.message.reply_text("Usage: `/close ETH/USDT`", parse_mode="Markdown")
            return

        raw = " ".join(context.args)
        pair = _resolve_pair(raw)

        try:
            mt5_resp = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/mt5-positions", timeout=_TIMEOUT_FAST)))
            bybit_resp = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/bybit-status", timeout=_TIMEOUT_FAST)))
            mt5_pos = mt5_resp.get("positions", [])
            bybit_pos = bybit_resp.get("positions", [])
            all_pos = [(p, "mt5") for p in mt5_pos] + [(p, "bybit") for p in bybit_pos]

            match = next((p for p, ex in all_pos if (p.get("pair", "").upper() == pair.upper())), None)
            exchange = next((ex for p, ex in all_pos if (p.get("pair", "").upper() == pair.upper())), None)

            if not match:
                await update.message.reply_text(f"❌ No open position found for *{pair}*", parse_mode="Markdown")
                return

            close_key = f"close_{exchange}_{pair}_{match.get('direction')}_{match.get('volume')}_{match.get('ticket')}"
            _pending_confirmations[close_key] = {
                "exchange": exchange, "pair": pair,
                "direction": match.get("direction"), "volume": match.get("volume"),
                "ticket": match.get("ticket")
            }

            card = _fmt_position_card(match)
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Confirm Close", callback_data=f"close_confirm:{close_key}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"close_cancel:{close_key}"),
            ]])
            await update.message.reply_text(
                f"⚠️ *Confirm close?*\n\n{card}", parse_mode="Markdown", reply_markup=keyboard
            )

        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server")

    # ── /kill ─────────────────────────────────────────────────────────────────

    async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            await _run_in_thread(lambda: req.post(f"{_BASE}/api/killswitch", json={"action": "on"}, timeout=_TIMEOUT_FAST))
            await update.message.reply_text("🛑 *KILL SWITCH ACTIVATED*\nAll auto-trading stopped.", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server")

    # ── /resume ───────────────────────────────────────────────────────────────

    async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            await _run_in_thread(lambda: req.post(f"{_BASE}/api/killswitch", json={"action": "off"}, timeout=_TIMEOUT_FAST))
            await update.message.reply_text("✅ *Kill switch lifted — auto-trading resumed*", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server")

    # ── /auto ─────────────────────────────────────────────────────────────────

    async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        args = context.args
        action = args[0].lower() if args else "toggle"
        if action not in ("on", "off", "toggle"):
            await update.message.reply_text("Usage: `/auto on|off`", parse_mode="Markdown")
            return
        try:
            resp = await _run_in_thread(lambda: _safe_json(req.post(
                f"{_BASE}/api/auto-trade", json={"action": action}, timeout=_TIMEOUT_FAST
            )))
            enabled = resp.get("enabled", False)
            icon = "🟢 ON" if enabled else "⚫ OFF"
            trades = resp.get("tradesToday", 0)
            max_d = resp.get("maxDaily", 3)
            await update.message.reply_text(
                f"🤖 Auto-trader: *{icon}*\n📅 Today: `{trades}/{max_d}`",
                parse_mode="Markdown"
            )
        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server")

    # ── /pnl ──────────────────────────────────────────────────────────────────

    async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            resp = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/performance", timeout=_TIMEOUT_FAST)))
            if resp.get("error"):
                await update.message.reply_text(f"⚠️ {resp.get('error')}")
                return
            if resp.get("total_trades", 0) == 0:
                await update.message.reply_text("📭 No completed trades yet")
                return

            total = resp.get("total_trades", 0)
            wr = resp.get("win_rate", 0)
            total_r = resp.get("total_r", 0)
            avg_r = resp.get("average_r_multiple", 0)
            pf = resp.get("profit_factor")
            dd = resp.get("max_drawdown_pct", 0)

            text = (
                f"📈 *Performance*\n\n"
                f"Trades:  `{total}`\n"
                f"Win Rate: `{wr}%`\n"
                f"Total R:  `{'+' if total_r >= 0 else ''}{total_r:.2f}R`\n"
                f"Avg R:    `{'+' if avg_r >= 0 else ''}{avg_r:.3f}R`\n"
                f"PF:       `{pf or 'N/A'}`\n"
                f"Max DD:   `{dd:.2f}%`"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server")

    # ── /decay ────────────────────────────────────────────────────────────────

    async def cmd_decay(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            resp = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/score-decay", timeout=_TIMEOUT_FAST)))
            relevant = {k: v for k, v in resp.items() if isinstance(v, dict) and v.get("decay", 0) > 0.3}
            if not relevant:
                await update.message.reply_text("✅ No significant score decay on open positions")
                return

            lines = ["📉 *Score Decay — Open Positions*\n"]
            for pair_name, d in sorted(relevant.items(), key=lambda x: x[1].get("decay", 0), reverse=True):
                entry_dir = d.get("direction", "?")
                cur_dir = d.get("currentDirection", "?")
                flip = " ⚠️ DIRECTION FLIP" if entry_dir != cur_dir else ""
                decay_val = d.get("decay", 0)
                emoji = "🔴" if decay_val >= 1.5 else "🟡"
                lines.append(
                    f"{emoji} *{pair_name}*: `{d.get('entryScore', 0):.2f}` → `{d.get('currentScore', 0):.2f}` "
                    f"(Δ{decay_val:.2f}){flip}"
                )
                ai_verdict = d.get("aiVerdict")
                if ai_verdict:
                    v_emoji = "🚨" if ai_verdict == "EXIT" else "👁" if ai_verdict == "WATCH" else "✋"
                    urgency = d.get("aiUrgency", "")
                    reasoning = d.get("aiReasoning", "")
                    lines.append(
                        f"   {v_emoji} AI: *{ai_verdict}*"
                        + (f" `[{urgency}]`" if urgency else "")
                        + (f" — _{reasoning}_" if reasoning else "")
                    )
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server")

    # ── /bt ───────────────────────────────────────────────────────────────────

    async def cmd_bt(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        if not context.args:
            await update.message.reply_text("Usage: `/bt EUR/USD`", parse_mode="Markdown")
            return
        raw = " ".join(context.args)
        pair = _resolve_pair(raw)
        msg = await update.message.reply_text(f"⏳ Running backtest for *{pair}*...", parse_mode="Markdown")
        try:
            resp = await _run_in_thread(lambda: _safe_json(req.post(
                f"{_BASE}/api/backtest", json={"pair": pair}, timeout=90
            )))
            if resp.get("error"):
                await msg.edit_text(f"❌ {resp['error']}")
                return
            sqn = resp.get("sqn", 0)
            wr = resp.get("winRate", resp.get("win_rate", 0))
            trades = resp.get("totalTrades", resp.get("total_trades", 0))
            oos_sqn = (resp.get("wfSplit") or {}).get("oos_sqn") or 0
            sqn_emoji = "✅" if sqn >= 2.0 else "⚠️" if sqn >= 1.0 else "❌"
            text = (
                f"📊 *Backtest — {pair}*\n\n"
                f"SQN: `{sqn}` {sqn_emoji}  |  WR: `{wr}%`\n"
                f"Trades: `{trades}`  |  OOS SQN: `{oos_sqn}`"
            )
            await msg.edit_text(text, parse_mode="Markdown")
        except Exception:
            await msg.edit_text("⚠️ Athena offline — check server")

    # ── Helper: send signal card with execute buttons ─────────────────────────

    async def _send_signal_card(chat_id_val, sig: dict, context):
        _prune_pending()
        signal_key = f"{sig.get('pair', sig.get('display', '?'))}_{sig.get('direction', '?')}_{int(datetime.now().timestamp())}"
        _pending_signals[signal_key] = {"signal": sig, "ts": datetime.now(timezone.utc)}

        card = _fmt_signal_card(sig)
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⚡ SCALP", callback_data=f"exec_scalp:{signal_key}"),
                InlineKeyboardButton("📊 INTRADAY", callback_data=f"exec_intraday:{signal_key}"),
                InlineKeyboardButton("🌊 SWING", callback_data=f"exec_swing:{signal_key}"),
            ],
            [
                InlineKeyboardButton("🧠 AI Analysis", callback_data=f"ai_analyse:{signal_key}"),
                InlineKeyboardButton("❌ Skip", callback_data=f"skip:{signal_key}"),
            ],
        ])
        await context.bot.send_message(
            chat_id=chat_id_val, text=card, parse_mode="Markdown", reply_markup=keyboard
        )

    # ── Button callbacks ──────────────────────────────────────────────────────

    async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        query = update.callback_query
        await query.answer()
        _prune_pending()

        data = query.data
        parts = data.split(":", 1)
        action = parts[0]
        key = parts[1] if len(parts) > 1 else ""

        # ── Skip ──
        if action == "skip":
            await query.edit_message_reply_markup(reply_markup=None)
            if key in _pending_signals:
                del _pending_signals[key]
            return

        # ── Execute ──
        if action.startswith("exec_"):
            entry = _pending_signals.get(key)
            if not entry:
                await query.message.reply_text("❌ Signal expired — re-scan to refresh")
                return
            sig = entry["signal"]
            pip_mode = action.replace("exec_", "")
            if pip_mode == "swing":
                pip_mode = None

            await query.message.reply_text(
                f"⏳ Executing *{sig.get('pair', sig.get('display'))} {sig.get('direction')} {pip_mode or 'SWING'}*...",
                parse_mode="Markdown"
            )
            try:
                resp = await _run_in_thread(lambda: _safe_json(req.post(
                    f"{_BASE}/api/quick-execute",
                    json={
                        "signal": sig,
                        "pip_mode": pip_mode,
                        "sizing_override": 1.0,
                    },
                    timeout=_TIMEOUT_EXEC,
                )))
                if resp.get("success"):
                    ticket = resp.get("ticket", "?")
                    entry_px = resp.get("entryPrice", sig.get("price", "?"))
                    sl = resp.get("sl", sig.get("sl", "?"))
                    tp = resp.get("tp", sig.get("tp1", "?"))
                    await query.message.reply_text(
                        f"✅ *EXECUTED*\n"
                        f"*{sig.get('pair', sig.get('display'))} {sig.get('direction')}* @ `{entry_px}`\n"
                        f"SL: `{sl}` | TP: `{tp}`\n"
                        f"Ticket: `#{ticket}`",
                        parse_mode="Markdown"
                    )
                    if key in _pending_signals:
                        del _pending_signals[key]
                else:
                    err = resp.get("error", "Unknown error")
                    await query.message.reply_text(f"❌ Execution failed: `{err}`", parse_mode="Markdown")
            except Exception:
                await query.message.reply_text("⚠️ Athena offline — check server")
            return

        # ── AI Analysis ──
        if action == "ai_analyse":
            entry = _pending_signals.get(key)
            if not entry:
                await query.message.reply_text("❌ Signal expired — re-scan to refresh")
                return
            sig = entry["signal"]
            await query.message.reply_text("🧠 Running AI analysis...")
            try:
                resp = await _run_in_thread(lambda: _safe_json(req.post(
                    f"{_BASE}/api/analyze",
                    json={"signal": sig, "stylePreference": "auto"},
                    timeout=60,
                )))
                if resp.get("error"):
                    await query.message.reply_text(f"❌ AI error: {resp['error']}")
                    return

                grade = resp.get("grade", "?")
                edge = resp.get("edgeProbability", 0)
                verdict = resp.get("verdict", "")
                risk_level = resp.get("riskLevel", "")
                warnings = resp.get("warnings", [])

                grade_emoji = {"A": "✅", "B": "✅", "C": "⚠️", "D": "🚫", "F": "🚫"}.get(str(grade).strip()[:1].upper(), "❓")
                warn_text = "\n".join(f"• {w}" for w in warnings[:3]) if warnings else "None"

                try:
                    ev = float(edge)
                    edge_f = ev / 100.0 if ev > 1.0 else ev
                except (TypeError, ValueError):
                    edge_f = 0.0

                result_text = (
                    f"{grade_emoji} *Grade: {grade}*  |  Edge: `{edge_f:.0%}`\n"
                    f"Risk: `{risk_level}`\n\n"
                    f"_{str(verdict)[:300]}_\n\n"
                    f"*Warnings:*\n{warn_text}"
                )

                g0 = str(grade).strip()[:1].upper()
                if g0 in ("A", "B"):
                    keyboard = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("⚡ SCALP", callback_data=f"exec_scalp:{key}"),
                            InlineKeyboardButton("📊 INTRADAY", callback_data=f"exec_intraday:{key}"),
                            InlineKeyboardButton("🌊 SWING", callback_data=f"exec_swing:{key}"),
                        ],
                        [InlineKeyboardButton("❌ Cancel", callback_data=f"skip:{key}")],
                    ])
                    await query.message.reply_text(result_text, parse_mode="Markdown", reply_markup=keyboard)
                else:
                    await query.message.reply_text(
                        f"{result_text}\n\n🚫 *Trade blocked — Grade {grade}*",
                        parse_mode="Markdown"
                    )
                    if key in _pending_signals:
                        del _pending_signals[key]

            except Exception:
                await query.message.reply_text("⚠️ Athena offline — check server")
            return

        # ── Decay for specific pair ──
        if action == "decay_pair":
            pair_name = key
            try:
                resp = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/score-decay", timeout=_TIMEOUT_FAST)))
                d = resp.get(pair_name)
                if not d:
                    await query.message.reply_text(f"✅ No decay data for *{pair_name}*", parse_mode="Markdown")
                    return
                decay_val = d.get("decay", 0)
                entry_dir = d.get("direction", "?")
                cur_dir = d.get("currentDirection", "?")
                flip = "\n⚠️ *DIRECTION FLIP — consider exiting*" if entry_dir != cur_dir else ""
                emoji = "🔴" if decay_val >= 1.5 else "🟡"
                await query.message.reply_text(
                    f"{emoji} *{pair_name} Score Decay*\n"
                    f"Entry: `{d.get('entryScore', 0):.2f}` → Now: `{d.get('currentScore', 0):.2f}` (Δ{decay_val:.2f})\n"
                    f"Entry dir: `{entry_dir}` | Current: `{cur_dir}`{flip}",
                    parse_mode="Markdown"
                )
            except Exception:
                await query.message.reply_text("⚠️ Athena offline — check server")
            return

        # ── Close confirm ──
        if action == "close_confirm":
            conf = _pending_confirmations.get(key)
            if not conf:
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text("❌ Position data expired")
                return
            try:
                payload = {
                    "exchange": conf["exchange"],
                    "pair": conf["pair"],
                    "direction": conf["direction"],
                    "volume": conf["volume"],
                }
                if conf["exchange"] == "mt5" and conf.get("ticket"):
                    payload["ticket"] = conf["ticket"]

                resp = await _run_in_thread(lambda: _safe_json(req.post(
                    f"{_BASE}/api/close-position", json=payload, timeout=_TIMEOUT_EXEC
                )))
                await query.edit_message_reply_markup(reply_markup=None)
                if resp.get("success"):
                    await query.message.reply_text(
                        f"✅ *Position closed*\n{conf['pair']} {conf['direction']}",
                        parse_mode="Markdown"
                    )
                else:
                    await query.message.reply_text(f"❌ Close failed: {resp.get('error', '?')}")
                if key in _pending_confirmations:
                    del _pending_confirmations[key]
            except Exception:
                await query.message.reply_text("⚠️ Athena offline — check server")
            return

        # ── Close cancel ──
        if action == "close_cancel":
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("❌ Close cancelled")
            if key in _pending_confirmations:
                del _pending_confirmations[key]
            return

    # Register all handlers
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("pos", cmd_positions))
    app.add_handler(CommandHandler("close", cmd_close))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("bal", cmd_balance))
    app.add_handler(CommandHandler("pnl", cmd_pnl))
    app.add_handler(CommandHandler("auto", cmd_auto))
    app.add_handler(CommandHandler("decay", cmd_decay))
    app.add_handler(CommandHandler("bt", cmd_bt))
    app.add_handler(CommandHandler("kill", cmd_kill))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CallbackQueryHandler(button_callback))

    log.warning("[TELEGRAM] Bot handlers registered, starting polling...")
    app.run_polling(drop_pending_updates=True)
