"""OPUS Engine - an intraday liquidity/auction trading engine.

OPUS is a self-contained decision system. It owns its own indicators, its own
scoring model, its own probability calibration, its own risk model and its own
broker adapters. It shares no scoring surface, threshold, gate or execution
semantic with anything else in this repository.

Design thesis
-------------
Intraday edge is not a pattern. It is the joint occurrence of

  1. a liquidity event      - someone was forced to transact (LDI)
  2. an efficiency imprint  - the move left an unfilled void or was absorbed (VFI / ARC)
  3. a value dislocation    - price is away from the session's accepted value (SAV)
  4. directional flow       - signed participation confirms the side (OFP)
  5. a permissive regime    - the market is in a state where that setup pays (RQS)
  6. a permissive clock     - the time-of-day actually carries follow-through (TCI)

and, decisively,

  7. a cost structure that leaves positive expectancy after paying the spread (CBI)

Most intraday systems die on (7). OPUS makes it a first-class hard gate: a
signal is promoted only when calibrated probability times reward, net of the
measured round-trip cost expressed in R, is positive.
"""

from __future__ import annotations

from opus.version import ENGINE_NAME, ENGINE_VERSION, SCORE_MODEL_VERSION

__all__ = ["ENGINE_NAME", "ENGINE_VERSION", "SCORE_MODEL_VERSION"]
