"""Pure Engine B decision snapshot shared by live scanning and historical replay.

The function in this module owns no historical simulation behavior.  It is the
single deterministic orchestration contract around production ``NakedEngine``
primitives for one point-in-time market snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from config import CONFIG
from engine_b_subsystems import engine_b_direction_min_score_gap, engine_b_pick_directional_candidate
from indicators import calc_atr, calc_indicators, calc_indicators_with_normalized
from market_structure import (
    NakedEngine,
    _engine_b_regime_gate,
    engine_b_confidence_passes,
    engine_b_live_trigger_kwargs,
    resolve_engine_b_h4_snap,
    resolve_engine_b_tfs,
)

ENGINE_B_SNAPSHOT_CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class EngineBSnapshotResult:
    contract_version: str
    selected: dict[str, Any] | None
    candidates: tuple[dict[str, Any], ...]
    rejection_reason: str | None
    resolved_style: str
    style_profile: dict[str, Any]
    regime: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "contractVersion": self.contract_version,
            "selected": self.selected,
            "candidates": list(self.candidates),
            "rejectionReason": self.rejection_reason,
            "resolvedStyle": self.resolved_style,
            "styleProfile": self.style_profile,
            "regime": self.regime,
        }


class EngineBHistoricalFeatureCache:
    """Per-task cache for Engine B's direction-independent structure features.

    The expensive D1/H4/H1 structure context is rebuilt only when one of its
    owning confirmed bars changes. Faster trigger bars refresh only the trigger
    candle view and trigger-local ATR. The cache is never shared across symbols,
    runs, or live scanner state.
    """

    def __init__(
        self,
        *,
        trigger_atr_fn: Callable[[str, list], float | None] | None = None,
    ) -> None:
        self._structure_key: tuple[Any, ...] | None = None
        self._base: dict[str, Any] | None = None
        self._d1_snapshot_key: tuple[Any, ...] | None = None
        self._d1_snapshot: dict[str, Any] = {}
        self._h4_snapshot_key: tuple[Any, ...] | None = None
        self._h4_snapshot: dict[str, Any] = {}
        self._regime_label = "RANGING"
        self._atr_key: tuple[Any, ...] | None = None
        self._atr = 0.0
        # Optional O(1) trigger-ATR provider (e.g. a full-series causal ATR
        # precompute). Returning None falls back to the per-call walk, so the
        # default behavior is unchanged when no provider is injected.
        self._trigger_atr_fn = trigger_atr_fn
        self.structure_builds = 0
        self.trigger_refreshes = 0
        self.cache_hits = 0

    @staticmethod
    def _role_identity(rows: list[dict[str, Any]]) -> tuple[Any, ...]:
        if not rows:
            return (0, None)
        last = rows[-1]
        return (
            len(rows),
            last.get("time"),
            last.get("open"),
            last.get("high"),
            last.get("low"),
            last.get("close"),
            last.get("volume", last.get("vol")),
            bool(last.get("is_forming")),
        )

    @staticmethod
    def _rows(value: Any) -> list[dict[str, Any]]:
        return value if isinstance(value, list) else list(value or [])

    def resolve_snapshot_inputs(
        self,
        *,
        role_candles: Mapping[str, list[dict[str, Any]]],
        asset_type: str,
        atr_tf: str,
    ) -> dict[str, Any]:
        """Cache causal higher-timeframe inputs until their owning bar changes."""

        d1 = self._rows(role_candles.get("D1"))
        h4 = self._rows(role_candles.get("H4"))
        atr_rows = self._rows(role_candles.get(atr_tf))

        d1_key = self._role_identity(d1)
        if d1_key != self._d1_snapshot_key:
            try:
                self._d1_snapshot = (
                    (calc_indicators_with_normalized(d1, asset_type) or {}).get("snap")
                    or {}
                )
            except Exception:
                self._d1_snapshot = {}
            self._d1_snapshot_key = d1_key

        h4_key = self._role_identity(h4)
        if h4_key != self._h4_snapshot_key:
            try:
                self._h4_snapshot = resolve_engine_b_h4_snap(h4, asset_type)
            except Exception:
                self._h4_snapshot = {}
            self._regime_label = str(resolve_engine_b_regime_label(h4, asset_type)).upper()
            self._h4_snapshot_key = h4_key

        atr_key = (str(atr_tf).upper(), self._role_identity(atr_rows))
        if atr_key != self._atr_key:
            try:
                self._atr = float(_atr_from_candles(atr_rows))
            except (TypeError, ValueError):
                self._atr = 0.0
            self._atr_key = atr_key

        return {
            "atr": self._atr,
            "regime": self._regime_label,
            "d1_snapshot": self._d1_snapshot,
            "h4_snapshot": self._h4_snapshot,
        }

    def resolve(
        self,
        *,
        naked: NakedEngine,
        role_candles: Mapping[str, list[dict[str, Any]]],
        atr: float,
        regime: str,
        style: str,
        asset_type: str,
        fallback_rr: float,
        trigger_tf: str,
        build: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        d1 = self._rows(role_candles.get("D1"))
        h4 = self._rows(role_candles.get("H4"))
        h1 = self._rows(role_candles.get("H1"))
        key = (
            self._role_identity(d1),
            self._role_identity(h4),
            self._role_identity(h1),
            round(float(atr), 12),
            str(regime),
            str(style),
            str(asset_type),
            round(float(fallback_rr), 8),
        )
        if self._base is None or key != self._structure_key:
            self._base = build()
            self._structure_key = key
            self.structure_builds += 1
            return self._base

        self.cache_hits += 1
        refreshed = dict(self._base)
        trigger_rows = self._rows(role_candles.get(trigger_tf))
        refreshed["_trigger_candles"] = trigger_rows
        trigger_atr = (
            self._trigger_atr_fn(trigger_tf, trigger_rows)
            if self._trigger_atr_fn is not None
            else None
        )
        if trigger_atr is None:
            period = 14
            try:
                period = int(refreshed.get("trigger_atr_period") or 14)
            except (TypeError, ValueError):
                period = 14
            score_group = refreshed.get("_score_group")
            asset = str(refreshed.get("asset_type") or "")
            if period == 14:
                from market_structure import engine_b_trigger_atr_period

                period = engine_b_trigger_atr_period(
                    score_group, asset, trigger_tf
                )
            trigger_atr = naked._compute_atr_from_candles(
                trigger_rows, period=period, fallback=float(atr)
            )
        refreshed["trigger_atr"] = trigger_atr
        refreshed["_trigger_tf_override"] = trigger_tf
        refreshed_tfs = dict(refreshed.get("_tfs") or {})
        refreshed_tfs["trigger"] = trigger_tf
        refreshed["_tfs"] = refreshed_tfs
        self.trigger_refreshes += 1
        return refreshed

    def diagnostics(self) -> dict[str, int]:
        return {
            "structureBuilds": self.structure_builds,
            "triggerRefreshes": self.trigger_refreshes,
            "cacheHits": self.cache_hits,
        }


def resolve_engine_b_style_profile(
    style: str | None,
    score_group: str | None = None,
    asset_type: str | None = None,
    *,
    symbol: str = "",
    speed_state: Any | None = None,
    config: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Resolve the production Engine B numeric and timeframe profile."""

    cfg = config if isinstance(config, Mapping) else CONFIG
    resolved = str(style or "auto").strip().lower()
    if resolved not in {"auto", "scalp", "intraday", "swing"}:
        resolved = "auto"
    if resolved == "auto":
        resolved = "intraday"
    tf_asset = str(asset_type or "forex").lower()
    tf_by_style = {
        selected: resolve_engine_b_tfs(
            tf_asset,
            selected,
            symbol=symbol,
            score_group=score_group,
            speed_state=speed_state,
        )
        for selected in ("scalp", "intraday", "swing")
    }
    profiles: dict[str, dict[str, Any]] = {
        "scalp": {
            "min_score": 3.0,
            "min_room_atr": 0.35,
            "min_rr": 1.0,
            "fallback_rr": 1.8,
            "require_macro_align": False,
        },
        "intraday": {
            "min_score": 4.0,
            "min_room_atr": 0.7,
            "min_rr": 1.2,
            "fallback_rr": 1.8,
            "require_macro_align": False,
        },
        "swing": {
            "min_score": 4.0,
            "min_room_atr": 1.0,
            "min_rr": 1.6,
            "fallback_rr": 2.5,
            "require_macro_align": False,
        },
    }
    configured_profiles = ((cfg.get("NAKED_ENGINE", {}) or {}).get("style_profiles", {}) or {})
    for profile_name, defaults in profiles.items():
        configured = configured_profiles.get(profile_name, {})
        if isinstance(configured, Mapping):
            defaults.update(configured)
    profile = dict(profiles[resolved])
    if score_group:
        group_overrides = (
            ((cfg.get("NAKED_ENGINE", {}) or {}).get("score_group_overrides", {}) or {}).get(score_group, {})
        )
        if isinstance(group_overrides, Mapping):
            style_override = group_overrides.get(resolved, {})
            if isinstance(style_override, Mapping):
                profile.update(style_override)
    selected_tfs = tf_by_style[resolved]
    profile.update(
        {
            "regime_tf": selected_tfs["regime"],
            "bias_tf": selected_tfs["bias"],
            "structure_tf": selected_tfs["struct"],
            "zone_tf": selected_tfs["zone"],
            "setup_tf": selected_tfs["setup"],
            "entry_tf": selected_tfs["trigger"],
            "execution_tf": selected_tfs["execution"],
            "atr_tf": selected_tfs["atr"],
            "timeframe_policy_version": selected_tfs["policy_version"],
            "timeframe_policy_profile": selected_tfs["policy_profile"],
            "style": resolved,
        }
    )
    if score_group:
        profile["score_group"] = score_group
    if not bool(cfg.get("ENGINE_B_STRUCTURE_GATE_ENABLED", True)):
        profile["disable_structure_gate"] = True
    return resolved, profile


def resolve_engine_b_regime_label(
    h4_candles: list[dict[str, Any]],
    pair_type: str = "stock",
    regime_hint: Mapping[str, Any] | str | None = None,
) -> str:
    standard = frozenset({"TRENDING", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY"})
    forex_map = {
        "TREND_PULLBACK": "TRENDING",
        "LONDON_BREAKOUT": "TRENDING",
        "MOMENTUM_BURST": "TRENDING",
        "RANGE_REVERSION": "RANGING",
        "MEAN_REVERSION": "RANGING",
        "CONSOLIDATION_BREAK": "HIGH_VOLATILITY",
        "LOW_VOL_ENV": "LOW_VOLATILITY",
    }
    raw: Any = None
    if isinstance(regime_hint, Mapping):
        raw = regime_hint.get("label") or regime_hint.get("regime")
    elif regime_hint:
        raw = regime_hint
    if raw:
        normalized = str(raw).upper()
        if normalized not in {"NONE", "UNKNOWN", ""}:
            if str(pair_type or "").lower() == "forex":
                if normalized in forex_map:
                    return forex_map[normalized]
                if normalized in standard:
                    return normalized
            else:
                return normalized
    if not h4_candles or len(h4_candles) < 20:
        return "RANGING"
    try:
        from regime import detect_regime

        indicators = calc_indicators(h4_candles)
        snapshot = indicators.get("snap", {}) if isinstance(indicators, dict) else {}
        regime = detect_regime(snapshot, pair_type or "stock", bb_width_pct=None)
        return str(regime.get("label", "RANGING")).upper()
    except Exception:
        return "RANGING"


def _atr_from_candles(candles: list[dict[str, Any]], period: int = 14) -> float:
    if len(candles) < max(15, int(period) + 1):
        return 0.0
    try:
        series = calc_atr(
            [float(row["high"]) for row in candles],
            [float(row["low"]) for row in candles],
            [float(row["close"]) for row in candles],
            int(period),
        )
        valid = [float(value) for value in series if value is not None and float(value) > 0]
        return valid[-1] if valid else 0.0
    except (KeyError, TypeError, ValueError):
        return 0.0


def evaluate_engine_b_snapshot(
    pair: dict[str, Any],
    role_candles: Mapping[str, list[dict[str, Any]]],
    *,
    current_price: float,
    style: str,
    atr_override: float | None = None,
    score_group: str | None = None,
    style_profile: Mapping[str, Any] | None = None,
    regime_label: str | None = None,
    d1_snapshot: Mapping[str, Any] | None = None,
    h4_snapshot: Mapping[str, Any] | None = None,
    dxy_h4_closes: list[float] | None = None,
    learning_context: Mapping[str, Any] | None = None,
    engine: NakedEngine | None = None,
    context_mode: str = "historical",
    precomputed_structure: dict[str, Any] | None = None,
    feature_cache: EngineBHistoricalFeatureCache | None = None,
    gate_fn: Callable[..., tuple[bool, float]] = engine_b_confidence_passes,
    picker_fn: Callable[..., dict[str, Any] | None] = engine_b_pick_directional_candidate,
    conflict_fn: Callable[[str, dict[str, Any]], bool] | None = None,
) -> EngineBSnapshotResult:
    """Evaluate both Engine B directions from explicit point-in-time inputs.

    ``context_mode='historical'`` disables mutable zone registries and live trade
    buckets.  Live callers may supply ``context_mode='live'`` and a fresh engine
    instance to preserve the production registry contract.
    """

    asset_type = str(pair.get("type") or pair.get("asset_type") or "")
    symbol = str(pair.get("display") or pair.get("symbol") or "")
    resolved_style, resolved_profile = resolve_engine_b_style_profile(
        style,
        score_group,
        asset_type,
        symbol=symbol,
    )
    if isinstance(style_profile, Mapping):
        # Numeric production overrides and stamped timeframe roles are accepted
        # only as an explicit immutable snapshot supplied by the caller.
        resolved_profile.update(dict(style_profile))
        resolved_profile["style"] = resolved_style

    historical = str(context_mode).lower() != "live"
    required = {"D1", "H4", "H1"}
    missing = sorted(tf for tf in required if not role_candles.get(tf))
    if missing and not (engine is not None and atr_override is not None):
        return EngineBSnapshotResult(
            ENGINE_B_SNAPSHOT_CONTRACT_VERSION,
            None,
            (),
            "missing_required_timeframes:" + ",".join(missing),
            resolved_style,
            resolved_profile,
            str(regime_label or "RANGING"),
        )
    d1 = (
        role_candles["D1"]
        if isinstance(role_candles["D1"], list)
        else list(role_candles["D1"])
    )
    h4 = (
        role_candles["H4"]
        if isinstance(role_candles["H4"], list)
        else list(role_candles["H4"])
    )
    h1 = (
        role_candles["H1"]
        if isinstance(role_candles["H1"], list)
        else list(role_candles["H1"])
    )
    atr_tf = str(resolved_profile.get("atr_tf") or "H1").upper()
    raw_atr_candles = role_candles.get(atr_tf) or []
    atr_candles = (
        raw_atr_candles
        if isinstance(raw_atr_candles, list)
        else list(raw_atr_candles)
    )
    cached_inputs = (
        feature_cache.resolve_snapshot_inputs(
            role_candles=role_candles,
            asset_type=asset_type,
            atr_tf=atr_tf,
        )
        if historical
        and feature_cache is not None
        and (
            atr_override is None
            or regime_label is None
            or d1_snapshot is None
            or h4_snapshot is None
        )
        else None
    )
    try:
        atr = (
            float(atr_override)
            if atr_override is not None
            else float(cached_inputs["atr"])
            if cached_inputs is not None
            else _atr_from_candles(atr_candles)
        )
    except (TypeError, ValueError):
        atr = 0.0
    if atr <= 0 or current_price <= 0:
        return EngineBSnapshotResult(
            ENGINE_B_SNAPSHOT_CONTRACT_VERSION,
            None,
            (),
            "ENGINE_B_REASON_ATR_ZERO" if atr <= 0 else "current_price_invalid",
            resolved_style,
            resolved_profile,
            str(regime_label or "RANGING"),
        )
    regime = str(
        regime_label
        or (cached_inputs or {}).get("regime")
        or resolve_engine_b_regime_label(h4, asset_type)
    ).upper()
    naked = engine or NakedEngine()
    if not historical:
        naked.set_registry_context(pair.get("symbol") or pair.get("display"))
    if d1_snapshot is None:
        if cached_inputs is not None:
            d1_snapshot = cached_inputs["d1_snapshot"]
        else:
            try:
                d1_snapshot = (
                    (calc_indicators_with_normalized(d1, asset_type) or {}).get("snap")
                    or {}
                )
            except Exception:
                d1_snapshot = {}
    if h4_snapshot is None:
        if cached_inputs is not None:
            h4_snapshot = cached_inputs["h4_snapshot"]
        else:
            try:
                h4_snapshot = resolve_engine_b_h4_snap(h4, asset_type)
            except Exception:
                h4_snapshot = {}
    trigger_kwargs = engine_b_live_trigger_kwargs(resolved_profile, dict(role_candles))
    precompute = precomputed_structure
    if precompute is None:
        def _build_structure() -> dict[str, Any]:
            return naked.precompute_structure_data(
                d1,
                h4,
                h1,
                float(current_price),
                float(atr),
                regime=regime,
                fallback_rr=float(resolved_profile.get("fallback_rr", 2.0)),
                asset_type=asset_type,
                enable_zone_registry=not historical,
                d1_snap=dict(d1_snapshot or {}),
                h4_snap=dict(h4_snapshot or {}),
                style=resolved_style,
                pair=pair,
                trade_bucket_require_fresh=not historical,
                trade_bucket_historical_mode=historical,
                struct_candles_confirmed=historical,
                dxy_h4_closes=dxy_h4_closes,
                **trigger_kwargs,
            )

        trigger_tf = str(resolved_profile.get("entry_tf") or "H1").upper()
        if historical and feature_cache is not None:
            precompute = feature_cache.resolve(
                naked=naked,
                role_candles=role_candles,
                atr=float(atr),
                regime=regime,
                style=resolved_style,
                asset_type=asset_type,
                fallback_rr=float(resolved_profile.get("fallback_rr", 2.0)),
                trigger_tf=trigger_tf,
                build=_build_structure,
            )
        else:
            precompute = _build_structure()
    candidates: list[dict[str, Any]] = []
    for direction in ("LONG", "SHORT"):
        structure = naked.analyze_structure_direction(precompute, float(current_price), direction)
        if structure.get("structural_verdict") != "CLEAR":
            candidates.append(
                {
                    "direction": direction,
                    "passed": False,
                    "score": 0.0,
                    "structuralVerdict": structure.get("structural_verdict"),
                    "rejectionReason": structure.get("reason") or structure.get("_error") or "structure_not_clear",
                    "structure": structure,
                    "confidence": None,
                }
            )
            continue
        entry_tf = str(resolved_profile.get("entry_tf") or "H1").upper()
        confidence_kwargs = {
            "entry_candles": list(role_candles.get(entry_tf) or []),
            "style_profile": resolved_profile,
        }
        if learning_context:
            confidence = naked.calculate_confidence(
                structure,
                float(current_price),
                direction,
                dict(learning_context),
                **confidence_kwargs,
            )
        else:
            confidence = naked.calculate_confidence(
                structure,
                float(current_price),
                direction,
                **confidence_kwargs,
            )
        lifecycle = str(confidence.get("lifecycle_state") or "").lower()
        gate_ok, min_score = gate_fn(
            confidence,
            resolved_profile,
            regime,
            asset_type,
            diagnostics_context={
                "pair": pair.get("display"),
                "symbol": pair.get("symbol"),
                "asset_class": asset_type,
                "score_group": score_group,
                "style": resolved_style,
            },
        )
        if lifecycle in {"invalidated", "expired"}:
            gate_ok = False
        confidence["passed"] = bool(gate_ok)
        confidence["checklist_passed"] = bool(gate_ok)
        confidence["min_score_scaled"] = float(min_score)
        combined = dict(structure)
        combined.update(confidence)
        combined.update(
            {
                "min_score_used": float(min_score),
                "regime_gate": _engine_b_regime_gate(regime, asset_type),
                "passed": bool(gate_ok),
                "current_price": float(current_price),
                "direction": direction,
                "direction_source": "independent_snapshot",
                "style": resolved_style,
                "regime": regime,
                "contractVersion": ENGINE_B_SNAPSHOT_CONTRACT_VERSION,
            }
        )
        for key in (
            "regime_tf", "bias_tf", "structure_tf", "zone_tf", "setup_tf",
            "entry_tf", "execution_tf", "atr_tf", "timeframe_policy_version",
        ):
            combined[key] = resolved_profile.get(key)
        try:
            score = float(confidence.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        candidates.append(
            {
                "direction": direction,
                "passed": bool(gate_ok),
                "score": score,
                "structuralVerdict": "CLEAR",
                "rejectionReason": None if gate_ok else confidence.get("reason") or "confidence_gate_failed",
                "structure": combined,
                "confidence": confidence,
                "terminal": lifecycle in {"invalidated", "expired"},
            }
        )

    eligible = [
        candidate
        for candidate in candidates
        if candidate["structuralVerdict"] == "CLEAR" and not candidate.get("terminal")
    ]
    if not eligible:
        return EngineBSnapshotResult(
            ENGINE_B_SNAPSHOT_CONTRACT_VERSION,
            None,
            tuple(candidates),
            "no_clear_direction",
            resolved_style,
            resolved_profile,
            regime,
        )
    passed = [
        {
            "direction": candidate["direction"],
            "score": candidate["score"],
            "res_b": candidate["structure"],
            "conf_b": candidate["confidence"],
        }
        for candidate in eligible
        if candidate["passed"]
    ]
    selected: dict[str, Any] | None = None
    if passed:
        picked = picker_fn(passed, min_gap=engine_b_direction_min_score_gap())
        blocked_conflict = bool(
            picked is not None
            and conflict_fn
            and conflict_fn(str(picked["direction"]), dict(picked["res_b"]))
        )
        if picked is not None and not blocked_conflict:
            selected = {
                "direction": picked["direction"],
                "score": float(picked["score"]),
                "passed": True,
                "structure": picked["res_b"],
                "confidence": picked["conf_b"],
            }
        if selected is None:
            return EngineBSnapshotResult(
                ENGINE_B_SNAPSHOT_CONTRACT_VERSION,
                None,
                tuple(candidates),
                "directional_candidate_ambiguous_or_conflicted",
                resolved_style,
                resolved_profile,
                regime,
            )
    if selected is None:
        best = max(eligible, key=lambda item: (bool(item["passed"]), float(item["score"]), str(item["direction"])))
        selected = {
            "direction": best["direction"],
            "score": float(best["score"]),
            "passed": bool(best["passed"]),
            "structure": best["structure"],
            "confidence": best["confidence"],
        }
    return EngineBSnapshotResult(
        ENGINE_B_SNAPSHOT_CONTRACT_VERSION,
        selected,
        tuple(candidates),
        None,
        resolved_style,
        resolved_profile,
        regime,
    )
