"""Frozen ASE feature schema (v2.1 §5)."""

from __future__ import annotations

import hashlib
import json

CORE_FEATURES = [
    "ret_z_1",
    "ret_z_4",
    "ret_z_8",
    "ret_z_24",
    "vol_level",
    "vol_of_vol",
    "vol_regime",
    "dd_64",
    "ru_64",
    "range_pos",
    "volu_z",
    "xsec_pct",
    "family_dispersion",
    "beta_bench",
    "sig_tsmom",
    "sig_carry",
    "sig_xsec",
    "sig_mr",
    "agreement_count",
    "conflict_flag",
    "candidate_dir",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "session",
    "instrument_id",
    "subclass",
]

SWING_FEATURES = ["ret_z_1", "ret_z_5", "ret_z_21"]

ENRICHED_FEATURES = ["cot_pct", "cot_delta_4w", "funding_z", "oi_delta_z"]

CATEGORICAL_FEATURES = ["instrument_id", "subclass", "session", "vol_regime"]


def schema_hash(*, horizon: str = "intraday", enriched: bool = False) -> str:
    if horizon == "swing":
        features = [f for f in CORE_FEATURES if not f.startswith("ret_z_")] + SWING_FEATURES
    else:
        features = list(CORE_FEATURES)
    if enriched:
        features = features + ENRICHED_FEATURES
    payload = json.dumps(sorted(features), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()
