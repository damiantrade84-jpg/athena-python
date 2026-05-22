"""AI strategy knowledge layer.

Read-only advisory layer that enriches AI chart-review payloads with explicit
trading-strategy playbooks, deterministic price-action facts, and a structured
verdict schema. This layer never executes trades, mutates engine results, or
changes thresholds. See CLAUDE.md and the design doc at
docs/superpowers/specs/2026-05-22-ai-strategy-knowledge-layer-design.md.
"""

from __future__ import annotations
