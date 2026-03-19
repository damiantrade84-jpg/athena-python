import sqlite3

# Check both databases
for db_name in ["cot.db", "cot_cache.db"]:
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()

        # Check tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        print(f"Tables in {db_name}:")
        for table in tables:
            print(f"  {table[0]}")

        # Check if cot_net exists
        if any("cot_net" in t[0] for t in tables):
            cursor.execute(
                "SELECT DISTINCT report_date FROM cot_net WHERE asset='EUR' ORDER BY report_date DESC LIMIT 5"
            )
            dates = cursor.fetchall()
            print(f"\nRecent EUR COT dates in {db_name}:")
            for date in dates:
                print(f"  {date[0]}")
        else:
            print(f"\nNo cot_net table found in {db_name}")

        conn.close()
        print()
    except Exception as e:
        print(f"Error checking {db_name}: {e}\n")
