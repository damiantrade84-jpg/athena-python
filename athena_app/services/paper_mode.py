"""Paper/demo mode enforcement to prevent real broker orders."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PaperModeEnforcer:
    """Enforces paper/demo mode to prevent real broker order placement."""
    
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.paper_soak = config.get("PAPER_SOAK", {})
        self.enabled = self.paper_soak.get("ENABLED", False)
        # Default REAL_ORDERS_ALLOWED to True only if PAPER_SOAK section exists but is not explicitly set
        # If PAPER_SOAK section doesn't exist, assume real orders are allowed
        if "PAPER_SOAK" in config:
            self.real_orders_allowed = self.paper_soak.get("REAL_ORDERS_ALLOWED", False)
        else:
            self.real_orders_allowed = True
        self.log_dir = Path("logs/paper_soak")
        
        if self.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._init_log_files()
    
    def _init_log_files(self):
        """Initialize JSONL log files for paper soak."""
        self.signals_log = self.log_dir / "signals.jsonl"
        self.execution_decisions_log = self.log_dir / "execution_decisions.jsonl"
        self.freshness_blocks_log = self.log_dir / "freshness_blocks.jsonl"
        self.risk_blocks_log = self.log_dir / "risk_blocks.jsonl"
        self.engine_consensus_log = self.log_dir / "engine_consensus.jsonl"
    
    def is_paper_mode(self) -> bool:
        """Check if paper mode is enabled."""
        return self.enabled
    
    def allow_real_orders(self) -> bool:
        """Check if real orders are allowed (always false in paper mode)."""
        if self.enabled:
            return False
        # When not enabled, respect REAL_ORDERS_ALLOWED config, default to True
        return self.real_orders_allowed if self.real_orders_allowed is not None else True
    
    def should_log_signal(self) -> bool:
        """Check if signals should be logged."""
        if not self.enabled:
            return False
        return self.paper_soak.get("LOG_ALL_SIGNALS", True)
    
    def should_log_blocked_signal(self) -> bool:
        """Check if blocked signals should be logged."""
        if not self.enabled:
            return False
        return self.paper_soak.get("LOG_BLOCKED_SIGNALS", True)
    
    def should_log_execution_decision(self) -> bool:
        """Check if execution decisions should be logged."""
        if not self.enabled:
            return False
        return self.paper_soak.get("LOG_EXECUTION_DECISIONS", True)
    
    def requires_freshness_gate(self) -> bool:
        """Check if freshness gate is required."""
        return self.paper_soak.get("REQUIRED_FRESHNESS_GATE", True)
    
    def log_signal(self, signal: dict[str, Any]):
        """Log a signal to the signals JSONL file."""
        if not self.should_log_signal():
            return
        
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": signal.get("symbol"),
            "asset_type": signal.get("asset_type"),
            "timeframe_set": signal.get("timeframe_set"),
            "engine_a": self._extract_engine_info(signal, "engine_a"),
            "engine_b": self._extract_engine_info(signal, "engine_b"),
            "engine_c": self._extract_engine_c_info(signal),
            "engine_d": self._extract_engine_d_info(signal),
            "freshness_status": self._extract_freshness_status(signal),
            "paper_execution": "PAPER_ONLY" if self.enabled else "REAL_ALLOWED",
            "no_real_order": self.enabled,
        }
        
        self._append_jsonl(self.signals_log, entry)
    
    def log_execution_decision(self, decision: dict[str, Any]):
        """Log an execution decision."""
        if not self.should_log_execution_decision():
            return
        
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": decision.get("symbol"),
            "decision": decision.get("decision", "UNKNOWN"),
            "reason": decision.get("reason", ""),
            "paper_mode": self.enabled,
            "no_real_order": self.enabled,
        }
        
        self._append_jsonl(self.execution_decisions_log, entry)
    
    def log_freshness_block(self, signal: dict[str, Any], reason: str):
        """Log a freshness gate block."""
        if not self.should_log_blocked_signal():
            return
        
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": signal.get("symbol"),
            "asset_type": signal.get("asset_type"),
            "block_reason": reason,
            "paper_mode": self.enabled,
        }
        
        self._append_jsonl(self.freshness_blocks_log, entry)
    
    def log_risk_block(self, signal: dict[str, Any], reason: str):
        """Log a risk gate block."""
        if not self.should_log_blocked_signal():
            return
        
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": signal.get("symbol"),
            "asset_type": signal.get("asset_type"),
            "block_reason": reason,
            "paper_mode": self.enabled,
        }
        
        self._append_jsonl(self.risk_blocks_log, entry)
    
    def log_engine_consensus(self, consensus: dict[str, Any]):
        """Log engine consensus decision."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": consensus.get("symbol"),
            "consensus_type": consensus.get("consensus_type"),
            "engine_a_decision": consensus.get("engine_a_decision"),
            "engine_b_decision": consensus.get("engine_b_decision"),
            "engine_c_decision": consensus.get("engine_c_decision"),
            "paper_mode": self.enabled,
        }
        
        self._append_jsonl(self.engine_consensus_log, entry)
    
    def _extract_engine_info(self, signal: dict[str, Any], engine_name: str) -> dict[str, Any]:
        """Extract engine A/B info from signal."""
        engine_data = signal.get(engine_name, {})
        return {
            "score": engine_data.get("score"),
            "direction": engine_data.get("direction"),
            "passed": engine_data.get("passed", False),
        }
    
    def _extract_engine_c_info(self, signal: dict[str, Any]) -> dict[str, Any]:
        """Extract Engine C info from signal."""
        engine_c = signal.get("engine_c", {})
        return {
            "decision_state": engine_c.get("decision_state"),
            "consensus_type": engine_c.get("consensus_type"),
        }
    
    def _extract_engine_d_info(self, signal: dict[str, Any]) -> dict[str, Any] | None:
        """Extract Engine D setup info from signal."""
        engine_d = signal.get("engine_d")
        if not engine_d:
            return None
        return {
            "setup_type": engine_d.get("setup_type"),
            "entry_price": engine_d.get("entry_price"),
        }
    
    def _extract_freshness_status(self, signal: dict[str, Any]) -> dict[str, Any]:
        """Extract freshness status from signal."""
        consistency = signal.get("candleConsistency", {})
        if not consistency:
            return {"status": "UNKNOWN"}
        
        # Get H1 status as representative
        h1_status = consistency.get("H1", {})
        if isinstance(h1_status, dict):
            return {
                "status": h1_status.get("status", "UNKNOWN"),
                "gate_decision": "ALLOW" if h1_status.get("status") in ("OK", "CONFIRMED_ONLY_OK") else "BLOCK",
            }
        elif isinstance(h1_status, str):
            return {
                "status": h1_status,
                "gate_decision": "ALLOW" if h1_status in ("OK", "CONFIRMED_ONLY_OK") else "BLOCK",
            }
        
        return {"status": "UNKNOWN"}
    
    def _append_jsonl(self, filepath: Path, entry: dict[str, Any]):
        """Append an entry to a JSONL file."""
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to write to {filepath}: {e}")


# Global instance (initialized in athena.py)
_paper_mode_enforcer: PaperModeEnforcer | None = None


def init_paper_mode(config: dict[str, Any]) -> PaperModeEnforcer:
    """Initialize the global paper mode enforcer."""
    global _paper_mode_enforcer
    _paper_mode_enforcer = PaperModeEnforcer(config)
    return _paper_mode_enforcer


def get_paper_mode() -> PaperModeEnforcer | None:
    """Get the global paper mode enforcer."""
    return _paper_mode_enforcer


def is_paper_mode_enabled() -> bool:
    """Check if paper mode is enabled."""
    enforcer = get_paper_mode()
    return enforcer.is_paper_mode() if enforcer else False


def allow_real_orders() -> bool:
    """Check if real orders are allowed."""
    enforcer = get_paper_mode()
    if not enforcer:
        return True  # Allow if not configured
    return enforcer.allow_real_orders()
