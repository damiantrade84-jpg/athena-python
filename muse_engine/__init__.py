"""MUSE — Meridian Undertow Synthesis Engine.

MUSE reads each market as tide meeting undertow:

    TIDE    (atlas D1)    — the slow meridian envelope and weekly-anchored range
    CURRENT (bias H4)     — channel drift, slope persistence and range expansion
    ECHO    (vector M15)  — the sweep echo and how fast price reclaimed it
    SURGE   (vector M15)  — the thrust arc that followed the echo
    HAVEN   (vector M15)  — the fresh lattice of unfilled imbalance to return to
    SPARK   (trigger M5)  — micro-reclaim recency that times the release
    HALO    (advisory)    — carry / COT / skew / funding / sentiment median voice

The four core prisms (echo, surge, haven, compass) fuse with a harmonic mean,
so the weakest prism dominates — stricter than an average, distinct from a
geometric blend. Tide timing scales the result and the halo only ever nudges
it. Deterministic gates decide whether a signal is tellable at all.

The package owns its tide clock, prisms, scoring, levels, persistence and
execution attestation. Market data, broker clients and context feeds are
injected at the runtime boundary so the analytical core stays deterministic
and import-safe. Paper + demo execution only; live stays disabled until
research is explicitly VALIDATED.
"""

from .config import MuseConfig, MuseConfigError, load_muse_config
from .scoring import evaluate_snapshot

__all__ = ["MuseConfig", "MuseConfigError", "evaluate_snapshot", "load_muse_config"]
