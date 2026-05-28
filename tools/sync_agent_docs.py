"""Validate Athena agent/skill startup files.

The repo now keeps root startup files manually maintained and tool-specific:

- AGENTS.md is for Codex/Cursor.
- CLAUDE.md is for Claude Code.

This script intentionally does not regenerate those files from large guides because
that previously reintroduced stale cross-tool context and referenced a missing
`docs/claude-code-guide.md` file.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CODEX_SKILLS = [
    "athena-audit",
    "athena-anti-miss-review",
    "athena-engine-parity",
    "athena-research-lab",
    "athena-ui-chart-review",
    "athena-test-repair",
    "athena-risk-execution",
]

DISALLOWED_ROOT_INSTRUCTION_FILES = [
    ROOT / "memory.md",
    ROOT / "memories.md",
    ROOT / "MEMORY.md",
    ROOT / "AGENT.md",
]

REQUIRED_FILES = [
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",
    ROOT / "docs" / "codex-guidance.md",
    ROOT / "docs" / "codex-memory-policy.md",
    ROOT / "docs" / "codex-code-review-discipline.md",
    ROOT / "static" / "react-app" / "AGENTS.md",
    ROOT / "athena_research" / "AGENTS.md",
    ROOT / "tests" / "AGENTS.md",
    ROOT / ".claude" / "skills" / "athena-audit" / "SKILL.md",
    ROOT / ".agents" / "skills" / "athena-audit" / "agents" / "openai.yaml",
]
REQUIRED_FILES.extend(
    ROOT / ".agents" / "skills" / name / "SKILL.md" for name in CODEX_SKILLS
)

FORBIDDEN_REFERENCES = [
    ".Codex/skills",
    ".codex/skills",
    "athena-code/SKILL.md",
    "backtest-analysis/SKILL.md",
    "engine-entry-design/SKILL.md",
    "docs/claude-code-guide.md",
    "memory.md as repo instructions",
    "memories.md as repo instructions",
    "MEMORY.md as repo instructions",
    "startup: memory.md",
    "startup: memories.md",
    "load memory.md",
    "load memories.md",
]

CHECK_FILES = [
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",
    ROOT / "docs" / "agent-operating-guide.md",
    ROOT / "docs" / "codex-guidance.md",
    ROOT / ".codex" / "agents" / "execution-safety-reviewer.toml",
    ROOT / ".claude" / "README.md",
]
CHECK_FILES.extend(
    ROOT / ".agents" / "skills" / name / "SKILL.md" for name in CODEX_SKILLS
)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
MAX_AGENTS_KIB = 12


def _parse_frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def main() -> None:
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED_FILES if not path.exists()]
    if missing:
        raise SystemExit("Missing required agent/skill files:\n- " + "\n- ".join(missing))

    violations: list[str] = []
    for path in DISALLOWED_ROOT_INSTRUCTION_FILES:
        if path.exists():
            violations.append(
                f"{path.relative_to(ROOT)} must not exist at repo root; "
                "archive to docs/archive/ and use AGENTS.md (see docs/codex-memory-policy.md)"
            )

    for path in CHECK_FILES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN_REFERENCES:
            if needle in text:
                violations.append(f"{path.relative_to(ROOT)} contains stale reference: {needle}")

    agents_size = (ROOT / "AGENTS.md").stat().st_size
    if agents_size > MAX_AGENTS_KIB * 1024:
        violations.append(
            f"AGENTS.md is {agents_size} bytes; keep under {MAX_AGENTS_KIB} KiB and move detail to skills"
        )

    for skill_name in CODEX_SKILLS:
        skill_path = ROOT / ".agents" / "skills" / skill_name / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        if fm.get("name") != skill_name:
            violations.append(f"{skill_path.relative_to(ROOT)} frontmatter name must be {skill_name!r}")
        if not fm.get("description"):
            violations.append(f"{skill_path.relative_to(ROOT)} missing description in frontmatter")
        extra_keys = set(fm) - {"name", "description"}
        if extra_keys:
            violations.append(
                f"{skill_path.relative_to(ROOT)} frontmatter must only contain name and description; found {sorted(extra_keys)}"
            )

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
