#!/usr/bin/env python3
"""Save live feed diagnostic snapshot to JSON file."""
import json
import subprocess
import sys
from pathlib import Path

def main():
    # Run the diagnostic command
    cmd = [
        sys.executable,
        "tools/live_feed_diagnostics.py",
        "--symbols", "EURUSD,GBPUSD,BTCUSDT,ETHUSDT",
        "--timeframes", "H1,H4,D1",
        "--json-output"
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent)
    
    if result.returncode != 0:
        print(f"Error running diagnostic: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    
    # Parse and save JSON
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}", file=sys.stderr)
        print(f"Output: {result.stdout[:1000]}", file=sys.stderr)
        sys.exit(1)
    
    # Save to file
    output_path = Path(__file__).parent.parent / "docs" / "diagnostics" / "latest_live_feed_snapshot.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    
    print(f"Saved diagnostic snapshot to {output_path}")
    print(f"Total rows: {len(data.get('rows', []))}")

if __name__ == "__main__":
    main()
