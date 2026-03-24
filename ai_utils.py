"""Shared AI parsing and normalization utilities."""

from __future__ import annotations

import json
import re


def parse_json_object(text: str) -> dict | None:
    """Parse a JSON object from LLM output with robust fallbacks."""
    parsed = None

    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                try:
                    parsed = json.loads(part[: part.rfind("}") + 1])
                    break
                except json.JSONDecodeError:
                    pass

    if parsed is None:
        match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                pass

    if parsed is None:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

    return parsed if isinstance(parsed, dict) else None

