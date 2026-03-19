#!/usr/bin/env python3
"""Quick Telegram test to verify connection"""

import os
from telegram_notify import _send_message_async, _is_enabled


def test_telegram():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("❌ Telegram environment variables not set")
        return False

    if bot_token == "your_bot_token_here":
        print("❌ Please update TELEGRAM_BOT_TOKEN with actual token")
        return False

    if not _is_enabled():
        print("❌ Telegram notifications not enabled in config")
        return False

    try:
        message = (
            "🤖 Athena Test Message\n\n✅ Telegram notifications are working!\n\nTime: "
            + str(os.times())
        )
        _send_message_async(message)

        print("✅ Telegram test message sent!")
        print("📱 You should receive a message on Telegram shortly")
        return True

    except Exception as e:
        print(f"❌ Telegram test error: {e}")
        return False


if __name__ == "__main__":
    test_telegram()
