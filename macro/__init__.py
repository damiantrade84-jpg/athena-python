"""Athena macro-event layer (V1: FOMC schedule + release feed, macro-risk lockout).

Greenfield, advisory/risk-only. This package:

* ingests the official Fed FOMC calendar page and monetary-policy RSS feed,
* stores macro events locally (idempotent SQLite),
* reuses the existing FRED ingestion (carry_feed) for historical rate confirmation only,
* exposes a macro-risk guard that blocks/downgrades NEW trade candidates during FOMC
  windows without touching any Engine A/B/D score math, SL/TP, sizing, or execution code.

The guard enforces auto-execution blocking exclusively through the pre-existing
``majorEventRisk.blocksAutoExecution`` contract already honored by ``auto_trader.py`` —
it never edits execution/risk/guardian modules and never enables auto execution.
"""

from __future__ import annotations

# Submodules (macro.config, macro.store, macro.macro_guard, ...) are imported on demand.

