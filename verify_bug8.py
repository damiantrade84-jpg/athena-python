import pandas as pd
from datetime import datetime, timezone

# Simulation of BUG 8 alignment logic

# Scenario: Decision Day is 2024-01-02 00:00:00 (Start of day)
entry_ts = pd.to_datetime("2024-01-02 00:00:00", utc=True)

# D1 bars: [2024-01-01 00:00:00] (Yesterday's closed bar)
# H4 bars: [2024-01-01 20:00:00] (Last bar starting before today)
# Today's H4: [2024-01-02 00:00:00], [2024-01-02 04:00:00]...

# Previous (BUGGY) Cutoff:
old_cutoff = entry_ts + pd.Timedelta(days=1)
# Today's bar starting AT entry_ts (00:00) is 00:00 < cutoff (2024-01-03 00:00) -> TRUE (LEAKED)

# New (FIXED) Cutoff:
new_cutoff = entry_ts
# Today's bar starting AT entry_ts (00:00) is 00:00 < cutoff (00:00) -> FALSE (EXCLUDED)
# Yesterday's last H4 (20:00) is 20:00 < cutoff (00:00) -> TRUE (INCLUDED)

print(f"Entry Time (today): {entry_ts}")
print(f"New Cutoff: {new_cutoff}")

test_ts = pd.to_datetime("2024-01-02 00:00:00", utc=True)
if not (test_ts < new_cutoff):
    print(f"SUCCESS: Bar starting at {test_ts} (today) is EXCLUDED.")
else:
    print(f"FAILURE: Bar starting at {test_ts} (today) is INCLUDED.")

test_prev = pd.to_datetime("2024-01-01 20:00:00", utc=True)
if test_prev < new_cutoff:
    print(f"SUCCESS: Bar starting at {test_prev} (yesterday) is INCLUDED.")
else:
    print(f"FAILURE: Bar starting at {test_prev} (yesterday) is EXCLUDED.")
