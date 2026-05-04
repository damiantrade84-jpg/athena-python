# Lessons

- When auditing ATHENA AI usage, include both AI prompt/output contracts and the construction/routing layer. A shared client constructor can be simple plumbing; the higher-value fix is structured AI output plus deterministic Python gates.
- When a dashboard tab shows blank fallback states, verify the live API response shape before changing backend logic; Guardian routes return object-shaped `checks`, feed `pairs/timeframes`, divergence `recent_events`, and forensic `views`.
- When reviewing Engine B target math, verify target-side formulas and fallback behavior together: structural TP diagnostics can look safe because fallback RR prevents bad trades, while the actual bug is suppressed structural target selection.
- Never rewrite generated UTF-8 frontend bundles with PowerShell default text decoding; use a UTF-8-safe tool such as the frontend build or Node file APIs, then verify mojibake strings like `Â·` and `â€”` are absent.
