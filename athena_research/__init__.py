"""
Athena Research Lab
-------------------
Standalone VectorBT-based strategy discovery framework.

SAFETY CONTRACT:
  - This module NEVER imports athena.py, execution.py, mt5_executor.py, or bybit_executor.py.
  - This module NEVER modifies live thresholds in config.yaml.
  - All outputs are read-only reports and metrics.
  - Backtest discovery thresholds are intentionally lower than live engine gates.
"""

__version__ = "1.0.0"
__all__ = [
    "data_loader",
    "strategies",
    "metrics",
    "reporting",
    "ai_analyst",
    "prompt_builder",
    "run_manager",
    "autopilot",
    "autopilot_session",
    "research_context",
    "research_lab_routes",
    "fast_gate_lab",
    "fast_live_gate_replay",
]

# Execution import guard — raise immediately if someone accidentally imports live modules
_FORBIDDEN_IMPORTS = {
    "athena",
    "execution",
    "mt5_executor",
    "bybit_executor",
    "auto_trader",
    "risk_engine",
}

def _check_no_live_imports() -> None:
    import sys
    violations = [m for m in _FORBIDDEN_IMPORTS if m in sys.modules]
    if violations:
        raise ImportError(
            f"Research Lab detected live execution modules in sys.modules: {violations}. "
            "Research code must not depend on live broker infrastructure."
        )
