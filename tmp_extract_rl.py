#!/usr/bin/env python3
"""
Tier 3 extractor: pull the Research Lab inline JS out of static/index.html
into static/js/features/research_lab.js. Preserves Unix line endings.

Idempotent: refuses to run a second time (the anchor block is already gone).
"""
from pathlib import Path
import sys

INDEX = Path("static/index.html")
OUT_JS = Path("static/js/features/research_lab.js")

START_ANCHOR_SUBSTR = "RESEARCH LAB JS"   # appears inside the marker comment
SCRIPT_OPEN  = "<script>"
SCRIPT_CLOSE = "</script>"
IIFE_OPEN    = "(function() {"
IIFE_CLOSE   = "})();"

def die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

def main():
    if not INDEX.exists():
        die(f"{INDEX} not found — wrong working directory?")

    # Read with explicit utf-8 + preserve newlines (Windows-safe)
    raw = INDEX.read_bytes().decode("utf-8")
    lines = raw.splitlines(keepends=True)

    # 1. Locate the marker comment
    anchor_idx = None
    for i, ln in enumerate(lines):
        if START_ANCHOR_SUBSTR in ln and "<!--" in ln:
            anchor_idx = i
            break
    if anchor_idx is None:
        die(f"Could not find '{START_ANCHOR_SUBSTR}' marker comment. "
            f"Either already extracted, or anchor changed.")

    # 2. Find <script> within 4 lines after marker (must be on its own line)
    script_open_idx = None
    for j in range(anchor_idx + 1, min(anchor_idx + 5, len(lines))):
        if lines[j].strip() == SCRIPT_OPEN:
            script_open_idx = j
            break
    if script_open_idx is None:
        die(f"No bare <script> tag within 4 lines of marker at line {anchor_idx + 1}.")

    # 3. Find next </script> on its own line
    script_close_idx = None
    for k in range(script_open_idx + 1, len(lines)):
        if lines[k].strip() == SCRIPT_CLOSE:
            script_close_idx = k
            break
    if script_close_idx is None:
        die("No closing </script> found after RL <script>.")

    # 4. Extract IIFE body and validate
    iife_body = lines[script_open_idx + 1 : script_close_idx]

    iife_open_seen = any(ln.strip().startswith(IIFE_OPEN) for ln in iife_body[:5])
    iife_close_seen = any(ln.strip() == IIFE_CLOSE for ln in iife_body[-5:])
    use_strict_seen = any("'use strict'" in ln or '"use strict"' in ln for ln in iife_body[:5])

    if not iife_open_seen:
        die(f"IIFE open '{IIFE_OPEN}' not found near start of body. "
            f"First 5 lines: {[ln.strip() for ln in iife_body[:5]]}")
    if not iife_close_seen:
        die(f"IIFE close '{IIFE_CLOSE}' not found near end of body. "
            f"Last 5 lines: {[ln.strip() for ln in iife_body[-5:]]}")
    if not use_strict_seen:
        print("WARN: 'use strict' not found near IIFE start — proceeding.", file=sys.stderr)

    # 5. Sanity: count window.rl* function definitions in the body
    rl_count = sum(1 for ln in iife_body if "window.rl" in ln and "= async function" in ln) \
             + sum(1 for ln in iife_body if "window.rl" in ln and "= function" in ln)
    if rl_count < 15:
        die(f"Found only {rl_count} window.rl* function definitions in body; "
            f"expected at least 15. Aborting to avoid partial extract.")

    # 6. Write the new module
    OUT_JS.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "// Research Lab — extracted from static/index.html (Tier 3 cleanup pass).\n"
        "// Wires up the Research Lab panel: One-Click Autopilot Cockpit,\n"
        "// Quick Discovery, Manual Override, results table, AI Review, AI Plan.\n"
        "// Backend endpoints: /api/research-lab/*\n"
        "//\n"
        "// This is a pure refactor — no logic changes. If this file misbehaves,\n"
        "// diff against the pre-extraction inline block in git history.\n"
        "\n"
    )
    js_content = header + "".join(iife_body)
    OUT_JS.write_bytes(js_content.encode("utf-8"))

    # 7. Rewrite index.html: keep marker, replace <script>…</script> with
    #    <script src="…"></script>
    replacement = '<script src="/static/js/features/research_lab.js"></script>\n'

    new_lines = (
        lines[: script_open_idx]
        + [replacement]
        + lines[script_close_idx + 1 :]
    )
    INDEX.write_bytes("".join(new_lines).encode("utf-8"))

    # 8. Stats
    removed = (script_close_idx - script_open_idx + 1)
    print(f"Marker line:         {anchor_idx + 1}")
    print(f"<script> open line:  {script_open_idx + 1}")
    print(f"</script> close line:{script_close_idx + 1}")
    print(f"IIFE body lines:     {len(iife_body)}")
    print(f"window.rl* defs:     {rl_count}")
    print(f"Wrote {OUT_JS} ({OUT_JS.stat().st_size} bytes)")
    print(f"index.html: removed {removed} lines, added 1 line — net -{removed - 1}")

if __name__ == "__main__":
    main()
