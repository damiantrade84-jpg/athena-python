"""
telegram_bot.py — Two-way Telegram command centre for Sentinel Pro

Commands:
  /scan <class>    — Run Engine C consensus scan
  /status          — Account status + open positions
  /positions       — Detailed open positions
  /kill            — Emergency kill switch
  /resume          — Resume auto-trading
  /ai <pair>       — AI Vision analysis on a pair
  /balance         — Account balance
  /help            — List commands

Inline buttons:
  🧠 AI Confirm    — Run AI Vision on consensus signal
  ⚡ SCALP         — Execute at scalp sizing
  📊 INTRADAY      — Execute at intraday sizing
  🌊 SWING         — Execute at swing sizing
  ❌ Skip           — Dismiss signal
"""

import os
import json
import logging
import threading
import asyncio
from typing import Optional
from datetime import datetime, timezone

log = logging.getLogger("sentinel")

# Store pending consensus signals for button callbacks
_pending_signals = {}  # message_id -> consensus dict


def start_telegram_bot():
    """Start the Telegram bot in a background thread.
    Called from athena.py on startup."""
    
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    
    if not token or not chat_id:
        log.warning("[TELEGRAM] Bot token or chat_id not set — bot disabled")
        return
    
    def _run_bot():
        import traceback
        try:
            from telegram.ext import Application, CommandHandler, CallbackQueryHandler
            # Build and run the application — run_polling() manages its own event loop
            # Must be called from a plain thread (not inside an existing async context)
            _build_and_run(token, chat_id)
        except Exception as e:
            log.error(f"[TELEGRAM] Bot failed to start: {e}")
            log.error(f"[TELEGRAM] Full traceback:\n{traceback.format_exc()}")
    
    t = threading.Thread(target=_run_bot, daemon=True)
    t.start()
    log.warning("[TELEGRAM] Bot started in background thread")


def _build_and_run(token: str, chat_id: str):
    """Build and run the bot synchronously. Application.run_polling() manages its own event loop.
    Must be called from a plain non-async thread."""
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application, CommandHandler, CallbackQueryHandler, ContextTypes
    )
    
    app = Application.builder().token(token).build()
    
    # Store chat_id for sending proactive messages
    app.bot_data["chat_id"] = chat_id
    
    # ── Command Handlers ──
    
    async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "🤖 *Sentinel Pro — Commands*\n\n"
            "`/scan crypto` — Engine C consensus (crypto)\n"
            "`/scan forex` — Engine C consensus (forex)\n"
            "`/scan stocks` — Engine C consensus (stocks)\n"
            "`/scan commodity` — Engine C consensus (commodities)\n"
            "`/scan index` — Engine C consensus (indices)\n"
            "`/status` — Account + open positions\n"
            "`/positions` — Detailed positions\n"
            "`/kill` — Emergency stop all trading\n"
            "`/resume` — Resume auto-trading\n"
            "`/balance` — Account balance\n"
            "`/help` — This message"
        )
        await update.message.reply_text(text, parse_mode="Markdown")
    
    async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args
        if not args:
            await update.message.reply_text(
                "Usage: `/scan crypto` or `/scan forex`", parse_mode="Markdown"
            )
            return
        
        asset_class = args[0].lower()
        valid = ["crypto", "forex", "stock", "stocks", "commodity", "index"]
        if asset_class not in valid:
            await update.message.reply_text(
                f"Invalid class. Use: {', '.join(valid)}", parse_mode="Markdown"
            )
            return
        
        if asset_class == "stocks":
            asset_class = "stock"
        
        await update.message.reply_text(
            f"⏳ Running Engine C consensus scan on *{asset_class}*...",
            parse_mode="Markdown",
        )
        
        # Run scan in thread pool to avoid blocking
        import requests as req
        try:
            resp = req.post(
                "http://127.0.0.1:5000/api/engine-c-scan",
                json={"assetClass": asset_class, "style": "auto"},
                timeout=180,
            )
            data = resp.json()
            
            if data.get("error"):
                await update.message.reply_text(f"❌ Error: {data['error']}")
                return
            
            # Send aligned signals
            aligned = data.get("aligned", [])
            a_only = data.get("a_only", [])
            b_only = data.get("b_only", [])
            conflict = data.get("conflict", [])
            
            summary = (
                f"📊 *Engine C Scan — {asset_class.upper()}*\n\n"
                f"✅ Aligned: {len(aligned)}\n"
                f"⚠️ A-only: {len(a_only)}\n"
                f"👁 B-only: {len(b_only)}\n"
                f"🚫 Conflict: {len(conflict)}"
            )
            await update.message.reply_text(summary, parse_mode="Markdown")
            
            # Send each aligned signal as interactive card
            for c in aligned[:5]:  # limit to top 5
                await _send_consensus_card(update.effective_chat.id, c, context)
            
            # Send a_only as info
            for c in a_only[:3]:
                await _send_consensus_card(update.effective_chat.id, c, context, category="a_only")
            
        except Exception as e:
            await update.message.reply_text(f"❌ Scan failed: {str(e)[:200]}")
    
    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        import requests as req
        try:
            # Get MT5 status
            mt5 = req.get("http://127.0.0.1:5000/api/mt5-status", timeout=10).json()
            bybit = req.get("http://127.0.0.1:5000/api/bybit-status", timeout=10).json()
            
            mt5_bal = mt5.get("balance", 0)
            mt5_eq = mt5.get("equity", 0)
            mt5_pos = mt5.get("positions", 0)
            bybit_bal = bybit.get("balance", 0)
            
            text = (
                f"📊 *Sentinel Pro Status*\n\n"
                f"*MT5:* {'🟢' if mt5.get('connected') else '🔴'}\n"
                f"  Balance: `${mt5_bal:,.2f}`\n"
                f"  Equity: `${mt5_eq:,.2f}`\n"
                f"  Positions: `{mt5_pos}`\n\n"
                f"*Bybit:* {'🟢' if bybit.get('connected') else '🔴'}\n"
                f"  Balance: `${bybit_bal:,.2f}`"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Status error: {str(e)[:200]}")
    
    async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
        import requests as req
        try:
            resp = req.post(
                "http://127.0.0.1:5000/api/kill-switch",
                json={"enabled": True},
                timeout=10,
            )
            await update.message.reply_text(
                "🛑 *KILL SWITCH ACTIVATED*\nAll auto-trading stopped.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Kill switch error: {e}")
    
    async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
        import requests as req
        try:
            resp = req.post(
                "http://127.0.0.1:5000/api/kill-switch",
                json={"enabled": False},
                timeout=10,
            )
            await update.message.reply_text(
                "✅ *Auto-trading resumed*",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Resume error: {e}")
    
    async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
        import requests as req
        try:
            mt5 = req.get("http://127.0.0.1:5000/api/mt5-status", timeout=10).json()
            bybit = req.get("http://127.0.0.1:5000/api/bybit-status", timeout=10).json()
            text = (
                f"💰 *Balance*\n"
                f"MT5: `${mt5.get('balance', 0):,.2f}`\n"
                f"Bybit: `${bybit.get('balance', 0):,.2f}`"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ {e}")
    
    # ── Inline Button Callbacks ──
    
    async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        parts = data.split(":")
        action = parts[0]
        signal_key = parts[1] if len(parts) > 1 else ""
        
        consensus = _pending_signals.get(signal_key)
        
        if action == "skip":
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("⏭ Signal skipped")
            if signal_key in _pending_signals:
                del _pending_signals[signal_key]
            return
        
        if action == "ai_confirm":
            if not consensus:
                await query.message.reply_text("❌ Signal data expired")
                return
            
            await query.message.reply_text("⏳ Running AI Vision analysis...")
            
            # Call chart-analysis via internal API
            import requests as req
            try:
                # First render chart server-side
                from chart_renderer import render_chart_image
                
                pair_display = consensus.get("display", "")
                candle_resp = req.get(
                    f"http://127.0.0.1:5000/api/candles?symbol={consensus.get('symbol', '')}&tf=H4&limit=150",
                    timeout=30,
                ).json()
                candles = candle_resp.get("candles", [])
                
                if candles:
                    img_base64 = render_chart_image(
                        candles=candles,
                        entry=consensus.get("entry"),
                        sl=consensus.get("sl"),
                        tp=consensus.get("tp"),
                        direction=consensus.get("direction"),
                        title=f"{pair_display} H4",
                    )
                    
                    # Send chart image to user
                    import base64
                    import io
                    img_bytes = base64.b64decode(img_base64)
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=io.BytesIO(img_bytes),
                        caption=f"📊 {pair_display} H4 — Engine C",
                    )
                    
                    # Run AI Vision
                    vision_resp = req.post(
                        "http://127.0.0.1:5000/api/chart-analysis",
                        json={
                            "image": f"data:image/png;base64,{img_base64}",
                            "symbol": consensus.get("symbol", ""),
                            "tf": "H4",
                            "signal": {
                                "direction": consensus.get("direction"),
                                "confluenceScore": consensus.get("conviction"),
                                "price": consensus.get("entry"),
                                "sl": consensus.get("sl"),
                                "tp1": consensus.get("tp"),
                                "type": consensus.get("type"),
                                "regime": consensus.get("regime"),
                            },
                            "engineB": consensus.get("engine_b_raw", {}),
                        },
                        timeout=60,
                    ).json()
                    
                    analysis = vision_resp.get("analysis", "No analysis")
                    
                    # Apply vision to consensus
                    confirm_resp = req.post(
                        "http://127.0.0.1:5000/api/engine-c-confirm",
                        json={"consensus": consensus, "vision": vision_resp},
                        timeout=30,
                    ).json()
                    
                    # Update pending signal
                    _pending_signals[signal_key] = confirm_resp
                    
                    # Send result
                    rating = confirm_resp.get("vision_rating", "?")
                    action_str = confirm_resp.get("vision_action", "?")
                    
                    emoji = "✅" if action_str == "confirm" else "⚠️" if action_str == "weaken" else "🚫"
                    
                    result_text = f"{emoji} *{rating}*\n\n{analysis[:500]}"
                    
                    if confirm_resp.get("trade"):
                        # Show execute buttons
                        keyboard = InlineKeyboardMarkup([
                            [
                                InlineKeyboardButton("⚡ SCALP", callback_data=f"exec_scalp:{signal_key}"),
                                InlineKeyboardButton("📊 INTRADAY", callback_data=f"exec_intraday:{signal_key}"),
                                InlineKeyboardButton("🌊 SWING", callback_data=f"exec_swing:{signal_key}"),
                            ],
                            [InlineKeyboardButton("❌ Cancel", callback_data=f"skip:{signal_key}")],
                        ])
                        await query.message.reply_text(
                            result_text, parse_mode="Markdown", reply_markup=keyboard
                        )
                    else:
                        await query.message.reply_text(
                            f"{result_text}\n\n🚫 Trade blocked by Vision",
                            parse_mode="Markdown",
                        )
                else:
                    await query.message.reply_text("❌ Could not fetch candle data")
                    
            except Exception as e:
                await query.message.reply_text(f"❌ AI Vision error: {str(e)[:300]}")
            return
        
        if action.startswith("exec_"):
            if not consensus:
                await query.message.reply_text("❌ Signal data expired")
                return
            
            pip_mode = action.replace("exec_", "")
            if pip_mode == "swing":
                pip_mode = None
            
            await query.message.reply_text(
                f"⏳ Executing {consensus.get('display', '?')} "
                f"{consensus.get('direction', '?')} {pip_mode or 'SWING'}..."
            )
            
            import requests as req
            try:
                signal = {
                    "pair": consensus.get("display"),
                    "display": consensus.get("display"),
                    "symbol": consensus.get("symbol"),
                    "type": consensus.get("type"),
                    "direction": consensus.get("direction"),
                    "price": consensus.get("entry"),
                    "sl": consensus.get("sl"),
                    "tp1": consensus.get("tp"),
                    "tp2": consensus.get("tp"),
                    "confluenceScore": consensus.get("conviction", 0),
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                
                engine_b_raw = consensus.get("engine_b_raw", {})
                engine_b_raw["recommended_stop_loss"] = consensus.get("sl")
                engine_b_raw["recommended_take_profit"] = consensus.get("tp")
                
                resp = req.post(
                    "http://127.0.0.1:5000/api/quick-execute",
                    json={
                        "signal": signal,
                        "engine_b": engine_b_raw,
                        "pip_mode": pip_mode,
                        "sizing_override": consensus.get("sizing_override", 1.0),
                    },
                    timeout=30,
                ).json()
                
                if resp.get("success"):
                    ticket = resp.get("ticket", "?")
                    await query.message.reply_text(
                        f"✅ *EXECUTED*\n"
                        f"{consensus.get('display')} {consensus.get('direction')} "
                        f"@ {consensus.get('entry')}\n"
                        f"SL: {consensus.get('sl')} | TP: {consensus.get('tp')}\n"
                        f"Ticket: `#{ticket}`",
                        parse_mode="Markdown",
                    )
                else:
                    reason = resp.get("error") or resp.get("reason", "unknown")
                    await query.message.reply_text(f"❌ Execution failed: {reason}")
                    
            except Exception as e:
                await query.message.reply_text(f"❌ Execute error: {str(e)[:200]}")
            
            if signal_key in _pending_signals:
                del _pending_signals[signal_key]
            return
    
    # ── Helper: Send consensus card ──
    
    async def _send_consensus_card(
        chat_id: str, consensus: dict, context, category: str = "aligned"
    ):
        display = consensus.get("display", "?")
        direction = consensus.get("direction", "?")
        tier = consensus.get("tier", "SKIP")
        conviction = consensus.get("conviction", 0)
        
        dir_emoji = "🟢" if direction == "LONG" else "🔴" if direction == "SHORT" else "⚪"
        
        a = consensus.get("engine_a_raw", {})
        b = consensus.get("engine_b_raw", {})
        
        conv_bar = ""
        conv_pct = int(conviction * 100)
        for i in range(10):
            conv_bar += "█" if i < round(conv_pct / 10) else "░"
        
        text = f"⚖️ *ENGINE C — {display}*\n"
        text += f"{dir_emoji} {direction}  `{conv_bar}` *{tier}*\n\n"
        
        # Engine A
        if a.get("direction"):
            text += f"*Engine A:* {a.get('direction')} {a.get('score', '?')}/{a.get('maxScore', '?')}"
            text += f" `{a.get('regime', {}).get('label', '') if isinstance(a.get('regime'), dict) else a.get('regime', '')}`\n"
            text += f"  SL `{a.get('sl', '?')}` | TP `{a.get('tp1', '?')}`\n"
        else:
            text += "*Engine A:* No signal\n"
        
        # Engine B
        if b.get("direction"):
            text += f"*Engine B:* {b.get('direction')} {b.get('score', '?')}/{b.get('max_possible', '?')}"
            seq = b.get("sequence", "")
            bos = " BOS✓" if b.get("bos") else ""
            ob = " OB✓" if b.get("ob_at_zone") else ""
            text += f" `{seq}{bos}{ob}`\n"
            text += f"  SL `{b.get('sl', '?')}` | TP `{b.get('tp', '?')}`\n"
        else:
            text += "*Engine B:* No signal\n"
        
        # Consensus levels
        if consensus.get("sl") and consensus.get("tp"):
            text += f"\n*Consensus:* SL `{consensus['sl']}` | TP `{consensus['tp']}`\n"
            text += f"RR `1:{consensus.get('rr', '?')}` | "
            sizing = consensus.get("sizing_override", 0)
            size_label = "FULL" if sizing >= 1.0 else "HALF" if sizing >= 0.65 else "QTR" if sizing >= 0.35 else "NONE"
            text += f"Size: *{size_label}*"
        
        # Create signal key for callback
        signal_key = f"{display}_{direction}_{int(datetime.now().timestamp())}"
        _pending_signals[signal_key] = consensus
        
        # Inline buttons
        if category == "aligned" and consensus.get("trade"):
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🧠 AI Confirm", callback_data=f"ai_confirm:{signal_key}"),
                    InlineKeyboardButton("❌ Skip", callback_data=f"skip:{signal_key}"),
                ],
            ])
        else:
            keyboard = None
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    
    # Register handlers
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("kill", cmd_kill))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    log.warning("[TELEGRAM] Bot handlers registered, starting polling...")
    app.run_polling(drop_pending_updates=True)


# ── Proactive notification function (called from athena.py) ──

def send_engine_c_alert(consensus: dict):
    """Send an Engine C consensus alert proactively.
    Called from auto-trade loop when Engine C finds an aligned signal."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    
    if not token or not chat_id:
        return
    
    display = consensus.get("display", "?")
    direction = consensus.get("direction", "?")
    tier = consensus.get("tier", "SKIP")
    conviction = consensus.get("conviction", 0)
    
    dir_emoji = "🟢" if direction == "LONG" else "🔴"
    
    conv_bar = ""
    for i in range(10):
        conv_bar += "█" if i < round(conviction * 10) else "░"
    
    text = (
        f"⚖️ *ENGINE C — {display}*\n"
        f"{dir_emoji} {direction}  `{conv_bar}` *{tier}*\n"
        f"SL `{consensus.get('sl', '?')}` | TP `{consensus.get('tp', '?')}` | "
        f"RR `1:{consensus.get('rr', '?')}`\n"
        f"Conviction: `{conviction:.0%}`"
    )
    
    import requests as req
    try:
        req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:
        pass
