# Athena Prompt Store

External, versioned prompt templates for Athena's advisory AI surfaces.

## Status

Phase 0 of the Athena AI Edge Program. Default-off via `AI_PROMPT_STORE_ENABLED: false`.
When disabled, every surface falls back to its hardcoded prompt — zero behavior change.
When enabled, `prompt_store.py` reads `<surface>.md` from this directory with
hot-reload (mtime-based) and sha256 audit. Versioned filenames (for example
`marcus_v6.md`) are resolved via `_SURFACE_FILE_ALIASES` when the surface key
does not match the on-disk stem.

Python fallbacks remain the default runtime source while
`AI_PROMPT_STORE_ENABLED` is false. Keep markdown bodies and Python fallbacks
in sync; focused tests assert they cannot silently drift.

## Safety contract

- Prompts are **advisory only**. They cannot execute trades, approve orders,
  mutate config, or override deterministic gates.
- Missing/unreadable files fall back to the hardcoded prompt — never raises.
- The on-disk file is the source of truth when enabled. Edits here take effect
  on the next call (hot-reload); no restart required.
- Every rendered prompt is hashed and stamped into the existing
  `ai_review_audit.jsonl` trail via the `prompt_source` / `prompt_text_hash`
  fields on `log_ai_review`.

## File format

Each `.md` file may begin with optional YAML frontmatter:

```
---
surface: <surface_key>
version: <version_label>
role: <optional role tag>
---
<prompt body>
```

Frontmatter is stripped by `prompt_store.load_prompt()` before the text is
returned to the caller. Files without frontmatter are returned verbatim.

## Surface keys

| Surface key | File | Wired in |
|-------------|------|----------|
| `engine_c_ai_system` | `engine_c_v2.md` | `engine_c_ai.py` |
| `marcus_chat_system` | `chat_system_v3.md` | `ai_trade_chat.py` |
| `news_sentiment_system` | `news_v2.md` | `news_sentiment_feed.py` |
| `meta_analysis_system` | `meta_v2.md` | `ai_learning.py` |
| `debate_bull_bear_system` | `debate_bull_v2.md`, `debate_bear_v2.md` | `signal_debate.py` |
| `debate_judge_system` | `debate_judge_v2.md` | `signal_debate.py` |
| `lee_system` | `lee_v2.md` | `ai_lee_confirmation.py` |
| `marcus_expert` | `marcus_v6.md` | `athena.py` |
| `vision_system` | `vision_v3.md` | `vision_prompts.py` |
| `chart_review_engine_a_preamble` | `chart_review_a_v3.md` | `ai_review/prompt_builder.py` |
| `chart_review_engine_b_preamble` | `chart_review_b_v3.md` | `ai_review/prompt_builder.py` |
| `scalp_review_engine_d_preamble` | `scalp_review_d_v3.md` | `ai_scalp_review/prompt_builder.py` |
| `engine_b_ai_expert_prefix` | `engine_b_v2.md` | `engine_b_ai.py` |
| `research_safety_preamble` | `research_analyst_v2.md` | `athena_research/prompt_builder.py` |

## Adding a new surface

1. Create `<surface>.md` in this directory with the prompt text.
2. In the Python module, replace the hardcoded constant with:

```python
from prompt_store import load_prompt

_PROMPT_FALLBACK = """..."""  # the original hardcoded text
_PROMPT, _PROMPT_SOURCE, _PROMPT_HASH = load_prompt(
    "<surface>",
    fallback=_PROMPT_FALLBACK,
)
```

3. Use `_PROMPT` in place of the original constant. When the feature is off,
   `_PROMPT` equals `_PROMPT_FALLBACK` (zero behavior change).
4. (Optional) Pass `_PROMPT_SOURCE` and `_PROMPT_HASH` into `log_ai_review`
   via the `prompt_source` / `prompt_text_hash` fields for audit traceability.

## Editing a prompt

1. Edit the `.md` file in this directory.
2. The next AI call picks up the new text (hot-reload).
3. Bump the `version:` field in the frontmatter so the audit trail distinguishes
   revisions.
4. Run the focused test for the affected surface to confirm parsing still works.
