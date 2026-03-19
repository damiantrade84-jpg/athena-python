with open("config.yaml", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Lines are 0-indexed.
# Line 352 is index 351.
# Line 548 is index 547.
# We want to keep lines 0 to 351 (inclusive), then 547 to end.
new_lines = lines[:352] + lines[547:]

with open("config.yaml", "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print("config.yaml fixed. Removed lines 353-547.")
