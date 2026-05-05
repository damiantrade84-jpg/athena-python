#!/usr/bin/env python3
"""Detailed Telegram diagnostic test."""

import os

import requests

from telegram_notify import (
    get_configured_chat_ids,
    get_delivery_state,
    get_runtime_config,
    send_test_message,
)


def diagnose_telegram() -> bool:
    print("=== Telegram Diagnostic ===")
    runtime_config = get_runtime_config()
    bot_token = runtime_config.get("token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_ids = get_configured_chat_ids(runtime_config)

    print("\n1. Runtime Config:")
    print(f"   TELEGRAM_BOT_TOKEN: {'SET' if bot_token else 'NOT SET'}")
    print(f"   TELEGRAM_CHAT_ID: {'SET' if chat_ids else 'NOT SET'}")
    print(f"   enabled: {runtime_config.get('enabled', False)}")
    print(f"   timeout_sec: {runtime_config.get('timeout_sec')}")
    print(f"   retry_attempts: {runtime_config.get('retry_attempts')}")
    print(f"   queue_max_size: {runtime_config.get('queue_max_size')}")

    if bot_token == "your_bot_token_here":
        print("   WARNING: Bot token is still placeholder.")
        return False
    if not bot_token or not chat_ids:
        print("   Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.")
        return False

    print("\n2. API Test:")
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getMe",
            timeout=runtime_config.get("timeout_sec", 10),
        )
        if response.status_code != 200:
            print(f"   Bot API error: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
        bot_info = response.json()
        result = bot_info.get("result", {})
        print(f"   Bot connected: @{result.get('username', '?')}")
        print(f"   Bot name: {result.get('first_name', '?')}")
    except Exception as exc:
        print(f"   API test failed: {exc}")
        return False

    print("\n3. Message Test:")
    try:
        delivered = send_test_message("Athena diagnostic test - shared delivery path")
        if delivered:
            print("   Message sent successfully.")
            print(f"   Delivery state: {get_delivery_state()}")
            return True
        print(f"   Send error: {get_delivery_state().get('last_error', 'unknown')}")
        return False
    except Exception as exc:
        print(f"   Send failed: {exc}")
        return False


if __name__ == "__main__":
    success = diagnose_telegram()
    if success:
        print("\nTelegram is working. Check your phone.")
    else:
        print("\nTelegram setup has issues. See above.")
