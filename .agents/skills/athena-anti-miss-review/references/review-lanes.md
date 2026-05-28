# Review lanes (anti-miss)

Use when spawning parallel subagents for multi-surface audits. Each lane is independent; **consolidate only after all lanes return**.

## Lane contract

Each subagent must return:

1. **Coverage** — entry points, files read, tests read, status (COVERED / PARTIAL / NOT REVIEWED / BLOCKED)
2. **Findings** — severity, file/anchor, execution path, why it matters, minimal fix, regression test
3. **Not reviewed** — explicit list of paths/files/tests skipped and why

Do not merge lanes into a final verdict until every spawned lane has returned or is marked BLOCKED with reason.

## Lanes

| Lane | Scope | Typical entry points | Chain to trace |
|------|--------|----------------------|----------------|
| **Engine A** | Factor confluence, forex scoring, Engine A payloads | `scanner.py`, `scoring.py`, `factor_scoring.py`, `forex_scoring.py`, Engine A routes | provider → candle policy → scoring/confidence → gates → SL/TP/RR → payload → consumer → tests |
| **Engine B** | SMC/ICT structure, Engine B AI advisory | `market_structure.py`, `zone_registry.py`, `engine_b_ai.py`, TV Chart overlays | provider → structure/zone logic → scoring → gates → payload → UI overlay → tests |
| **Engine D / Scalp Workbench** | Scalp lab, volume profile | `scalp_engine.py`, `volume_profile.py`, Scalp Workbench UI/API | provider → candle policy → scalp scoring → gates → SL/TP/RR → workbench payload → tests |
| **UI / API contracts** | Routes, React consumers, AI review payloads | `athena_app/api/`, `static/react-app/`, `ai_context.py`, review route modules | route → payload builder → frontend consumer → contract tests |
| **Tests / imports** | pytest coverage, forbidden imports, stale assertions | `tests/test_*.py`, `tests/AGENTS.md` | test → production route/module under test; confirm no `athena.py` imports; flag tests asserting stale behavior |

Skip lanes outside task scope — mark **NOT REVIEWED** with reason in the consolidated map. Do not use **PASS** if a required lane for the change was not inspected.

## Consolidation

After all lanes return, the lead reviewer:

1. Merges review maps (dedupe files, preserve per-lane NOT REVIEWED)
2. Runs global search pass and adversarial pass (see parent `SKILL.md`)
3. Resolves cross-lane findings (e.g. UI lane calling endpoint Engine A lane did not patch)
4. Emits final Coverage / Findings / Verdict sections

Verdict rules unchanged: no **PASS** when required execution paths were not inspected.
