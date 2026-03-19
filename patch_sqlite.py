import os
import re

# We will recursively walk the desktop/athena-python dir
target_dir = r"c:\Users\damia\OneDrive\Desktop\athena-python"

# We want to match sqlite3.connect(...) but ONLY if it doesn't already have timeout
# This regex matches sqlite3.connect(something) and captures "something"
# We also capture if there's a "with " at the start.
connect_pattern = re.compile(r"(with\s+)?sqlite3\.connect\(([^,]+?)\)(\s+as\s+\w+:)?")

files_changed = 0

for root, dirs, files in os.walk(target_dir):
    for filename in files:
        if filename.endswith(".py") and filename != "patch_sqlite.py":
            filepath = os.path.join(root, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()

                # Check if there's any sqlite connection
                if "sqlite3.connect" in content:
                    lines = content.split("\n")
                    modified = False
                    for i, line in enumerate(lines):
                        if "sqlite3.connect" in line and "timeout=" not in line:
                            # Replace sqlite3.connect(X) with sqlite3.connect(X, timeout=1.0)
                            # Regex will replace: sqlite3.connect(_DB) -> sqlite3.connect(_DB, timeout=1.0)
                            def repl(m):
                                prefix = m.group(1) or ""
                                db_arg = m.group(2)
                                suffix = m.group(3) or ""
                                return f"{prefix}sqlite3.connect({db_arg}, timeout=1.0){suffix}"

                            new_line = connect_pattern.sub(repl, line)
                            if new_line != line:
                                lines[i] = new_line
                                modified = True

                    if modified:
                        with open(filepath, "w", encoding="utf-8") as f:
                            f.write("\n".join(lines))
                        files_changed += 1
                        print(f"Patched: {filepath}")
            except Exception as e:
                print(f"Error processing {filepath}: {e}")

print(f"Total files patched: {files_changed}")
