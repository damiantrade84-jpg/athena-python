with open('telegram_bot.py', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace('            except Exception:\n                await query.message.reply_text(\'⚠️ Athena offline — check server\')\n            return\n\n        # ── Decay', '            except Exception as e:\n                await query.message.reply_text(f\'⚠️ Athena offline or timed out — check server\\n\{str(e)[:150]}\\', parse_mode=\'Markdown\')\n            return\n\n        # ── Decay')
with open('telegram_bot.py', 'w', encoding='utf-8') as f:
    f.write(c)
