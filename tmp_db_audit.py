import sqlite3
conn = sqlite3.connect('audit.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print('Tables:', tables)

for tbl in tables:
    cur.execute(f"PRAGMA table_info({tbl})")
    cols = [r[1] for r in cur.fetchall()]
    cur.execute(f"SELECT COUNT(*) FROM {tbl}")
    n = cur.fetchone()[0]
    print(f'{tbl}: {cols[:12]}... rows={n}')

# Check audit_log for engine-like columns
if 'audit_log' in tables:
    cur.execute("PRAGMA table_info(audit_log)")
    cols = [r[1] for r in cur.fetchall()]
    print('\naudit_log all columns:', cols)
    # Check engine field distribution
    if 'engine' in cols:
        cur.execute("SELECT COALESCE(NULLIF(TRIM(engine), ''), 'NULL_OR_BLANK') AS engine_label, COUNT(*) FROM audit_log GROUP BY COALESCE(NULLIF(TRIM(engine), ''), 'NULL_OR_BLANK') ORDER BY COUNT(*) DESC")
        print('\nEngine labels in audit_log:')
        for row in cur.fetchall():
            print(' ', row)
    # Check signal_source
    if 'signal_source' in cols:
        cur.execute("SELECT COALESCE(NULLIF(TRIM(signal_source), ''), 'NULL_OR_BLANK') AS signal_source, COUNT(*) FROM audit_log GROUP BY COALESCE(NULLIF(TRIM(signal_source), ''), 'NULL_OR_BLANK') ORDER BY COUNT(*) DESC")
        print('\nSignal source in audit_log:')
        for row in cur.fetchall():
            print(' ', row)

conn.close()
