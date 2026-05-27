#!/usr/bin/env python3
"""
test_telegram.py - Test Telegram notification system

Run this script to verify Telegram notifications are working properly.
Tests all notification types: signal fired, trade opened/closed, system alerts.
"""

import time
import telegram_notify


def test_signal_notification():
    """Test signal fired notification"""
    print("Testing signal notification...")
    telegram_notify.notify_signal_fired(
        pair="BTC/USDT",
        direction="LONG",
        score=1.25,
        dir_score=0.85,
        nondir_score=0.40,
        top_factors=["trend", "momentum", "volume"],
        regime="TRENDING",
    )
    print("✅ Signal notification sent")


def test_trade_notifications():
    """Test trade opened and closed notifications"""
    print("Testing trade opened notification...")
    telegram_notify.notify_trade_opened(
        pair="ETH/USDT",
        direction="SHORT",
        entry_price=3456.78,
        stop_loss=3520.00,
        take_profit=3380.00,
    )
    print("✅ Trade opened notification sent")

    time.sleep(2)  # Small delay between notifications

    print("Testing trade closed notification...")
    telegram_notify.notify_trade_closed(
        pair="ETH/USDT", pnl_r=1.25, is_win=True, duration_minutes=145.5
    )
    print("✅ Trade closed notification sent")


def test_system_alerts():
    """Test system alert notifications"""
    print("Testing Bybit WS disconnect notification...")
    telegram_notify.notify_bybit_ws_disconnect()
    print("✅ Bybit WS disconnect notification sent")

    time.sleep(2)

    print("Testing Polygon rate limit notification...")
    telegram_notify.notify_polygon_rate_limit()
    print("✅ Polygon rate limit notification sent")


def test_daily_summary():
    """Test daily summary notification"""
    print("Testing daily summary notification...")
    # Update some test stats
    telegram_notify.update_open_positions(
        [
            {"pair": "BTC/USDT", "direction": "LONG", "size": 0.1},
            {"pair": "XAU/USD", "direction": "SHORT", "size": 0.05},
        ]
    )

    # Force daily summary (normally only runs at 22:00 UTC)
    telegram_notify._daily_stats["signals_fired"] = [
        {"pair": "BTC/USDT", "direction": "LONG", "score": 1.25}
    ]
    telegram_notify._daily_stats["trades_opened"] = [
        {"pair": "ETH/USDT", "direction": "SHORT", "entry_price": 3456.78}
    ]
    telegram_notify._daily_stats["trades_closed"] = [
        {"pair": "ETH/USDT", "pnl_r": 1.25, "is_win": True}
    ]

    telegram_notify.notify_daily_summary()
    print("✅ Daily summary notification sent")


def main():
    """Run all Telegram notification tests"""
    print("🚀 Starting Telegram notification tests...")
    print(f"Enabled: {telegram_notify._is_enabled()}")

    if not telegram_notify._is_enabled():
        print("❌ Telegram notifications are disabled in config.yaml")
        print("Set TELEGRAM.enabled: true to enable notifications")
        return

    try:
        test_signal_notification()
        time.sleep(3)

        test_trade_notifications()
        time.sleep(3)

        test_system_alerts()
        time.sleep(3)

        test_daily_summary()

        print("\n🎉 All Telegram notification tests completed!")
        print("Check your Telegram chat for the test messages.")

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
