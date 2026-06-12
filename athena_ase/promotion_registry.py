"""Backward-compatible holdout registry alias (Phase 4)."""

from __future__ import annotations

from athena_ase.registry.holdout import HoldoutRegistry, default_holdout_path

PromotionRegistry = HoldoutRegistry
default_registry_path = default_holdout_path
