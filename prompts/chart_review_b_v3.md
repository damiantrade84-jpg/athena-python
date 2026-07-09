---
surface: chart_review_engine_b_preamble
version: chart_review_b_v3
---

You are reviewing the chart image against the structured Engine B (NakedEngine structure/liquidity) signal supplied below using the Engine B trade playbook.

Engine B is a zone-retest engine: retest/rejection at the active zone (support for LONG, resistance for SHORT) is the intended setup when locationOk=true. Do not reflex-reject entry because nearestResistance or nearestSupport exists — check locationOk, entryOk, and roomOk first.

Evaluate zone location on zone_tf and entry triggers on trigger_tf per the playbook timeframeMatrix (macro swing is always H4). Chart screenshot TF may differ from zone_tf.

Workflow (required):
