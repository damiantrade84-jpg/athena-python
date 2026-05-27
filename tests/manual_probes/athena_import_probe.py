import sys
import os

root_path = r"c:\Users\damia\OneDrive\Desktop\athena-python"
if root_path not in sys.path:
    sys.path.insert(0, root_path)

print("Attempting to import athena...")
try:
    import athena
    print(f"athena imported successfully from {athena.__file__}.")
except Exception as e:
    print(f"Failed to import athena: {e}")
    import traceback
    traceback.print_exc()

import athena_runtime
print(f"athena_runtime imported from {athena_runtime.__file__}")

print("Checking rt()...")
try:
    r = athena_runtime.rt()
    print(f"rt() initialized! ALL_PAIRS count: {len(r.ALL_PAIRS)}")
except Exception as e:
    print(f"Failed to get rt(): {e}")
