"""Validate Athena agent/skill startup files.

The repo now keeps root startup files manually maintained and tool-specific:

- AGENTS.md is for Codex/Cursor.
- CLAUDE.md is for Claude Code.

This script intentionally does not regenerate those files from large guides because
that previously reintroduced stale cross-tool context and referenced a missing
`docs/claude-code-guide.md` file.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",
    ROOT / ".agents" / "skills" / "athena-audit" / "SKILL.md",
    ROOT / ".agents" / "skills" / "athena-audit" / "agents" / "openai.yaml",
    ROOT / ".claude" / "skills" / "athena-audit" / "SKILL.md",
]

FORBIDDEN_REFERENCES = [
    ".Codex/skills",
    ".codex/skills",
    "athena-code/SKILL.md",
    "backtest-analysis/SKILL.md",
    "engine-entry-design/SKILL.md",
    "docs/claude-code-guide.md",
]

CHECK_FILES = [
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",
    ROOT / "docs" / "agent-operating-guide.md",
    ROOT / ".codex" / "agents" / "execution-safety-reviewer.toml",
    ROOT / ".claude" / "README.md",
]


def main() -> None:
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED_FILES if not path.exists()]
    if missing:
        raise SystemExit("Missing required agent/skill files:\n- " + "\n- ".join(missing))

    violations: list[str] = []
    for path in CHECK_FILES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN_REFERENCES:
            if needle in text:
                violations.append(f"{path.relative_to(ROOT)} contains stale reference: {needle}")

    codex_policy = ROOT / ".agents" / "skills" / "athena-audit" / "agents" / "openai.yaml"
    if "allow_implicit_invocation: false" not in codex_policy.read_text(encoding="utf-8"):
        violations.append("Codex skill policy must contain allow_implicit_invocation: false")

    claude_skill = ROOT / ".claude" / "skills" / "athena-audit" / "SKILL.md"
    if "disable-model-invocation: true" not in claude_skill.read_text(encoding="utf-8"):
        violations.append("Claude athena-audit skill must contain disable-model-invocation: true")

    root_claude_skill = ROOT / ".claude" / "SKILL.md"
    if root_claude_skill.exists():
        violations.append("Remove .claude/SKILL.md; Claude project skill belongs under .claude/skills/athena-audit/SKILL.md")

    if violations:
        raise SystemExit("Agent/skill validation failed:\n- " + "\n- ".join(violations))

    print("Agent/skill startup files validated.")


if __name__ == "__main__":
    main()
