# Lessons

- When Research Lab gives strategy feedback, strengthen the lab's own self-audit instead of manually second-guessing every result; reports must expose simulator backend, fallback reason, and signal/trade consistency so confidence is evidence-based.
- When auditing ATHENA AI usage, include both AI prompt/output contracts and the construction/routing layer. A shared client constructor can be simple plumbing; the higher-value fix is structured AI output plus deterministic Python gates.
- When adding ATHENA runtime diagnostics, verify the logger level actually emits them. `log.info(...)` on the `sentinel` logger is suppressed by `log.setLevel(logging.WARNING)`, so failure-critical AI timing/config must be logged at warning/error level or tested as visible.
- When a dashboard tab shows blank fallback states, verify the live API response shape before changing backend logic; Guardian routes return object-shaped `checks`, feed `pairs/timeframes`, divergence `recent_events`, and forensic `views`.
- When reviewing Engine B target math, verify target-side formulas and fallback behavior together: structural TP diagnostics can look safe because fallback RR prevents bad trades, while the actual bug is suppressed structural target selection.
- Never rewrite generated UTF-8 frontend bundles with PowerShell default text decoding; use a UTF-8-safe tool such as the frontend build or Node file APIs, then verify mojibake strings like `Â·` and `â€”` are absent.
