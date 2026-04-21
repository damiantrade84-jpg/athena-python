"""Shared AI parsing and normalization utilities."""

from __future__ import annotations

import json
import re


def parse_json_object(text: str) -> dict | None:
    """Parse a JSON object from LLM output with robust fallbacks."""
    if not text:
        return None

    # 1. Direct parse (works when response_format=json_object is used)
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown code fences
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                try:
                    return json.loads(part)
                except json.JSONDecodeError:
                    pass

    # 3. Extract first {...} block (handles any nesting depth)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            result = json.loads(text[start:end])
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    return None

