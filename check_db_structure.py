#!/usr/bin/env python3
"""Quick test of COT and Carry database structure"""

import sqlite3

def check_db_structure(db_file, name):
    print(f"\n=== {name} Database Structure ===")
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        print(f"Tables: {[t[0] for t in tables]}")
        
        if tables:
            table_name = tables[0][0]
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = cursor.fetchall()
            print(f"Columns: {[col[1] for col in columns]}")
            
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cursor.fetchone()[0]
            print(f"Records: {count}")
            
            if count > 0:
                cursor.execute(f"SELECT * FROM {table_name} ORDER BY rowid DESC LIMIT 3")
                recent = cursor.fetchall()
                print("Recent data:")
                for r in recent:
                    print("  ", r)
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error: {e}")
        return False

if __name__ == "__main__":
    check_db_structure("cot_cache.db", "COT")
    check_db_structure("carry_cache.db", "Carry")
