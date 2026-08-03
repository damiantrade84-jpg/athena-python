"""Declarative catalog of every Tuning Lab knob.

Single source of truth for both ``GET /api/experiment/knobs`` (what the UI
renders) and ``overlay.py`` (how a flat ``{knob_id: value}`` payload expands
into the real nested config shape). Every knob maps onto a config surface
that already exists and is already read by live scoring:

- TF roles            -> ``ENGINE_TF_ROLE_OVERRIDES.BY_GROUP`` (timeframe_policy.py)
- Engine A weights     -> ``ENGINE_A_QUANT_WEIGHTS_BY_GROUP`` (engine_a_v3/profile.py)
- Engine A threshold   -> ``ENGINE_A_SCORE_GROUP_THRESHOLDS`` (engine_a_v3/profile.py)
- Engine B gates       -> ``NAKED_ENGINE.score_group_overrides`` (market_structure.py)
- Engine B SL clamps   -> ``ENGINE_B_ATR_SL_CLAMP_SCORE_GROUP_OVERRIDES``
                          (market_structure.py ATR clamp / tighten / TP1-retry
                          paths — the ONLY surface those fields are read from;
                          the same-named keys under NAKED_ENGINE.score_group_overrides
                          have no consumer and are inert)
- New indicator terms  -> ``ENGINE_A_V3_MOMENTUM_BLEND.BY_GROUP`` /
                          ``ENGINE_A_V3_LOCATION.BY_GROUP`` /
                          ``ENGINE_A_V3_VOLUME_BLEND.BY_GROUP`` /
                          ``ENGINE_B_WEIGHTED_SCORING.COMPONENT_WEIGHTS_BY_GROUP``
                          (engine_a_v3/quant_scorer.py::_group_scoped_blend_weight,
                          engine_b_quality.py::weighted_scoring_config_for_group)

``scope`` tells the UI/overlay how a knob is keyed:
- ``"group_style"``: config path has both a ``{group}`` and a ``{style}`` slot.
- ``"group"``: config path has only a ``{group}`` slot — this includes every
  new indicator-weight knob. A group with no override falls back to the
  existing process-wide value (today, 0.0 for all of them), so tuning and
  pushing one group's indicator weight never touches any other group's
  scoring — the same "test on one group, ship only for that group" contract
  ``ENGINE_A_QUANT_WEIGHTS_BY_GROUP`` already gives the core component weights.
- ``"global"``: no group/style slot at all (none of today's knobs use this —
  reserved for a knob whose only config surface is genuinely process-wide).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TF_ROLES: tuple[str, ...] = ("regime", "bias", "structure", "setup", "trigger")
# Slowest -> fastest, matches timeframe_policy.TIMEFRAME_LADDER.
TF_LADDER: tuple[str, ...] = ("D1", "H4", "H1", "M30", "M15", "M5", "M1")

_ENGINE_A_WEIGHT_COMPONENTS: tuple[str, ...] = ("trend", "momentum", "location", "volume")

_ENGINE_B_GATE_FIELDS: dict[str, tuple[str, float | None, float | None]] = {
    # Engine B style-profile min_score lives on a ~0-8 gate-points scale
    # (shipped profiles use 3.0-4.0, see engine_b_snapshot.py) — a 0-100 range
    # invited values that block every signal in the group.
    "min_score": ("float", 0.0, 10.0),
    "min_rr": ("float", 0.0, 10.0),
    "min_room_atr": ("float", 0.0, 5.0),
    "space_rr_substitute": ("bool", None, None),
    "profile_trusted": ("bool", None, None),
    "macro_required": ("bool", None, None),
}

# SL-width ATR clamps are consumed ONLY from this flat per-group surface
# (market_structure.py reads ENGINE_B_ATR_SL_CLAMP_SCORE_GROUP_OVERRIDES at the
# clamp, SL-tighten and TP1-retry sites). They are deliberately NOT under
# NAKED_ENGINE.score_group_overrides — keys placed there are never read.
_ENGINE_B_SL_CLAMP_FIELDS: dict[str, tuple[str, float | None, float | None]] = {
    "max_sl_atr": ("float", 0.1, 10.0),
    "min_sl_atr": ("float", 0.05, 5.0),
}


@dataclass(frozen=True)
class Knob:
    id: str
    label: str
    applies_to: tuple[str, ...]  # subset of ("A", "B")
    kind: str  # "enum" | "float" | "bool"
    config_path: tuple[str, ...]  # "{group}"/"{style}" are template slots
    scope: str  # "group_style" | "group" | "global"
    default: Any = None
    choices: tuple[str, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "appliesTo": list(self.applies_to),
            "kind": self.kind,
            "scope": self.scope,
            "default": self.default,
            "choices": list(self.choices) if self.choices else None,
            "min": self.minimum,
            "max": self.maximum,
            "description": self.description,
            "configPath": list(self.config_path),
        }


def _tf_role_knobs() -> list[Knob]:
    labels = {
        "regime": "Regime (slowest)",
        "bias": "Bias",
        "structure": "Structure",
        "setup": "Setup",
        "trigger": "Trigger (fastest)",
    }
    return [
        Knob(
            id=f"tf_role.{role}",
            label=labels[role],
            applies_to=("A", "B"),
            kind="enum",
            config_path=("ENGINE_TF_ROLE_OVERRIDES", "BY_GROUP", "{group}", "{style}", role),
            scope="group_style",
            choices=TF_LADDER,
            description=(
                "Timeframe role override for this group/style. Must respect "
                "D1 > H4 > H1 > M30 > M15 > M5 > M1 across regime..trigger."
            ),
        )
        for role in TF_ROLES
    ]


def _engine_a_weight_knobs() -> list[Knob]:
    return [
        Knob(
            id=f"engine_a.weight.{component}",
            label=f"{component.title()} weight",
            applies_to=("A",),
            kind="float",
            config_path=("ENGINE_A_QUANT_WEIGHTS_BY_GROUP", "{group}", component),
            scope="group",
            minimum=0.0,
            maximum=1.0,
            description=(
                "Engine A component weight for this score group "
                "(renormalized to sum 1.0 across trend/momentum/location/volume)."
            ),
        )
        for component in _ENGINE_A_WEIGHT_COMPONENTS
    ]


def _engine_a_threshold_knobs() -> list[Knob]:
    return [
        Knob(
            id="engine_a.trade_threshold",
            label="Trade threshold",
            applies_to=("A",),
            kind="float",
            config_path=("ENGINE_A_SCORE_GROUP_THRESHOLDS", "{group}"),
            scope="group",
            minimum=0.0,
            maximum=3.0,
            description=(
                "Minimum aligned-quality score (0..3 scale) required to trade this group."
            ),
        )
    ]


_ENGINE_B_GATE_DESCRIPTIONS: dict[str, str] = {
    "macro_required": (
        "Engine B per-(group,style) gate override: macro_required. FOREX ONLY — "
        "activates the fail-closed DXY macro-correlation gate for this group "
        "(market_structure.py); inert for non-forex groups. This is not the "
        "macro swing-alignment gate (require_macro_align)."
    ),
}


def _engine_b_gate_knobs() -> list[Knob]:
    out: list[Knob] = []
    for field_name, (kind, lo, hi) in _ENGINE_B_GATE_FIELDS.items():
        out.append(
            Knob(
                id=f"engine_b.gate.{field_name}",
                label=field_name.replace("_", " ").title(),
                applies_to=("B",),
                kind=kind,
                config_path=("NAKED_ENGINE", "score_group_overrides", "{group}", "{style}", field_name),
                scope="group_style",
                minimum=lo,
                maximum=hi,
                description=_ENGINE_B_GATE_DESCRIPTIONS.get(
                    field_name, f"Engine B per-(group,style) gate override: {field_name}."
                ),
            )
        )
    for field_name, (kind, lo, hi) in _ENGINE_B_SL_CLAMP_FIELDS.items():
        out.append(
            Knob(
                id=f"engine_b.gate.{field_name}",
                label=field_name.replace("_", " ").title(),
                applies_to=("B",),
                kind=kind,
                config_path=("ENGINE_B_ATR_SL_CLAMP_SCORE_GROUP_OVERRIDES", "{group}", field_name),
                scope="group",
                minimum=lo,
                maximum=hi,
                description=(
                    f"Engine B per-group SL-width ATR clamp: {field_name}. Read from "
                    "ENGINE_B_ATR_SL_CLAMP_SCORE_GROUP_OVERRIDES by the live clamp "
                    "paths (applies to every style in this group)."
                ),
            )
        )
    return out


def _indicator_knobs() -> list[Knob]:
    return [
        Knob(
            id="indicator.momentum.stoch_weight",
            label="Stochastic (momentum blend)",
            applies_to=("A",),
            kind="float",
            config_path=("ENGINE_A_V3_MOMENTUM_BLEND", "BY_GROUP", "{group}", "STOCH_WEIGHT"),
            scope="group",
            default=0.0,
            minimum=0.0,
            maximum=1.0,
            description=(
                "Adds a Stochastic %K term to Engine A's momentum blend, alongside "
                "the existing RSI/DI/MACD terms. Scoped to this score group only — "
                "every other group keeps today's exact momentum scoring."
            ),
        ),
        Knob(
            id="indicator.momentum.cci_weight",
            label="CCI (momentum blend)",
            applies_to=("A",),
            kind="float",
            config_path=("ENGINE_A_V3_MOMENTUM_BLEND", "BY_GROUP", "{group}", "CCI_WEIGHT"),
            scope="group",
            default=0.0,
            minimum=0.0,
            maximum=1.0,
            description="Adds a Commodity Channel Index term to Engine A's momentum blend, scoped to this score group only.",
        ),
        Knob(
            id="indicator.momentum.williams_r_weight",
            label="Williams %R (momentum blend)",
            applies_to=("A",),
            kind="float",
            config_path=("ENGINE_A_V3_MOMENTUM_BLEND", "BY_GROUP", "{group}", "WILLIAMS_R_WEIGHT"),
            scope="group",
            default=0.0,
            minimum=0.0,
            maximum=1.0,
            description="Adds a Williams %R term to Engine A's momentum blend, scoped to this score group only.",
        ),
        Knob(
            id="indicator.momentum.roc_weight",
            label="Rate of Change (momentum blend)",
            applies_to=("A",),
            kind="float",
            config_path=("ENGINE_A_V3_MOMENTUM_BLEND", "BY_GROUP", "{group}", "ROC_WEIGHT"),
            scope="group",
            default=0.0,
            minimum=0.0,
            maximum=1.0,
            description="Adds a Rate-of-Change term to Engine A's momentum blend, scoped to this score group only.",
        ),
        Knob(
            id="indicator.location.keltner_weight",
            label="Keltner Channel (location blend)",
            applies_to=("A",),
            kind="float",
            config_path=("ENGINE_A_V3_LOCATION", "BY_GROUP", "{group}", "KELTNER_BLEND_WEIGHT"),
            scope="group",
            default=0.0,
            minimum=0.0,
            maximum=1.0,
            description=(
                "Blends a Keltner-channel position term into Engine A's location "
                "component, alongside the existing EMA-distance/Bollinger logic. "
                "Scoped to this score group only."
            ),
        ),
        Knob(
            id="indicator.volume.mfi_weight",
            label="Money Flow Index (volume blend)",
            applies_to=("A",),
            kind="float",
            config_path=("ENGINE_A_V3_VOLUME_BLEND", "BY_GROUP", "{group}", "MFI_WEIGHT"),
            scope="group",
            default=0.0,
            minimum=0.0,
            maximum=1.0,
            description="Blends an MFI term into Engine A's volume component, scoped to this score group only.",
        ),
        Knob(
            id="indicator.engine_b.momentum_oscillator_weight",
            label="Momentum oscillator confluence (Stoch/CCI/Williams %R)",
            applies_to=("B",),
            kind="float",
            config_path=("ENGINE_B_WEIGHTED_SCORING", "COMPONENT_WEIGHTS_BY_GROUP", "{group}", "momentum_oscillator_confluence"),
            scope="group",
            default=0.0,
            minimum=0.0,
            maximum=1.0,
            description=(
                "Adds a Stochastic/CCI/Williams %R composite as a new Engine B "
                "confluence subscore. Requires ENGINE_B_WEIGHTED_SCORING.ENABLED "
                "(already on in this deployment). Scoped to this score group only."
            ),
        ),
    ]


def all_knobs() -> list[Knob]:
    return (
        _tf_role_knobs()
        + _engine_a_weight_knobs()
        + _engine_a_threshold_knobs()
        + _engine_b_gate_knobs()
        + _indicator_knobs()
    )


def find_knob(knob_id: str) -> Knob | None:
    return next((k for k in all_knobs() if k.id == knob_id), None)


def catalog(engine: str) -> dict[str, Any]:
    engine_u = str(engine or "").strip().upper()
    if engine_u not in ("A", "B"):
        raise ValueError(f"unsupported engine: {engine!r}")
    knobs = [k for k in all_knobs() if engine_u in k.applies_to]
    return {
        "engine": engine_u,
        "tfLadder": list(TF_LADDER),
        "tfRoles": list(TF_ROLES),
        "knobs": [k.as_dict() for k in knobs],
    }


def validate_tf_role_selection(roles: dict[str, str]) -> None:
    """Fail-fast client-facing check that a partial TF-role selection respects
    the slowest-to-fastest ladder. This is a UX preflight only — the real,
    authoritative check is ``timeframe_policy.validate_timeframe_role_order``,
    run for real when the overlay is actually resolved by the worker.
    """
    order = {tf: index for index, tf in enumerate(TF_LADDER)}
    indices: list[tuple[str, int]] = []
    for role in TF_ROLES:
        tf = roles.get(role)
        if not tf:
            continue
        if tf not in order:
            raise ValueError(f"unknown timeframe {tf!r} for role {role!r}")
        indices.append((role, order[tf]))
    for (role_a, idx_a), (role_b, idx_b) in zip(indices, indices[1:]):
        if idx_a > idx_b:
            raise ValueError(
                f"timeframe role order violated: {role_a} must not be faster than {role_b}"
            )
