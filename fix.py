import pathlib
import re

p = pathlib.Path('telegram_bot.py')
c = p.read_text(encoding='utf-8')

# The exact exception block for the AI analysis
target = """            except Exception:
                await query.message.reply_text("⚠️ Athena offline — check server")
            return

        # ── Decay"""

replacement = """            except Exception as e:
                await query.message.reply_text(f"⚠️ Athena offline or timed out — check server\\n`{str(e)[:150]}`", parse_mode="Markdown")
            return

        # ── Decay"""

c = c.replace(target, replacement)
p.write_text(c, encoding='utf-8')
