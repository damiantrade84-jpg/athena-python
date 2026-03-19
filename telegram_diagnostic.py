#!/usr/bin/env python3
"""Detailed Telegram diagnostic test"""

import os
import yaml
from pathlib import Path


def diagnose_telegram():
    print("=== Telegram Diagnostic ===")

    # 1. Check environment variables
    print("\n1. Environment Variables:")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    enabled = os.environ.get("TELEGRAM_ENABLED", "")

    print(f"   TELEGRAM_BOT_TOKEN: {'SET' if bot_token else 'NOT SET'}")
    print(f"   TELEGRAM_CHAT_ID: {'SET' if chat_id else 'NOT SET'}")
    print(f"   TELEGRAM_ENABLED: {enabled}")

    if bot_token == "your_bot_token_here":
        print("   ⚠️  Bot token is still placeholder!")
        return False

    # 2. Check config.yaml
    print("\n2. Config.yaml:")
    try:
        config_path = Path(__file__).parent / "config.yaml"
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
            telegram_config = config.get("TELEGRAM", {})
            print(f"   enabled: {telegram_config.get('enabled', False)}")
            print(f"   token: {'SET' if telegram_config.get('token') else 'EMPTY'}")
            print(f"   chat_id: {'SET' if telegram_config.get('chat_id') else 'EMPTY'}")
    except Exception as e:
        print(f"   Error reading config.yaml: {e}")
        return False

    # 3. Test actual API call
    print("\n3. API Test:")
    try:
        import requests

        url = f"https://api.telegram.org/bot{bot_token}/getMe"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            bot_info = response.json()
            print(f"   ✅ Bot connected: @{bot_info['result']['username']}")
            print(f"   ✅ Bot name: {bot_info['result']['first_name']}")
        else:
            print(f"   ❌ Bot API error: {response.status_code}")
            print(f"   Response: {response.text}")
            return False

    except Exception as e:
        print(f"   ❌ API test failed: {e}")
        return False

    # 4. Test sending message
    print("\n4. Message Test:")
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": "🤖 Athena Diagnostic Test\n\n✅ Direct API test successful!",
            "parse_mode": "Markdown",
        }

        response = requests.post(url, json=payload, timeout=10)

        if response.status_code == 200:
            print("   ✅ Message sent successfully!")
            return True
        else:
            print(f"   ❌ Send error: {response.status_code}")
            print(f"   Response: {response.text}")
            return False

    except Exception as e:
        print(f"   ❌ Send failed: {e}")
        return False


if __name__ == "__main__":
    success = diagnose_telegram()
    if success:
        print("\n🎉 Telegram is working! Check your phone.")
    else:
        print("\n❌ Telegram setup has issues. See above.")
