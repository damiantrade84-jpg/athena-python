"""Timeframe / entry-path diagnostic audit for Engine A and Engine B.

Research-only. Gated behind ATHENA_TF_ENTRY_PATH_AUDIT=1 or explicit CLI flag.
Does not modify live execution or production thresholds.
"""

from athena_research.tf_entry_path_audit.runner import run_tf_entry_path_audit

__all__ = ["run_tf_entry_path_audit"]
