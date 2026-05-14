"""Regenerate root agent docs.

- AGENTS.md: header + full body from docs/agent-operating-guide.md (Cursor / Codex).
- CLAUDE.md: header + short body from docs/claude-code-guide.md (Claude Code; AGENTS.md is .claudeignore'd).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "agent-operating-guide.md"
CLAUDE_BODY = ROOT / "docs" / "claude-code-guide.md"

AGENTS_HEADER = """# AGENTS.md — Sentinel Pro v4 (Cursor / Codex)

**Audience:** Cursor agents and Codex. **Canonical:** [`docs/agent-operating-guide.md`](docs/agent-operating-guide.md). This file duplicates the canonical guide for tooling that reads **AGENTS.md** at repo root.

---

"""

CLAUDE_HEADER = """# CLAUDE.md — Athena / Sentinel Pro v4

Claude Code quick guide. Full operating rules: [docs/agent-operating-guide.md](docs/agent-operating-guide.md). (**`AGENTS.md` is Cursor/Codex-only** and is omitted from Claude Code context via `.claudeignore`.)

---

"""

def main() -> None:
    body = GUIDE.read_text(encoding="utf-8")
    (ROOT / "AGENTS.md").write_text(AGENTS_HEADER + body, encoding="utf-8", newline="\n")

    claude_guide = CLAUDE_BODY.read_text(encoding="utf-8")
    (ROOT / "CLAUDE.md").write_text(CLAUDE_HEADER + claude_guide, encoding="utf-8", newline="\n")
    print(
        "Updated AGENTS.md from docs/agent-operating-guide.md "
        "and CLAUDE.md from docs/claude-code-guide.md"
    )


if __name__ == "__main__":
    main()
