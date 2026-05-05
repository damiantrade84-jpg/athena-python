#!/usr/bin/env python3
"""Quick Telegram test to verify the shared delivery path."""

import os

from telegram_notify import (
    _is_enabled,
    get_configured_chat_ids,
    get_delivery_state,
    get_runtime_config,
    send_test_message,
)


def test_telegram() -> bool:
    config = get_runtime_config()
    bot_token = config.get("token") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_ids = get_configured_chat_ids(config)

    if not bot_token or not chat_ids:
        print("Telegram environment variables are not set")
        return False

    if bot_token == "your_bot_token_here":
        print("Please update TELEGRAM_BOT_TOKEN with an actual token")
        return False

    if not _is_enabled():
        print("Telegram notifications are not enabled in config")
        return False

    try:
        message = f"Athena test message - shared delivery path\nTime: {os.times()}"
        sent = send_test_message(message)
        state = get_delivery_state()
        if not sent:
            print(f"Telegram test delivery failed: {state.get('last_error', 'unknown error')}")
            return False
        print("Telegram test message sent.")
        print("You should receive a message on Telegram shortly.")
        return True
    except Exception as exc:
        print(f"Telegram test error: {exc}")
        return False


if __name__ == "__main__":
    test_telegram()
