"""ASE feature builder — all inputs via PTIS asof() only (§5).

Phase 2 implements the full frozen feature set. Phase 0 establishes the import guard.
"""

from __future__ import annotations

from athena_ase.data.ptis import asof

__all__ = ["asof"]
