"""Regenerate root AGENTS.md and CLAUDE.md from docs/agent-operating-guide.md."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "agent-operating-guide.md"

AGENTS_HEADER = """# AGENTS.md — Sentinel Pro v4 (Cursor / Codex)

**Audience:** Cursor agents and Codex. **Canonical:** [`docs/agent-operating-guide.md`](docs/agent-operating-guide.md). This file duplicates the canonical guide for tooling that reads **AGENTS.md** at repo root.

---

"""

CLAUDE_HEADER = """# CLAUDE.md — Athena / Sentinel Pro v4

**Audience:** Claude Code (`claude.ai/code`). **Canonical:** [`docs/agent-operating-guide.md`](docs/agent-operating-guide.md) — same mirrored body as [`AGENTS.md`](AGENTS.md).

---

"""


def main() -> None:
    body = GUIDE.read_text(encoding="utf-8")
    (ROOT / "AGENTS.md").write_text(AGENTS_HEADER + body, encoding="utf-8", newline="\n")
    (ROOT / "CLAUDE.md").write_text(CLAUDE_HEADER + body, encoding="utf-8", newline="\n")
    print("Updated AGENTS.md and CLAUDE.md from docs/agent-operating-guide.md")


if __name__ == "__main__":
    main()
