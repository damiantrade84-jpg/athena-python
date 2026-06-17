# Forex Edge Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone, research-only forex package that ingests free official data, runs one frozen monthly currency-portfolio study and one frozen fixing-window study, and produces reproducible evidence without any execution or promotion path.

**Architecture:** Add `athena_research/forex_edge/` with isolated source adapters, an immutable Parquet store, explicit quality contracts, separate portfolio and fixing backtests, independent validation, and a research-only CLI. All behavior is driven by `configs/forex_edge_research.yaml`; the package does not import ASE, Engine A, broker, execution, risk, or production configuration modules.

**Tech Stack:** Python 3.11-3.14, pandas, NumPy, SciPy, PyArrow/Parquet, requests, PyYAML, zoneinfo, pytest.

---

## Ground Truth And Boundaries

- Approved design: `docs/superpowers/specs/2026-06-15-forex-edge-research-design.md`.
- Evidence study: `docs/FOREX_EDGE_RESEARCH_STUDY_2026-06-14.md`.
- Current branch contains unrelated user changes. Stage only files named by each task.
- Universe: 21 current Athena forex pairs, copied into a frozen research module.
- Daily target: `2006-01-01` onward.
- M5 bid/ask target: `2015-01-01` onward.
- Initial fixing backtest: EURUSD, GBPUSD, USDJPY.
- Dukascopy data is imported from user-exported files; no undocumented downloader.
- Carry is a policy-rate proxy and remains non-promotable.
- BIS REER remains revision-risk flagged because no historical-vintage contract is verified.

Do not modify:

```text
config.yaml
config.py
ase_cli.py
athena_ase/
scoring.py
factor_scoring.py
forex_scoring.py
engine_c.py
execution.py
auto_trader.py
risk_engine.py
guardian.py
mt5_executor.py
bybit_executor.py
```

The only permitted `athena_ase` reference is in the repository contract test
that compares the frozen research universe to the current ASE universe.

## File Structure

```text
configs/forex_edge_research.yaml
forex_edge_cli.py
athena_research/forex_edge/
  __init__.py
  config.py
  models.py
  universe.py
  store.py
  quality.py
  reporting.py
  validation.py
  runner.py
  sources/{__init__,common,bis,cftc,fred,dukascopy}.py
  portfolio/{__init__,signals,construction,costs,backtest}.py
  fixing/{__init__,calendar,windows,costs,backtest}.py
tests/test_forex_edge_research.py
```

---

## Task 1: Core Models, Frozen Universe, And Dedicated Configuration

**Files:**
- Create: `athena_research/forex_edge/__init__.py`
- Create: `athena_research/forex_edge/models.py`
- Create: `athena_research/forex_edge/universe.py`
- Create: `athena_research/forex_edge/config.py`
- Create: `configs/forex_edge_research.yaml`
- Create: `tests/test_forex_edge_research.py`

- [ ] **Step 1: Write the failing foundation tests**

Create `tests/test_forex_edge_research.py`:

```python
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_forex_edge_package_has_no_forbidden_imports() -> None:
    package = ROOT / "athena_research" / "forex_edge"
    forbidden = {
        "athena", "athena_ase", "scoring", "factor_scoring",
        "forex_scoring", "execution", "auto_trader", "risk_engine",
        "guardian", "mt5_executor", "bybit_executor",
    }
    imported: set[str] = set()
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(forbidden)


def test_frozen_universe_matches_current_ase_contract() -> None:
    from athena_ase.universe import instruments_for_family
    from athena_research.forex_edge.universe import FOREX_PAIRS

    current = tuple(item.symbol for item in instruments_for_family("forex"))
    assert FOREX_PAIRS == current
    assert len(FOREX_PAIRS) == 21


def test_quote_orientation_normalizes_currency_appreciation() -> None:
    from athena_research.forex_edge.universe import currency_usd_price

    assert currency_usd_price("EUR", 1.10) == pytest.approx(1.10)
    assert currency_usd_price("JPY", 150.0) == pytest.approx(1 / 150.0)
    assert currency_usd_price("CAD", 1.35) == pytest.approx(1 / 1.35)


def test_dedicated_config_is_frozen_and_research_only() -> None:
    from athena_research.forex_edge.config import load_config

    cfg = load_config(ROOT / "configs" / "forex_edge_research.yaml")
    assert cfg["portfolio"]["development_end"] == "2018-12-31"
    assert cfg["fixing"]["development_end"] == "2020-12-31"
    assert cfg["portfolio"]["top_n"] == 4
    assert cfg["portfolio"]["min_currencies"] == 12
    assert cfg["production_eligible"] is False


def test_config_redaction_removes_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    from athena_research.forex_edge.config import redact_secrets

    monkeypatch.setenv("FRED_API_KEY", "fred-secret-value")
    payload = {
        "url": "https://api.test/data?api_key=fred-secret-value",
        "api_key": "fred-secret-value",
        "nested": {"Authorization": "Bearer fred-secret-value"},
    }
    redacted = redact_secrets(payload)
    assert "fred-secret-value" not in repr(redacted)
    assert redacted["api_key"] == "[REDACTED]"
```

- [ ] **Step 2: Run RED**

```powershell
py -m pytest tests/test_forex_edge_research.py -q
```

Expected: `ModuleNotFoundError: athena_research.forex_edge`.

- [ ] **Step 3: Implement the typed contracts**

Create `athena_research/forex_edge/__init__.py`:

```python
"""Standalone, read-only forex edge research package."""
```

Create `athena_research/forex_edge/models.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ForexEdgeError(RuntimeError):
    """Base error for the standalone research package."""


class BlockedDataError(ForexEdgeError):
    """Required evidence is missing or fails a pre-registered quality gate."""


class InvalidResearchInputError(ForexEdgeError, ValueError):
    """Configuration or source schema is invalid."""


class StudyStatus(str, Enum):
    BLOCKED_DATA = "BLOCKED_DATA"
    COMPLETED_NO_EDGE = "COMPLETED_NO_EDGE"
    RESEARCH_CANDIDATE = "RESEARCH_CANDIDATE"


class EligibilityStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    BLOCKED_DATA = "BLOCKED_DATA"


class ReasonCode(str, Enum):
    MISSING_SERIES = "MISSING_SERIES"
    MISSING_PAIR = "MISSING_PAIR"
    MISSING_CURRENCY = "MISSING_CURRENCY"
    MISSING_COT_MAPPING = "MISSING_COT_MAPPING"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INSUFFICIENT_UNIVERSE_BREADTH = "INSUFFICIENT_UNIVERSE_BREADTH"
    STALE_DATA = "STALE_DATA"
    UNVERIFIED_AVAILABILITY = "UNVERIFIED_AVAILABILITY"
    AMBIGUOUS_UNIT = "AMBIGUOUS_UNIT"
    AMBIGUOUS_TIMEZONE = "AMBIGUOUS_TIMEZONE"
    DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
    NONPOSITIVE_PRICE = "NONPOSITIVE_PRICE"
    CROSSED_QUOTE = "CROSSED_QUOTE"
    MIDPOINT_ONLY = "MIDPOINT_ONLY"
    EXCESSIVE_GAPS = "EXCESSIVE_GAPS"
    NO_EXECUTABLE_QUOTE = "NO_EXECUTABLE_QUOTE"
    PROXY_CARRY_ONLY = "PROXY_CARRY_ONLY"
    UNVERIFIED_REVISION_HISTORY = "UNVERIFIED_REVISION_HISTORY"
    PROXY_TRANSACTION_COSTS = "PROXY_TRANSACTION_COSTS"
    PBO_UNAVAILABLE = "PBO_UNAVAILABLE"
    PINNED_MANIFEST_REQUIRED = "PINNED_MANIFEST_REQUIRED"
    UNREGISTERED_QUALITY_LIMIT = "UNREGISTERED_QUALITY_LIMIT"


class EvidenceFlag(str, Enum):
    NON_PROMOTABLE_PROXY_CARRY = "NON_PROMOTABLE_PROXY_CARRY"
    NON_PROMOTABLE_REVISION_RISK = "NON_PROMOTABLE_REVISION_RISK"
    PROXY_TRANSACTION_COSTS = "PROXY_TRANSACTION_COSTS"
    PBO_UNAVAILABLE = "PBO_UNAVAILABLE"


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    status: EligibilityStatus
    reason_codes: tuple[ReasonCode, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "status": self.status.value,
            "reason_codes": [code.value for code in self.reason_codes],
            "details": self.details,
        }


@dataclass(frozen=True)
class DatasetManifest:
    schema_version: int
    dataset: str
    key: str
    version: str
    source: str
    source_url: str
    retrieved_at: str
    actual_start: str | None
    actual_end: str | None
    row_count: int
    raw_hashes: tuple[str, ...]
    partition_hashes: dict[str, str]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrialRecord:
    trial_id: str
    study: str
    configuration: str
    cost_multiplier: float
    returns_hash: str
    n_observations: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StudyResult:
    study_status: StudyStatus
    production_eligible: bool
    evidence_flags: tuple[EvidenceFlag, ...]
    metrics: dict[str, Any]
    eligibility: EligibilityResult
    trials: tuple[TrialRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_status": self.study_status.value,
            "production_eligible": self.production_eligible,
            "evidence_flags": [flag.value for flag in self.evidence_flags],
            "metrics": self.metrics,
            "eligibility": self.eligibility.to_dict(),
            "trials": [trial.to_dict() for trial in self.trials],
        }
```

- [ ] **Step 4: Implement the frozen universe**

Create `athena_research/forex_edge/universe.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


FOREX_PAIRS: tuple[str, ...] = (
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "AUDCHF", "AUDNZD",
    "NZDUSD", "EURGBP", "USDCAD", "USDCHF", "EURJPY", "GBPJPY",
    "AUDJPY", "EURAUD", "GBPAUD", "USDZAR", "EURCHF", "USDMXN",
    "USDSGD", "USDBRL", "USDINR",
)

CURRENCIES: tuple[str, ...] = (
    "USD", "EUR", "GBP", "JPY", "AUD", "CHF", "NZD",
    "CAD", "ZAR", "MXN", "SGD", "BRL", "INR",
)


@dataclass(frozen=True)
class CanonicalPair:
    pair: str
    currency: str
    usd_per_currency: bool


CANONICAL_USD_PAIRS: dict[str, CanonicalPair] = {
    "EUR": CanonicalPair("EURUSD", "EUR", True),
    "GBP": CanonicalPair("GBPUSD", "GBP", True),
    "JPY": CanonicalPair("USDJPY", "JPY", False),
    "AUD": CanonicalPair("AUDUSD", "AUD", True),
    "CHF": CanonicalPair("USDCHF", "CHF", False),
    "NZD": CanonicalPair("NZDUSD", "NZD", True),
    "CAD": CanonicalPair("USDCAD", "CAD", False),
    "ZAR": CanonicalPair("USDZAR", "ZAR", False),
    "MXN": CanonicalPair("USDMXN", "MXN", False),
    "SGD": CanonicalPair("USDSGD", "SGD", False),
    "BRL": CanonicalPair("USDBRL", "BRL", False),
    "INR": CanonicalPair("USDINR", "INR", False),
}


def currency_usd_price(currency: str, pair_value: float) -> float:
    ccy = currency.upper()
    if ccy == "USD":
        return 1.0
    value = float(pair_value)
    if value <= 0:
        raise ValueError("pair_value must be positive")
    spec = CANONICAL_USD_PAIRS[ccy]
    return value if spec.usd_per_currency else 1.0 / value


def pair_weight_for_currency(currency: str, currency_weight: float) -> float:
    spec = CANONICAL_USD_PAIRS[currency.upper()]
    return float(currency_weight) if spec.usd_per_currency else -float(currency_weight)
```

- [ ] **Step 5: Add the dedicated YAML and loader**

Create `configs/forex_edge_research.yaml` with the exact values from design
sections 4, 5, 8, 9, 10, and 11. The source mappings must be:

```yaml
schema_version: 1
production_eligible: false
universe:
  pairs: [EURUSD, GBPUSD, USDJPY, AUDUSD, AUDCHF, AUDNZD, NZDUSD, EURGBP, USDCAD, USDCHF, EURJPY, GBPJPY, AUDJPY, EURAUD, GBPAUD, USDZAR, EURCHF, USDMXN, USDSGD, USDBRL, USDINR]
sources:
  bis:
    base_url: https://stats.bis.org/api/v1/data/WS_EER_M
    reer_series:
      USD: M.R.B.US
      EUR: M.R.B.XM
      GBP: M.R.B.GB
      JPY: M.R.B.JP
      AUD: M.R.B.AU
      CHF: M.R.B.CH
      NZD: M.R.B.NZ
      CAD: M.R.B.CA
      ZAR: M.R.B.ZA
      MXN: M.R.B.MX
      SGD: M.R.B.SG
      BRL: M.R.B.BR
      INR: M.R.B.IN
  cftc:
    historical_url: https://www.cftc.gov/files/dea/history/deacot{year}.zip
    mappings:
      EUR: EURO FX
      GBP: BRITISH POUND
      JPY: JAPANESE YEN
      AUD: AUSTRALIAN DOLLAR
      CHF: SWISS FRANC
      NZD: NEW ZEALAND DOLLAR
      CAD: CANADIAN DOLLAR
      MXN: MEXICAN PESO
  fred:
    api_base: https://api.stlouisfed.org/fred
    api_key_env: FRED_API_KEY
    spot_series:
      EUR: {series_id: DEXUSEU, usd_per_currency: true}
      GBP: {series_id: DEXUSUK, usd_per_currency: true}
      JPY: {series_id: DEXJPUS, usd_per_currency: false}
      AUD: {series_id: DEXUSAL, usd_per_currency: true}
      CHF: {series_id: DEXSZUS, usd_per_currency: false}
      NZD: {series_id: DEXUSNZ, usd_per_currency: true}
      CAD: {series_id: DEXCAUS, usd_per_currency: false}
      ZAR: {series_id: DEXSFUS, usd_per_currency: false}
      MXN: {series_id: DEXMXUS, usd_per_currency: false}
      SGD: {series_id: DEXSIUS, usd_per_currency: false}
      BRL: {series_id: DEXBZUS, usd_per_currency: false}
      INR: {series_id: DEXINUS, usd_per_currency: false}
    rate_series:
      USD: DFF
      EUR: ECBDFR
      GBP: IRSTCI01GBM156N
      JPY: IRSTCI01JPM156N
      AUD: IRSTCI01AUM156N
      CHF: IRSTCI01CHM156N
      NZD: IRSTCI01NZM156N
      CAD: IRSTCI01CAM156N
      ZAR: IRSTCI01ZAM156N
      MXN: IRSTCI01MXM156N
      BRL: IRSTCI01BRM156N
      INR: IRSTCI01INM156N
portfolio:
  target_start: "2006-01-01"
  development_end: "2018-12-31"
  holdout_start: "2019-01-01"
  min_currencies: 12
  top_n: 4
  momentum_lookback_months: 12
  momentum_skip_months: 1
  value_lookback_months: 60
  vol_lookback_days: 63
  annual_vol_target: 0.10
  initial_gross: 1.0
  max_gross: 2.0
  max_currency_weight: 0.25
  cost_multipliers: [1.0, 1.5, 2.0]
  round_trip_bps: {major: 1.8, other: 4.1}
  major_pairs: [EURUSD, GBPUSD, USDJPY, AUDUSD, NZDUSD, USDCAD, USDCHF]
fixing:
  target_start: "2015-01-01"
  development_end: "2020-12-31"
  holdout_start: "2021-01-01"
  pairs: [EURUSD, GBPUSD, USDJPY]
  commission_round_trip_bps: 0.6
  cost_multipliers: [1.0, 1.5, 2.0]
  anchors:
    london: {timezone: Europe/London, hour: 16, minute: 0}
    tokyo: {timezone: Asia/Tokyo, hour: 9, minute: 55}
  windows:
    pre_continuation:
      observation_start_minutes: -30
      signal_minutes: -15
      entry_next_bar_minutes: 5
      exit_minutes: 0
    post_reversal:
      observation_start_minutes: -15
      signal_minutes: 0
      entry_next_bar_minutes: 5
      exit_minutes: 30
quality:
  # These are required pre-registration inputs, not strategy parameters.
  # The approved design did not assign evidence-backed numeric values. Keep
  # them null and fail empirical runs closed until a reviewed config revision
  # supplies values before holdout results are inspected.
  spot_staleness_days: null
  rate_staleness_days: null
  reer_staleness_days: null
  m5_max_spread_bps: null
validation:
  bootstrap_block: 10
  bootstrap_resamples: 5000
  bootstrap_seed: 42
  cscv_partitions: 16
  dsr_z_min: 1.645
  pbo_max: 0.50
  concentration_max: 0.40
  min_positive_holdout_years: 3
```

Create `athena_research/forex_edge/config.py` with:

```python
from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml

from athena_research.forex_edge.universe import FOREX_PAIRS


SECRET_KEYS = {"api_key", "apikey", "authorization", "token", "secret", "password"}


def default_store_root() -> Path:
    override = os.environ.get("ATHENA_FOREX_EDGE_ROOT", "").strip()
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        local = str(Path.home() / "AppData" / "Local")
    return Path(local) / "Athena" / "research" / "forex_edge"


def redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if str(key).lower() in SECRET_KEYS
                else redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, str):
        parts = urlsplit(value)
        if parts.query:
            query = [
                (key, "[REDACTED]" if key.lower() in SECRET_KEYS else item)
                for key, item in parse_qsl(parts.query, keep_blank_values=True)
            ]
            value = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        for env_name in ("FRED_API_KEY",):
            secret = os.environ.get(env_name, "")
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    return value


def load_config(path: str | Path) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if int(raw.get("schema_version", 0)) != 1:
        raise ValueError("forex edge config schema_version must be 1")
    if tuple(raw.get("universe", {}).get("pairs", ())) != FOREX_PAIRS:
        raise ValueError("configured forex universe does not match frozen universe")
    if raw.get("production_eligible") is not False:
        raise ValueError("production_eligible must remain false")
    if int(raw["portfolio"]["min_currencies"]) < 12:
        raise ValueError("portfolio min_currencies must be at least 12")
    if int(raw["portfolio"]["top_n"]) != 4:
        raise ValueError("portfolio top_n must be 4")
    return deepcopy(raw)
```

`load_config()` may load null quality caps so fixture ingestion and parser
tests can run. Add `validate_empirical_config(config, lane)` and call it before
`quality-report`, `run-portfolio`, `run-fixing`, or `run-both`. Portfolio
requires spot, rate, and REER staleness values; fixing requires the M5 spread
cap; quality-report and run-both require all four. It raises
`BlockedDataError("UNREGISTERED_QUALITY_LIMIT")` if a required value is null,
non-finite, or nonpositive. Synthetic tests pass an in-memory config copy with
explicit fixture-local limits. Those values must not be written back to the
repository or described as empirically justified.

- [ ] **Step 6: Run GREEN**

```powershell
py -m pytest tests/test_forex_edge_research.py -q
```

Expected: 5 passed.

- [ ] **Step 7: Commit**

```powershell
git add -- configs/forex_edge_research.yaml athena_research/forex_edge/__init__.py athena_research/forex_edge/models.py athena_research/forex_edge/universe.py athena_research/forex_edge/config.py tests/test_forex_edge_research.py
git commit -m "feat(forex-edge): add isolated research foundations"
```

---

## Task 2: Immutable Raw, Normalized, Manifest, And Run Storage

**Files:**
- Create: `athena_research/forex_edge/store.py`
- Modify: `tests/test_forex_edge_research.py`

- [ ] **Step 1: Append failing storage tests**

```python
def test_store_versions_data_without_overwrite(tmp_path: Path) -> None:
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)
    raw = store.write_raw("FRED", "spot_EUR", b'{"value":1.1}')
    frame = pd.DataFrame({
        "timestamp": pd.to_datetime(["2020-01-02", "2020-01-03"], utc=True),
        "value": [1.1, 1.2],
    })
    first = store.write_normalized(
        dataset="spot", key="EUR", frame=frame, source="FRED",
        source_url="https://example.test/fred", raw_hashes=(raw.sha256,),
        metadata={"unit": "USD_PER_CURRENCY", "config_hash": "cfg"},
    )
    same = store.write_normalized(
        dataset="spot", key="EUR", frame=frame, source="FRED",
        source_url="https://example.test/fred", raw_hashes=(raw.sha256,),
        metadata={"unit": "USD_PER_CURRENCY", "config_hash": "cfg"},
    )
    assert first.version == same.version
    pd.testing.assert_frame_equal(
        store.load_normalized("spot", "EUR", first.version),
        frame,
    )
    changed = frame.copy()
    changed.loc[1, "value"] = 1.3
    second = store.write_normalized(
        dataset="spot", key="EUR", frame=changed, source="FRED",
        source_url="https://example.test/fred", raw_hashes=(raw.sha256,),
        metadata={"unit": "USD_PER_CURRENCY", "config_hash": "cfg"},
    )
    assert second.version != first.version


def test_store_rejects_conflicting_existing_partition(tmp_path: Path) -> None:
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)
    path = store.root / "normalized" / "spot" / "EUR" / "bad" / "2020" / "data.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not parquet")
    with pytest.raises(RuntimeError, match="immutable partition conflict"):
        store._write_partition(path, pd.DataFrame({"x": [1]}))
```

- [ ] **Step 2: Run RED**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_store_versions_data_without_overwrite tests/test_forex_edge_research.py::test_store_rejects_conflicting_existing_partition -q
```

Expected: missing `store`.

- [ ] **Step 3: Implement storage**

Create `store.py` with:

```python
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from athena_research.forex_edge.config import default_store_root
from athena_research.forex_edge.models import DatasetManifest
from athena_research.reproducibility import hash_stable_json


@dataclass(frozen=True)
class RawArtifact:
    path: Path
    sha256: str
    retrieval_id: str


def canonical_frame_hash(frame: pd.DataFrame) -> str:
    work = frame.copy().reindex(sorted(frame.columns), axis=1)
    for column in work.columns:
        if pd.api.types.is_datetime64_any_dtype(work[column]):
            work[column] = pd.to_datetime(work[column], utc=True).map(
                lambda value: value.isoformat()
            )
    return hash_stable_json(work.where(pd.notna(work), None).to_dict("records"))


class ResearchStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_store_root()
        self.root.mkdir(parents=True, exist_ok=True)

    def write_raw(self, source: str, dataset: str, content: bytes) -> RawArtifact:
        digest = hashlib.sha256(content).hexdigest()
        retrieval_id = digest[:16]
        path = self.root / "raw" / source / dataset / retrieval_id / "response.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != content:
            raise RuntimeError(f"immutable raw conflict: {path}")
        if not path.exists():
            path.write_bytes(content)
        return RawArtifact(path, digest, retrieval_id)

    def _write_partition(self, path: Path, frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                existing = pd.read_parquet(path)
            except Exception as exc:
                raise RuntimeError(f"immutable partition conflict: {path}") from exc
            if canonical_frame_hash(existing) != canonical_frame_hash(frame):
                raise RuntimeError(f"immutable partition conflict: {path}")
            return
        temp = path.with_suffix(".tmp.parquet")
        frame.to_parquet(temp, index=False)
        os.replace(temp, path)

    def write_normalized(
        self, *, dataset: str, key: str, frame: pd.DataFrame,
        source: str, source_url: str, raw_hashes: tuple[str, ...],
        metadata: dict[str, Any],
    ) -> DatasetManifest:
        frame_hash = canonical_frame_hash(frame)
        version = hash_stable_json({
            "dataset": dataset, "key": key, "frame_hash": frame_hash,
            "raw_hashes": raw_hashes, "config_hash": metadata["config_hash"],
        })[:16]
        base = self.root / "normalized" / dataset / key / version
        work = frame.copy()
        partition_hashes: dict[str, str] = {}
        if work.empty:
            self._write_partition(base / "empty" / "data.parquet", work)
            partition_hashes["empty"] = frame_hash
            actual_start = actual_end = None
        else:
            work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
            actual_start = work["timestamp"].min().isoformat()
            actual_end = work["timestamp"].max().isoformat()
            work["_year"] = work["timestamp"].dt.year
            for year, chunk in work.groupby("_year", sort=True):
                clean = chunk.drop(columns="_year").reset_index(drop=True)
                self._write_partition(base / str(int(year)) / "data.parquet", clean)
                partition_hashes[str(int(year))] = canonical_frame_hash(clean)
        manifest_path = self.root / "manifests" / dataset / key / f"{version}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if manifest_path.exists():
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            return DatasetManifest(**payload)
        manifest = DatasetManifest(
            schema_version=1, dataset=dataset, key=key, version=version,
            source=source, source_url=source_url,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            actual_start=actual_start, actual_end=actual_end,
            row_count=len(frame), raw_hashes=raw_hashes,
            partition_hashes=partition_hashes, metadata=metadata,
        )
        manifest_path.write_text(
            json.dumps(manifest.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return manifest

    def load_normalized(self, dataset: str, key: str, version: str) -> pd.DataFrame:
        paths = sorted((self.root / "normalized" / dataset / key / version).glob("*/data.parquet"))
        if not paths:
            raise FileNotFoundError(f"{dataset}/{key}/{version}")
        return pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)

    def run_dir(self, run_id: str) -> Path:
        path = self.root / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path
```

- [ ] **Step 4: Run GREEN**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_store_versions_data_without_overwrite tests/test_forex_edge_research.py::test_store_rejects_conflicting_existing_partition -q
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```powershell
git add -- athena_research/forex_edge/store.py tests/test_forex_edge_research.py
git commit -m "feat(forex-edge): add immutable research store"
```

---

## Task 3: Shared HTTP And Fail-Closed Quality Contracts

**Files:**
- Create: `athena_research/forex_edge/sources/__init__.py`
- Create: `athena_research/forex_edge/sources/common.py`
- Create: `athena_research/forex_edge/quality.py`
- Modify: `tests/test_forex_edge_research.py`

- [ ] **Step 1: Append failing quality tests**

```python
def test_macro_asof_rejects_future_and_unverified_rows() -> None:
    from athena_research.forex_edge.quality import macro_asof

    rows = pd.DataFrame({
        "timestamp": pd.to_datetime(["2020-01-01", "2020-02-01"], utc=True),
        "available_time": pd.to_datetime(["2020-01-15", "2020-03-15"], utc=True),
        "value": [1.0, 2.0],
        "availability_verified": [True, True],
    })
    row = macro_asof(rows, pd.Timestamp("2020-02-15", tz="UTC"))
    assert row["value"] == 1.0
    with pytest.raises(ValueError, match="UNVERIFIED_AVAILABILITY"):
        macro_asof(
            rows.assign(availability_verified=False),
            pd.Timestamp("2020-02-15", tz="UTC"),
        )


def test_bid_ask_quality_rejects_crossed_and_conflicting_duplicates() -> None:
    from athena_research.forex_edge.models import ReasonCode
    from athena_research.forex_edge.quality import validate_bid_ask_bars

    bars = pd.DataFrame({
        "bar_end": pd.to_datetime(["2021-01-04 16:00Z", "2021-01-04 16:00Z"]),
        "bid_open": [1.2, 1.3], "bid_high": [1.2, 1.3],
        "bid_low": [1.2, 1.3], "bid_close": [1.2, 1.3],
        "ask_open": [1.1, 1.4], "ask_high": [1.1, 1.4],
        "ask_low": [1.1, 1.4], "ask_close": [1.1, 1.4],
    })
    result = validate_bid_ask_bars(bars)
    assert result.eligible is False
    assert ReasonCode.CROSSED_QUOTE in result.reason_codes
    assert ReasonCode.DUPLICATE_CONFLICT in result.reason_codes
```

- [ ] **Step 2: Run RED**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_macro_asof_rejects_future_and_unverified_rows tests/test_forex_edge_research.py::test_bid_ask_quality_rejects_crossed_and_conflicting_duplicates -q
```

Expected: missing `quality`.

- [ ] **Step 3: Implement injectable read-only HTTP**

Create `sources/__init__.py`:

```python
"""Read-only provider adapters for forex edge research."""
```

Create `sources/common.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

import requests


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    content: bytes
    headers: Mapping[str, str]
    url: str

    def raise_for_status(self) -> None:
        if not 200 <= self.status_code < 300:
            raise RuntimeError(f"provider HTTP {self.status_code}")


class HttpGet(Protocol):
    def __call__(
        self, url: str, *, params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None, timeout: float = 30.0,
    ) -> HttpResponse: ...


def requests_get(
    url: str, *, params: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None, timeout: float = 30.0,
) -> HttpResponse:
    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    return HttpResponse(
        response.status_code, response.content, dict(response.headers), response.url
    )
```

- [ ] **Step 4: Implement quality checks**

Create `quality.py`:

```python
from __future__ import annotations

import pandas as pd

from athena_research.forex_edge.models import (
    EligibilityResult, EligibilityStatus, ReasonCode,
)


def macro_asof(frame: pd.DataFrame, decision_time: pd.Timestamp) -> pd.Series:
    required = {"available_time", "value", "availability_verified"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"MISSING_SERIES:{sorted(missing)}")
    if not bool(frame["availability_verified"].all()):
        raise ValueError(ReasonCode.UNVERIFIED_AVAILABILITY.value)
    decision = pd.Timestamp(decision_time)
    decision = decision.tz_localize("UTC") if decision.tzinfo is None else decision.tz_convert("UTC")
    eligible = frame[pd.to_datetime(frame["available_time"], utc=True) <= decision]
    if eligible.empty:
        raise ValueError(ReasonCode.MISSING_SERIES.value)
    return eligible.sort_values("available_time", kind="stable").iloc[-1]


def validate_bid_ask_bars(frame: pd.DataFrame) -> EligibilityResult:
    required = {
        "timestamp", "bid_open", "bid_high", "bid_low", "bid_close",
        "ask_open", "ask_high", "ask_low", "ask_close",
    }
    missing = required.difference(frame.columns)
    if missing:
        return EligibilityResult(
            False, EligibilityStatus.INELIGIBLE,
            (ReasonCode.MIDPOINT_ONLY,), {"missing_columns": sorted(missing)},
        )
    price_columns = sorted(required - {"timestamp"})
    numeric = frame[price_columns].apply(pd.to_numeric, errors="coerce")
    reasons: list[ReasonCode] = []
    details: dict[str, object] = {}
    if numeric.isna().any().any() or (numeric <= 0).any().any():
        reasons.append(ReasonCode.NONPOSITIVE_PRICE)
    crossed = (
        (numeric["ask_open"] < numeric["bid_open"])
        | (numeric["ask_high"] < numeric["bid_high"])
        | (numeric["ask_low"] < numeric["bid_low"])
        | (numeric["ask_close"] < numeric["bid_close"])
    )
    if crossed.any():
        reasons.append(ReasonCode.CROSSED_QUOTE)
        details["crossed_rows"] = int(crossed.sum())
    work = frame.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
    duplicated = work[work.duplicated("timestamp", keep=False)]
    conflicts = sum(
        len(group[price_columns].drop_duplicates()) > 1
        for _, group in duplicated.groupby("timestamp")
    )
    if conflicts:
        reasons.append(ReasonCode.DUPLICATE_CONFLICT)
        details["duplicate_conflicts"] = int(conflicts)
    ordered = tuple(dict.fromkeys(reasons))
    return EligibilityResult(
        not ordered,
        EligibilityStatus.ELIGIBLE if not ordered else EligibilityStatus.INELIGIBLE,
        ordered,
        details,
    )
```

- [ ] **Step 5: Run GREEN and commit**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_macro_asof_rejects_future_and_unverified_rows tests/test_forex_edge_research.py::test_bid_ask_quality_rejects_crossed_and_conflicting_duplicates -q
git add -- athena_research/forex_edge/sources/__init__.py athena_research/forex_edge/sources/common.py athena_research/forex_edge/quality.py tests/test_forex_edge_research.py
git commit -m "feat(forex-edge): add fail-closed data quality contracts"
```

---

## Task 4: FRED/ALFRED Spot And Rate Adapter

**Files:**
- Create: `athena_research/forex_edge/sources/fred.py`
- Modify: `tests/test_forex_edge_research.py`

- [ ] **Step 1: Append failing FRED tests**

```python
def test_fred_normalization_uses_vintage_and_explicit_units() -> None:
    from athena_research.forex_edge.sources.fred import (
        normalize_fred_observations, percent_to_decimal,
    )

    payload = {"observations": [{
        "realtime_start": "2020-01-06", "realtime_end": "2020-01-06",
        "date": "2020-01-03", "value": "150.0",
    }]}
    frame = normalize_fred_observations(
        payload, series_id="DEXJPUS", currency="JPY", kind="spot",
        unit="Japanese Yen to U.S. Dollar", usd_per_currency=False,
    )
    assert frame.loc[0, "value"] == pytest.approx(1 / 150.0)
    assert frame.loc[0, "available_time"] == pd.Timestamp(
        "2020-01-06 16:15", tz="America/New_York"
    ).tz_convert("UTC")
    assert percent_to_decimal(5.25, "Percent") == pytest.approx(0.0525)
    with pytest.raises(ValueError, match="AMBIGUOUS_UNIT"):
        percent_to_decimal(5.25, "Index")
    rate_payload = {"observations": [{
        "realtime_start": "2020-01-06", "realtime_end": "2020-01-06",
        "date": "2020-01-03", "value": "5.25",
    }]}
    rate = normalize_fred_observations(
        rate_payload, series_id="DFF", currency="USD", kind="rate",
        unit="Percent",
    )
    assert rate.loc[0, "value"] == pytest.approx(5.25)
    assert rate.loc[0, "unit"] == "Percent"
    assert bool(rate.loc[0, "availability_verified"]) is False


def test_fred_errors_redact_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from athena_research.forex_edge.sources.common import HttpResponse
    from athena_research.forex_edge.sources.fred import fetch_fred_series

    monkeypatch.setenv("FRED_API_KEY", "super-secret")
    def fake_get(url, *, params=None, headers=None, timeout=30.0):
        return HttpResponse(500, b"failure", {}, url + "?api_key=super-secret")
    with pytest.raises(RuntimeError) as exc:
        fetch_fred_series(
            "DEXUSEU", api_base="https://api.test/fred",
            api_key_env="FRED_API_KEY", http_get=fake_get,
        )
    assert "super-secret" not in str(exc.value)
```

- [ ] **Step 2: Run RED**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_fred_normalization_uses_vintage_and_explicit_units tests/test_forex_edge_research.py::test_fred_errors_redact_api_key -q
```

- [ ] **Step 3: Implement FRED**

Create `sources/fred.py`:

```python
from __future__ import annotations

import json
import os
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from athena_research.forex_edge.config import redact_secrets
from athena_research.forex_edge.models import ReasonCode
from athena_research.forex_edge.sources.common import HttpGet, requests_get


_NY = ZoneInfo("America/New_York")


def percent_to_decimal(value: float, unit: str) -> float:
    if unit.strip().lower() not in {"percent", "percent per annum"}:
        raise ValueError(ReasonCode.AMBIGUOUS_UNIT.value)
    return float(value) / 100.0


def _available_time(realtime_start: str, kind: str) -> pd.Timestamp:
    day = date.fromisoformat(realtime_start)
    release_time = time(16, 15) if kind == "spot" else time(23, 59, 59)
    return pd.Timestamp(datetime.combine(day, release_time, tzinfo=_NY)).tz_convert("UTC")


def normalize_fred_observations(
    payload: dict[str, Any], *, series_id: str, currency: str,
    kind: str, unit: str, usd_per_currency: bool | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for observation in payload.get("observations", []):
        raw = str(observation.get("value", "")).strip()
        if raw in {"", "."}:
            continue
        realtime_start = str(observation.get("realtime_start", "")).strip()
        if not realtime_start:
            raise ValueError(ReasonCode.UNVERIFIED_AVAILABILITY.value)
        raw_value = float(raw)
        if kind == "spot":
            if usd_per_currency is None or raw_value <= 0:
                raise ValueError(ReasonCode.AMBIGUOUS_UNIT.value)
            value = raw_value if usd_per_currency else 1.0 / raw_value
            normalized_unit = "USD_PER_CURRENCY"
        elif kind == "rate":
            # Preserve source units. Conversion occurs at the carry signal
            # boundary after unit and point-in-time eligibility checks.
            percent_to_decimal(raw_value, unit)
            value = raw_value
            normalized_unit = unit
        else:
            raise ValueError(f"unknown FRED kind: {kind}")
        rows.append({
            "timestamp": pd.Timestamp(observation["date"], tz="UTC"),
            "available_time": _available_time(realtime_start, kind),
            "value": value, "raw_value": raw_value,
            "series_id": series_id, "currency": currency,
            "unit": normalized_unit, "raw_unit": unit,
            "realtime_start": realtime_start,
            "realtime_end": str(observation.get("realtime_end", "")),
            # ALFRED verifies the vintage date. It does not, by itself, prove
            # a source-specific publication timestamp for every rate series.
            "availability_verified": kind == "spot",
            "availability_reason": (
                "" if kind == "spot" else ReasonCode.UNVERIFIED_AVAILABILITY.value
            ),
        })
    return pd.DataFrame(rows)


def fetch_fred_series(
    series_id: str, *, api_base: str, api_key_env: str,
    observation_start: str = "2000-01-01",
    observation_end: str | None = None,
    http_get: HttpGet = requests_get,
) -> bytes:
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"{api_key_env} not configured")
    params: dict[str, object] = {
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "output_type": 4, "observation_start": observation_start,
    }
    if observation_end:
        params["observation_end"] = observation_end
    response = None
    try:
        response = http_get(
            f"{api_base.rstrip('/')}/series/observations",
            params=params, timeout=30.0,
        )
        response.raise_for_status()
        json.loads(response.content.decode("utf-8"))
        return response.content
    except Exception as exc:
        safe = redact_secrets({
            "series_id": series_id,
            "url": response.url if response is not None else api_base,
        })
        raise RuntimeError(f"FRED request failed: {safe}") from exc
```

The later ingest runner must call `/series` for metadata and pass the returned
`units` string to the normalizer. Never infer units from values. A rate series
remains ineligible until its configuration supplies a reviewed
source-specific publication-time policy. The frozen first config supplies no
such policies, so carry-only and blended trials are registered as
`BLOCKED_DATA/UNVERIFIED_AVAILABILITY`; momentum and value trials continue
independently.

- [ ] **Step 4: Run GREEN and commit**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_fred_normalization_uses_vintage_and_explicit_units tests/test_forex_edge_research.py::test_fred_errors_redact_api_key -q
git add -- athena_research/forex_edge/sources/fred.py tests/test_forex_edge_research.py
git commit -m "feat(forex-edge): add point-in-time FRED ingestion"
```

---

## Task 5: BIS REER And CFTC COT Adapters

**Files:**
- Create: `athena_research/forex_edge/sources/bis.py`
- Create: `athena_research/forex_edge/sources/cftc.py`
- Modify: `tests/test_forex_edge_research.py`

- [ ] **Step 1: Append failing adapter tests**

```python
def test_bis_reer_applies_conservative_lag_and_revision_flag() -> None:
    from athena_research.forex_edge.sources.bis import parse_bis_reer_csv

    content = (
        "FREQ,TYPE,BASKET,REF_AREA,TIME_PERIOD,OBS_VALUE,UNIT_MEASURE\n"
        "M,R,B,GB,2020-01,101.5,IX\n"
    ).encode()
    frame = parse_bis_reer_csv(content, currency="GBP", series_key="M.R.B.GB")
    assert frame.loc[0, "timestamp"] == pd.Timestamp("2020-01-31 23:59:59.999999999", tz="UTC")
    assert frame.loc[0, "available_time"] == pd.Timestamp("2020-02-29 23:59:59", tz="UTC")
    assert bool(frame.loc[0, "revision_history_verified"]) is False


def test_cftc_applies_following_monday_and_reports_missing_mapping() -> None:
    from athena_research.forex_edge.sources.cftc import (
        missing_cot_currencies, normalize_cftc_frame,
    )

    raw = pd.DataFrame({
        "Market and Exchange Names": ["EURO FX - CHICAGO MERCANTILE EXCHANGE"],
        "As of Date in Form YYYY-MM-DD": ["2020-01-07"],
        "Noncommercial Positions-Long (All)": [100],
        "Noncommercial Positions-Short (All)": [40],
    })
    frame = normalize_cftc_frame(raw, {"EUR": "EURO FX"})
    assert frame.loc[0, "net_noncommercial"] == 60
    assert frame.loc[0, "available_time"] == pd.Timestamp("2020-01-13", tz="UTC")
    assert missing_cot_currencies(("EUR", "SGD"), {"EUR": "EURO FX"}) == ("SGD",)
```

- [ ] **Step 2: Run RED**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_bis_reer_applies_conservative_lag_and_revision_flag tests/test_forex_edge_research.py::test_cftc_applies_following_monday_and_reports_missing_mapping -q
```

- [ ] **Step 3: Implement BIS**

Create `sources/bis.py`:

```python
from __future__ import annotations

from io import BytesIO

import pandas as pd

from athena_research.forex_edge.models import ReasonCode
from athena_research.forex_edge.sources.common import HttpGet, requests_get


def build_bis_url(base_url: str, series_key: str, start: str, end: str = "") -> str:
    query = f"?startPeriod={start}&detail=full"
    if end:
        query += f"&endPeriod={end}"
    return f"{base_url.rstrip('/')}/{series_key}/all{query}"


def parse_bis_reer_csv(content: bytes, *, currency: str, series_key: str) -> pd.DataFrame:
    frame = pd.read_csv(BytesIO(content))
    required = {"TIME_PERIOD", "OBS_VALUE", "UNIT_MEASURE"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"MISSING_SERIES:{sorted(missing)}")
    values = pd.to_numeric(frame["OBS_VALUE"], errors="coerce")
    if values.isna().any():
        raise ValueError(ReasonCode.AMBIGUOUS_UNIT.value)
    raw_units = tuple(sorted(frame["UNIT_MEASURE"].dropna().astype(str).unique()))
    if raw_units != ("IX",):
        raise ValueError(ReasonCode.AMBIGUOUS_UNIT.value)
    periods = pd.PeriodIndex(frame["TIME_PERIOD"].astype(str), freq="M")
    timestamp = periods.to_timestamp(how="end").tz_localize("UTC")
    available = (
        (periods + 1).to_timestamp(how="end").tz_localize("UTC").normalize()
        + pd.Timedelta(hours=23, minutes=59, seconds=59)
    )
    return pd.DataFrame({
        "timestamp": timestamp, "available_time": available,
        "value": values.astype(float), "currency": currency,
        "series_id": series_key, "unit": "INDEX", "raw_unit": "IX",
        "availability_verified": True,
        "revision_history_verified": False,
    })


def fetch_bis_reer(
    base_url: str, series_key: str, *, start: str, end: str = "",
    http_get: HttpGet = requests_get,
) -> tuple[str, bytes]:
    url = build_bis_url(base_url, series_key, start, end)
    response = http_get(url, headers={"Accept": "text/csv"}, timeout=30.0)
    response.raise_for_status()
    return url, response.content
```

- [ ] **Step 4: Implement CFTC**

Create `sources/cftc.py`:

```python
from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pandas as pd

from athena_research.forex_edge.sources.common import HttpGet, requests_get


MARKET = "Market and Exchange Names"
REPORT_DATE = "As of Date in Form YYYY-MM-DD"
LONG = "Noncommercial Positions-Long (All)"
SHORT = "Noncommercial Positions-Short (All)"


def following_monday(report_date: pd.Timestamp) -> pd.Timestamp:
    report = pd.Timestamp(report_date)
    report = report.tz_localize("UTC") if report.tzinfo is None else report.tz_convert("UTC")
    days = (7 - report.weekday()) % 7 or 7
    return report.normalize() + pd.Timedelta(days=days)


def normalize_cftc_frame(raw: pd.DataFrame, mappings: dict[str, str]) -> pd.DataFrame:
    required = {MARKET, REPORT_DATE, LONG, SHORT}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"MISSING_SERIES:{sorted(missing)}")
    rows: list[dict[str, object]] = []
    market_text = raw[MARKET].astype(str).str.upper()
    for currency, prefix in mappings.items():
        for _, row in raw[market_text.str.startswith(prefix.upper())].iterrows():
            report = pd.Timestamp(row[REPORT_DATE], tz="UTC")
            long_value = float(row[LONG])
            short_value = float(row[SHORT])
            rows.append({
                "timestamp": report, "available_time": following_monday(report),
                "currency": currency,
                "net_noncommercial": long_value - short_value,
                "long_noncommercial": long_value,
                "short_noncommercial": short_value,
                "availability_verified": True,
            })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    conflicts = frame[frame.duplicated(["currency", "timestamp"], keep=False)]
    if not conflicts.empty and any(
        len(group.drop_duplicates()) > 1
        for _, group in conflicts.groupby(["currency", "timestamp"])
    ):
        raise ValueError("DUPLICATE_CONFLICT")
    return frame.drop_duplicates(["currency", "timestamp"]).sort_values(
        ["currency", "timestamp"]
    ).reset_index(drop=True)


def missing_cot_currencies(
    currencies: tuple[str, ...], mappings: dict[str, str],
) -> tuple[str, ...]:
    return tuple(currency for currency in currencies if currency not in mappings)


def parse_cftc_zip(content: bytes) -> pd.DataFrame:
    with ZipFile(BytesIO(content)) as archive:
        names = sorted(
            name for name in archive.namelist()
            if name.lower().endswith((".csv", ".txt"))
        )
        if len(names) != 1:
            raise ValueError("CFTC archive must contain exactly one data file")
        with archive.open(names[0]) as handle:
            return pd.read_csv(handle, low_memory=False)


def fetch_cftc_year(
    url_template: str, year: int, *, http_get: HttpGet = requests_get,
) -> tuple[str, bytes]:
    url = url_template.format(year=int(year))
    response = http_get(url, timeout=60.0)
    response.raise_for_status()
    return url, response.content
```

- [ ] **Step 5: Run GREEN and commit**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_bis_reer_applies_conservative_lag_and_revision_flag tests/test_forex_edge_research.py::test_cftc_applies_following_monday_and_reports_missing_mapping -q
git add -- athena_research/forex_edge/sources/bis.py athena_research/forex_edge/sources/cftc.py tests/test_forex_edge_research.py
git commit -m "feat(forex-edge): add BIS and CFTC ingestion"
```

---

## Task 6: Strict Dukascopy Bid/Ask Import

**Files:**
- Create: `athena_research/forex_edge/sources/dukascopy.py`
- Modify: `tests/test_forex_edge_research.py`

- [ ] **Step 1: Add failing importer tests**

Append:

```python
def test_dukascopy_ticks_resample_to_executable_m5_bars(tmp_path: Path) -> None:
    from athena_research.forex_edge.sources.dukascopy import import_dukascopy

    source = tmp_path / "EURUSD_ticks.csv"
    source.write_text(
        "time,bid,ask\n"
        "2021-01-04 15:30:01,1.2000,1.2002\n"
        "2021-01-04 15:34:59,1.2004,1.2006\n"
        "2021-01-04 15:35:01,1.2003,1.2005\n",
        encoding="utf-8",
    )
    bars = import_dukascopy(
        source,
        symbol="EURUSD",
        timezone_name="UTC",
        schema="tick_bid_ask",
    )
    first = bars.iloc[0]
    assert first["timestamp"] == pd.Timestamp("2021-01-04 15:35:00Z")
    assert first["bid_open"] == pytest.approx(1.2000)
    assert first["bid_close"] == pytest.approx(1.2004)
    assert first["ask_open"] == pytest.approx(1.2002)
    assert first["ask_close"] == pytest.approx(1.2006)
    assert (bars["ask_low"] >= bars["bid_low"]).all()


@pytest.mark.parametrize(
    ("schema", "timezone_name", "expected"),
    [
        ("midpoint_m5", "UTC", "MIDPOINT_ONLY"),
        ("tick_bid_ask", "", "AMBIGUOUS_TIMEZONE"),
    ],
)
def test_dukascopy_rejects_ineligible_schema_or_timezone(
    tmp_path: Path, schema: str, timezone_name: str, expected: str,
) -> None:
    from athena_research.forex_edge.sources.dukascopy import import_dukascopy

    source = tmp_path / "quotes.csv"
    source.write_text("time,bid,ask\n2021-01-04 15:30:01,1.2,1.2002\n")
    with pytest.raises(ValueError, match=expected):
        import_dukascopy(
            source,
            symbol="EURUSD",
            timezone_name=timezone_name,
            schema=schema,
        )


def test_dukascopy_rejects_crossed_and_conflicting_quotes(tmp_path: Path) -> None:
    from athena_research.forex_edge.sources.dukascopy import import_dukascopy

    crossed = tmp_path / "crossed.csv"
    crossed.write_text("time,bid,ask\n2021-01-04 15:30:01,1.2003,1.2002\n")
    with pytest.raises(ValueError, match="CROSSED_QUOTE"):
        import_dukascopy(
            crossed, symbol="EURUSD", timezone_name="UTC",
            schema="tick_bid_ask",
        )

    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text(
        "time,bid,ask\n"
        "2021-01-04 15:30:01,1.2000,1.2002\n"
        "2021-01-04 15:30:01,1.2001,1.2003\n"
    )
    with pytest.raises(ValueError, match="DUPLICATE_CONFLICT"):
        import_dukascopy(
            duplicate, symbol="EURUSD", timezone_name="UTC",
            schema="tick_bid_ask",
        )
```

- [ ] **Step 2: Run RED**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_dukascopy_ticks_resample_to_executable_m5_bars tests/test_forex_edge_research.py::test_dukascopy_rejects_ineligible_schema_or_timezone tests/test_forex_edge_research.py::test_dukascopy_rejects_crossed_and_conflicting_quotes -q
```

Expected: import failure for `sources.dukascopy`.

- [ ] **Step 3: Implement explicit-schema import**

Create `athena_research/forex_edge/sources/dukascopy.py` with these public
contracts:

```python
KNOWN_SCHEMAS = {
    "tick_bid_ask": {
        "time": "time", "bid": "bid", "ask": "ask",
    },
    "m5_bid_ask": {
        "time": "time",
        "bid_open": "bid_open", "bid_high": "bid_high",
        "bid_low": "bid_low", "bid_close": "bid_close",
        "ask_open": "ask_open", "ask_high": "ask_high",
        "ask_low": "ask_low", "ask_close": "ask_close",
    },
}

BAR_COLUMNS = (
    "timestamp", "symbol",
    "bid_open", "bid_high", "bid_low", "bid_close",
    "ask_open", "ask_high", "ask_low", "ask_close",
)


def import_dukascopy(
    source_path: Path,
    *,
    symbol: str,
    timezone_name: str,
    schema: str,
    column_mapping: dict[str, str] | None = None,
    delimiter: str = ",",
) -> pd.DataFrame:
    ...
```

Implement the body in this exact order:

1. Reject missing/unknown timezone with `AMBIGUOUS_TIMEZONE`; resolve it with
   `zoneinfo.ZoneInfo`.
2. Reject schemas containing `midpoint` with `MIDPOINT_ONLY`; reject any
   unknown schema unless a complete explicit `column_mapping` is supplied.
3. Read without modifying the source file and compute its SHA-256 separately
   for the dataset manifest.
4. Parse local timestamps with
   `tz_localize(timezone_name, ambiguous="raise", nonexistent="raise")`, then
   convert to UTC.
5. Reject nonpositive bid/ask values, any `ask < bid`, and same-timestamp rows
   whose executable values differ.
6. For tick input, resample half-open M5 intervals with
   `resample("5min", label="right", closed="left")`; use first/max/min/last
   independently for bid and ask.
7. For bar input, treat source timestamps as interval starts unless the
   configured schema explicitly declares `timestamp_represents: end`; shift
   starts forward five minutes so normalized bars are always keyed by end.
8. Return only `BAR_COLUMNS`, sorted by UTC timestamp, with no forward fill.

Add a private `_validate_bar_quotes()` that validates every OHLC side and
checks `ask_open >= bid_open` and `ask_close >= bid_close`. Do not repair
crossed or missing quotes.

- [ ] **Step 4: Run GREEN and commit**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_dukascopy_ticks_resample_to_executable_m5_bars tests/test_forex_edge_research.py::test_dukascopy_rejects_ineligible_schema_or_timezone tests/test_forex_edge_research.py::test_dukascopy_rejects_crossed_and_conflicting_quotes -q
git add -- athena_research/forex_edge/sources/dukascopy.py tests/test_forex_edge_research.py
git commit -m "feat(forex-edge): import executable Dukascopy quotes"
```

---

## Task 7: Frozen Currency Signals And Point-In-Time Eligibility

**Files:**
- Create: `athena_research/forex_edge/portfolio/__init__.py`
- Create: `athena_research/forex_edge/portfolio/signals.py`
- Modify: `tests/test_forex_edge_research.py`

- [ ] **Step 1: Add failing signal tests**

Append:

```python
def test_portfolio_signals_use_only_available_values_and_do_not_impute() -> None:
    from athena_research.forex_edge.portfolio.signals import (
        carry_proxy_scores,
        momentum_12_1_scores,
        reer_value_5y_scores,
    )

    decision = pd.Timestamp("2020-01-31 23:59:59Z")
    rates = pd.DataFrame({
        "currency": ["EUR", "GBP", "JPY"],
        "timestamp": pd.to_datetime(
            ["2020-01-30", "2020-01-30", "2020-01-30"], utc=True
        ),
        "available_time": pd.to_datetime(
            ["2020-01-31", "2020-01-31", "2020-02-03"], utc=True
        ),
        "value": [1.0, 2.0, -0.1],
        "unit": ["Percent", "Percent", "Percent"],
        "availability_verified": [True, True, True],
    })
    carry = carry_proxy_scores(rates, decision)
    assert carry.to_dict() == {"EUR": 0.01, "GBP": 0.02}
    assert "JPY" not in carry

    dates = pd.date_range("2018-01-31", periods=25, freq="ME", tz="UTC")
    returns = pd.DataFrame({
        "timestamp": list(dates) * 2,
        "currency": ["EUR"] * len(dates) + ["GBP"] * len(dates),
        "return": [0.01] * len(dates) + [0.02] * (len(dates) - 1) + [float("nan")],
        "available_time": list(dates) * 2,
    })
    momentum = momentum_12_1_scores(returns, decision)
    assert momentum["EUR"] == pytest.approx((1.01 ** 11) - 1)
    assert "GBP" not in momentum

    reer_dates = pd.date_range("2015-01-31", periods=61, freq="ME", tz="UTC")
    reer = pd.DataFrame({
        "timestamp": list(reer_dates) * 2,
        "available_time": list(reer_dates) * 2,
        "currency": ["EUR"] * 61 + ["GBP"] * 61,
        "value": [100.0] * 60 + [90.0] + [100.0] * 60 + [float("nan")],
    })
    value = reer_value_5y_scores(reer, reer_dates[-1])
    assert value["EUR"] > 0
    assert "GBP" not in value


def test_centered_ranks_and_blend_preserve_missingness() -> None:
    from athena_research.forex_edge.portfolio.signals import (
        blend_rank_scores,
        centered_rank_scores,
    )

    ranked = centered_rank_scores(pd.Series({"EUR": 3.0, "GBP": 2.0, "JPY": 1.0}))
    assert ranked["EUR"] == pytest.approx(1.0)
    assert ranked["JPY"] == pytest.approx(-1.0)

    blend = blend_rank_scores({
        "carry": pd.Series({"EUR": 1.0, "GBP": 0.0}),
        "momentum": pd.Series({"EUR": 0.5, "GBP": -0.5}),
        "value": pd.Series({"EUR": 0.0}),
    })
    assert blend.to_dict() == {"EUR": pytest.approx(0.5)}
```

- [ ] **Step 2: Run RED**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_portfolio_signals_use_only_available_values_and_do_not_impute tests/test_forex_edge_research.py::test_centered_ranks_and_blend_preserve_missingness -q
```

Expected: import failure for `portfolio.signals`.

- [ ] **Step 3: Implement the frozen formulas**

Create `athena_research/forex_edge/portfolio/signals.py`:

```python
from athena_research.forex_edge.sources.fred import percent_to_decimal


def _available(frame: pd.DataFrame, decision_time: pd.Timestamp) -> pd.DataFrame:
    decision = pd.Timestamp(decision_time)
    decision = decision.tz_localize("UTC") if decision.tzinfo is None else decision.tz_convert("UTC")
    return frame[frame["available_time"] <= decision].copy()


def carry_proxy_scores(
    rates: pd.DataFrame, decision_time: pd.Timestamp,
) -> pd.Series:
    usable = _available(rates, decision_time)
    usable = usable[
        usable["availability_verified"].eq(True)
    ].dropna(subset=["value", "unit"])
    latest = usable.sort_values("timestamp").groupby("currency").tail(1)
    values = {
        str(row["currency"]): percent_to_decimal(
            float(row["value"]), str(row["unit"])
        )
        for _, row in latest.iterrows()
    }
    return pd.Series(values, dtype=float).sort_index()


def momentum_12_1_scores(
    currency_returns: pd.DataFrame, decision_time: pd.Timestamp,
) -> pd.Series:
    usable = _available(currency_returns, decision_time)
    monthly = (
        usable.set_index("timestamp")
        .groupby("currency")["return"]
        .resample("ME").apply(lambda values: (1.0 + values).prod() - 1.0)
        .rename("return").reset_index()
    )
    values: dict[str, float] = {}
    for currency, group in monthly.groupby("currency"):
        history = group[group["timestamp"] < pd.Timestamp(decision_time)].tail(12)
        window = history.iloc[:-1]
        if len(window) == 11 and window["return"].notna().all():
            values[str(currency)] = float((1.0 + window["return"]).prod() - 1.0)
    return pd.Series(values, dtype=float).sort_index()


def reer_value_5y_scores(
    reer: pd.DataFrame, decision_time: pd.Timestamp,
) -> pd.Series:
    usable = _available(reer, decision_time)
    values: dict[str, float] = {}
    for currency, group in usable.sort_values("timestamp").groupby("currency"):
        history = group.dropna(subset=["value"]).tail(61)
        if len(history) != 61:
            continue
        current = float(history.iloc[-1]["value"])
        trailing_mean = float(history.iloc[:-1]["value"].mean())
        if trailing_mean > 0:
            values[str(currency)] = -(current / trailing_mean - 1.0)
    return pd.Series(values, dtype=float).sort_index()


def centered_rank_scores(values: pd.Series) -> pd.Series:
    clean = values.dropna().astype(float)
    if len(clean) < 2:
        return pd.Series(dtype=float)
    ranks = clean.rank(method="average")
    return ((ranks - 1.0) / (len(clean) - 1.0) * 2.0 - 1.0).sort_index()


def blend_rank_scores(parts: dict[str, pd.Series]) -> pd.Series:
    if not parts:
        return pd.Series(dtype=float)
    joined = pd.concat(parts, axis=1, join="inner").dropna(how="any")
    return joined.mean(axis=1).sort_index()
```

The momentum test fixture may require its decision date to be moved after the
last fixture month so the 12 complete prior months are visible. Make that
fixture correction only; do not change the 12-1 formula or fill missing data.

- [ ] **Step 4: Run GREEN and commit**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_portfolio_signals_use_only_available_values_and_do_not_impute tests/test_forex_edge_research.py::test_centered_ranks_and_blend_preserve_missingness -q
git add -- athena_research/forex_edge/portfolio/__init__.py athena_research/forex_edge/portfolio/signals.py tests/test_forex_edge_research.py
git commit -m "feat(forex-edge): add frozen currency signals"
```

---

## Task 8: Currency Portfolio Construction, Costs, And Backtest

**Files:**
- Create: `athena_research/forex_edge/portfolio/construction.py`
- Create: `athena_research/forex_edge/portfolio/costs.py`
- Create: `athena_research/forex_edge/portfolio/backtest.py`
- Modify: `tests/test_forex_edge_research.py`

- [ ] **Step 1: Add failing construction and timing tests**

Append:

```python
def test_currency_weights_are_neutral_capped_and_map_to_canonical_pairs() -> None:
    from athena_research.forex_edge.portfolio.construction import (
        build_currency_weights,
        map_currency_weights_to_pairs,
    )

    scores = pd.Series({
        "AUD": 6, "EUR": 5, "GBP": 4, "NZD": 3,
        "CAD": -3, "CHF": -4, "JPY": -5, "USD": -6,
        "BRL": 2, "INR": 1, "MXN": 0, "SGD": -1,
    }, dtype=float)
    weights = build_currency_weights(scores, top_n=4, min_currencies=12)
    assert weights.abs().sum() == pytest.approx(1.0)
    assert weights.sum() == pytest.approx(0.0)
    assert weights.abs().max() <= 0.25

    pair_weights = map_currency_weights_to_pairs(weights)
    assert set(pair_weights).issubset({
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD",
        "USDCHF", "USDZAR", "USDMXN", "USDSGD", "USDBRL", "USDINR",
    })


def test_volatility_scaling_uses_prior_returns_only() -> None:
    from athena_research.forex_edge.portfolio.construction import (
        scale_weights_to_vol,
    )

    weights = pd.Series({"EUR": 0.5, "JPY": -0.5})
    prior = pd.Series([0.001] * 62 + [0.002])
    scaled = scale_weights_to_vol(
        weights, prior, target_vol=0.10, lookback=63, max_gross=2.0,
    )
    changed_future = pd.concat([prior, pd.Series([0.50])], ignore_index=True)
    assert scale_weights_to_vol(
        weights, changed_future.iloc[:-1], target_vol=0.10,
        lookback=63, max_gross=2.0,
    ).to_dict() == pytest.approx(scaled.to_dict())
    assert scaled.abs().sum() <= 2.0


def test_portfolio_positions_start_after_decision_and_charge_turnover() -> None:
    from athena_research.forex_edge.portfolio.backtest import run_monthly_portfolio

    dates = pd.to_datetime(
        ["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-04"], utc=True
    )
    pair_returns = pd.DataFrame({
        "timestamp": list(dates) * 2,
        "symbol": ["EURUSD"] * 4 + ["USDJPY"] * 4,
        "return": [0.0, 0.50, 0.01, 0.01, 0.0, -0.50, -0.01, -0.01],
    })
    decisions = {
        pd.Timestamp("2020-01-31T00:00:00Z"): pd.Series(
            {"EUR": 0.5, "JPY": -0.5}
        )
    }
    result = run_monthly_portfolio(
        pair_returns,
        decisions,
        roundtrip_cost_bps={"EURUSD": 1.8, "USDJPY": 1.8},
        cost_multiplier=1.0,
    )
    daily = result.daily_returns.set_index("timestamp")
    assert daily.loc[pd.Timestamp("2020-01-31T00:00:00Z"), "gross_return"] == 0
    assert daily.loc[pd.Timestamp("2020-02-03T00:00:00Z"), "gross_return"] > 0
    assert daily["cost"].sum() > 0
```

- [ ] **Step 2: Run RED**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_currency_weights_are_neutral_capped_and_map_to_canonical_pairs tests/test_forex_edge_research.py::test_volatility_scaling_uses_prior_returns_only tests/test_forex_edge_research.py::test_portfolio_positions_start_after_decision_and_charge_turnover -q
```

Expected: import failures for portfolio construction/backtest.

- [ ] **Step 3: Implement construction and canonical execution mapping**

Create `portfolio/construction.py` with:

```python
CANONICAL_PAIR = {
    "EUR": ("EURUSD", 1.0), "GBP": ("GBPUSD", 1.0),
    "JPY": ("USDJPY", -1.0), "AUD": ("AUDUSD", 1.0),
    "NZD": ("NZDUSD", 1.0), "CAD": ("USDCAD", -1.0),
    "CHF": ("USDCHF", -1.0), "ZAR": ("USDZAR", -1.0),
    "MXN": ("USDMXN", -1.0), "SGD": ("USDSGD", -1.0),
    "BRL": ("USDBRL", -1.0), "INR": ("USDINR", -1.0),
}


def build_currency_weights(
    scores: pd.Series, *, top_n: int, min_currencies: int,
) -> pd.Series:
    clean = scores.dropna().sort_values()
    if len(clean) < min_currencies:
        raise ValueError("INSUFFICIENT_UNIVERSE_BREADTH")
    short = clean.head(top_n).index
    long = clean.tail(top_n).index
    weights = pd.Series(0.0, index=clean.index)
    weights.loc[long] = 0.5 / top_n
    weights.loc[short] = -0.5 / top_n
    if weights.abs().max() > 0.25 or abs(float(weights.sum())) > 1e-12:
        raise ValueError("INVALID_EXPOSURE")
    return weights[weights.ne(0)].sort_index()


def map_currency_weights_to_pairs(weights: pd.Series) -> pd.Series:
    pair_weights: dict[str, float] = {}
    for currency, weight in weights.items():
        if currency == "USD":
            continue
        pair, orientation = CANONICAL_PAIR[str(currency)]
        pair_weights[pair] = pair_weights.get(pair, 0.0) + float(weight) * orientation
    return pd.Series(pair_weights, dtype=float).sort_index()


def scale_weights_to_vol(
    weights: pd.Series,
    prior_portfolio_returns: pd.Series,
    *,
    target_vol: float,
    lookback: int,
    max_gross: float,
) -> pd.Series:
    history = prior_portfolio_returns.dropna().tail(lookback)
    if len(history) != lookback:
        raise ValueError("INSUFFICIENT_HISTORY")
    annualized = float(history.std(ddof=1) * np.sqrt(252.0))
    if not np.isfinite(annualized) or annualized <= 0:
        raise ValueError("INVALID_VOLATILITY")
    multiplier = min(target_vol / annualized, max_gross / float(weights.abs().sum()))
    return weights * multiplier
```

USD is the residual economic leg. It is not emitted as a separate tradable
instrument. After mapping, verify that reconstructed currency legs equal the
requested non-USD legs and the implied USD residual equals
`-sum(non_usd_weights)`.

- [ ] **Step 4: Implement frozen costs and monthly backtest**

Create `portfolio/costs.py`:

```python
MAJORS = frozenset({
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF",
})


def roundtrip_cost_bps(symbol: str) -> float:
    return 1.8 if symbol in MAJORS else 4.1


def rebalance_cost(
    previous: pd.Series,
    current: pd.Series,
    costs_bps: dict[str, float],
    *,
    multiplier: float,
) -> float:
    aligned = pd.concat([previous, current], axis=1).fillna(0.0)
    aligned.columns = ["previous", "current"]
    delta = (aligned["current"] - aligned["previous"]).abs()
    # A position change pays half of the frozen round-trip schedule. Opening
    # and later closing together pay the complete round trip.
    per_side = pd.Series({symbol: costs_bps[symbol] / 2e4 for symbol in delta.index})
    return float((delta * per_side * multiplier).sum())
```

Create `portfolio/backtest.py` with:

```python
@dataclass(frozen=True)
class PortfolioBacktest:
    daily_returns: pd.DataFrame
    positions: pd.DataFrame
    turnover: float
    total_cost: float


def run_monthly_portfolio(
    pair_returns: pd.DataFrame,
    decisions: dict[pd.Timestamp, pd.Series],
    *,
    roundtrip_cost_bps: dict[str, float],
    cost_multiplier: float,
) -> PortfolioBacktest:
    ...
```

The implementation must:

1. Pivot pair returns by UTC date and symbol.
2. Convert each decision's currency legs to canonical pair weights.
3. Apply those weights only on the first available row strictly after the
   decision timestamp; never apply a month-end decision to the same row.
4. Forward-hold positions until the next rebalance.
5. Compute gross return from prior-applied positions and current pair returns.
6. Charge `rebalance_cost()` on the application row, including the initial
   opening and final liquidation.
7. Emit `gross_return`, `cost`, `net_return`, `gross_exposure`, and
   `net_pair_exposure` for every date.
8. Raise `MISSING_PAIR` if a requested canonical pair column is absent; do not
   silently drop a currency.

- [ ] **Step 5: Run GREEN and commit**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_currency_weights_are_neutral_capped_and_map_to_canonical_pairs tests/test_forex_edge_research.py::test_volatility_scaling_uses_prior_returns_only tests/test_forex_edge_research.py::test_portfolio_positions_start_after_decision_and_charge_turnover -q
git add -- athena_research/forex_edge/portfolio/construction.py athena_research/forex_edge/portfolio/costs.py athena_research/forex_edge/portfolio/backtest.py tests/test_forex_edge_research.py
git commit -m "feat(forex-edge): backtest monthly currency portfolios"
```

---

## Task 9: Fixing Calendars, Exact Windows, And Executable PnL

**Files:**
- Create: `athena_research/forex_edge/fixing/__init__.py`
- Create: `athena_research/forex_edge/fixing/calendar.py`
- Create: `athena_research/forex_edge/fixing/windows.py`
- Create: `athena_research/forex_edge/fixing/costs.py`
- Create: `athena_research/forex_edge/fixing/backtest.py`
- Modify: `tests/test_forex_edge_research.py`

- [ ] **Step 1: Add failing calendar and executable-pricing tests**

Append:

```python
def test_fixing_calendar_resolves_london_dst_and_fixed_tokyo_offset() -> None:
    from athena_research.forex_edge.fixing.calendar import resolve_fixing_utc

    assert resolve_fixing_utc(
        pd.Timestamp("2021-01-15"), timezone_name="Europe/London",
        local_time="16:00",
    ) == pd.Timestamp("2021-01-15 16:00:00Z")
    assert resolve_fixing_utc(
        pd.Timestamp("2021-07-15"), timezone_name="Europe/London",
        local_time="16:00",
    ) == pd.Timestamp("2021-07-15 15:00:00Z")
    assert resolve_fixing_utc(
        pd.Timestamp("2021-07-15"), timezone_name="Asia/Tokyo",
        local_time="09:55",
    ) == pd.Timestamp("2021-07-15 00:55:00Z")


def test_fixing_window_enters_next_bar_and_uses_executable_sides() -> None:
    from athena_research.forex_edge.fixing.backtest import run_fixing_event
    from athena_research.forex_edge.fixing.windows import fixing_window

    fixing = pd.Timestamp("2021-07-15 15:00:00Z")
    times = pd.date_range("2021-07-15 14:30:00Z", periods=13, freq="5min")
    bars = pd.DataFrame({
        "timestamp": times,
        "symbol": "EURUSD",
        "bid_open": [1.1000 + i * 0.0001 for i in range(13)],
        "bid_close": [1.10005 + i * 0.0001 for i in range(13)],
        "ask_open": [1.1002 + i * 0.0001 for i in range(13)],
        "ask_close": [1.10025 + i * 0.0001 for i in range(13)],
    })
    for side in ("bid", "ask"):
        bars[f"{side}_high"] = bars[[f"{side}_open", f"{side}_close"]].max(axis=1)
        bars[f"{side}_low"] = bars[[f"{side}_open", f"{side}_close"]].min(axis=1)

    window = fixing_window(fixing, mode="pre_continuation")
    trade = run_fixing_event(
        bars, symbol="EURUSD", window=window,
        roundtrip_commission_bps=0.6, cost_multiplier=1.0,
    )
    assert trade is not None
    assert trade["signal_bar_end"] == pd.Timestamp("2021-07-15 14:45:00Z")
    assert trade["entry_bar_end"] == pd.Timestamp("2021-07-15 14:50:00Z")
    assert trade["entry_price"] == pytest.approx(
        bars.loc[bars["timestamp"].eq(trade["entry_bar_end"]), "ask_open"].item()
    )
    assert trade["exit_price"] == pytest.approx(
        bars.loc[bars["timestamp"].eq(fixing), "bid_close"].item()
    )


def test_fixing_event_fails_closed_when_required_bar_is_missing() -> None:
    from athena_research.forex_edge.fixing.backtest import run_fixing_event
    from athena_research.forex_edge.fixing.windows import fixing_window

    fixing = pd.Timestamp("2021-01-15 16:00:00Z")
    bars = pd.DataFrame({"timestamp": [fixing], "symbol": ["EURUSD"]})
    with pytest.raises(ValueError, match="NO_EXECUTABLE_QUOTE"):
        run_fixing_event(
            bars, symbol="EURUSD",
            window=fixing_window(fixing, mode="post_reversal"),
            roundtrip_commission_bps=0.6, cost_multiplier=1.0,
        )
```

- [ ] **Step 2: Run RED**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_fixing_calendar_resolves_london_dst_and_fixed_tokyo_offset tests/test_forex_edge_research.py::test_fixing_window_enters_next_bar_and_uses_executable_sides tests/test_forex_edge_research.py::test_fixing_event_fails_closed_when_required_bar_is_missing -q
```

Expected: import failures for fixing modules.

- [ ] **Step 3: Implement exact fixing timestamps and windows**

Create `fixing/calendar.py`:

```python
def resolve_fixing_utc(
    date: pd.Timestamp, *, timezone_name: str, local_time: str,
) -> pd.Timestamp:
    day = pd.Timestamp(date).date()
    hour, minute = (int(part) for part in local_time.split(":"))
    local = pd.Timestamp(
        datetime.combine(day, time(hour, minute)), tz=ZoneInfo(timezone_name)
    )
    return local.tz_convert("UTC")
```

Create `fixing/windows.py`:

```python
@dataclass(frozen=True)
class FixingWindow:
    fixing_time: pd.Timestamp
    observation_start: pd.Timestamp
    signal_bar_end: pd.Timestamp
    entry_bar_end: pd.Timestamp
    exit_bar_end: pd.Timestamp
    direction_mode: Literal["continuation", "reversal"]


def fixing_window(
    fixing_time: pd.Timestamp,
    *,
    mode: Literal["pre_continuation", "post_reversal"],
) -> FixingWindow:
    fixing = pd.Timestamp(fixing_time).tz_convert("UTC")
    if mode == "pre_continuation":
        signal = fixing - pd.Timedelta(minutes=15)
        return FixingWindow(
            fixing, fixing - pd.Timedelta(minutes=30), signal,
            signal + pd.Timedelta(minutes=5), fixing, "continuation",
        )
    if mode == "post_reversal":
        signal = fixing
        return FixingWindow(
            fixing, fixing - pd.Timedelta(minutes=15), signal,
            signal + pd.Timedelta(minutes=5),
            fixing + pd.Timedelta(minutes=30), "reversal",
        )
    raise ValueError(f"Unknown fixing mode: {mode}")
```

Do not snap timestamps to a nearby bar.

- [ ] **Step 4: Implement executable event PnL**

Create `fixing/costs.py`:

```python
def stressed_trade_return(
    *,
    direction: int,
    entry_bid: float,
    entry_ask: float,
    exit_bid: float,
    exit_ask: float,
    commission_bps: float,
    cost_multiplier: float,
) -> tuple[float, float, float]:
    entry_mid = (entry_bid + entry_ask) / 2.0
    exit_mid = (exit_bid + exit_ask) / 2.0
    gross_mid = direction * (exit_mid / entry_mid - 1.0)
    executable = (
        exit_bid / entry_ask - 1.0
        if direction > 0 else entry_bid / exit_ask - 1.0
    )
    observed_spread_cost = gross_mid - executable
    commission = commission_bps / 1e4
    net = gross_mid - (observed_spread_cost + commission) * cost_multiplier
    return float(gross_mid), float(observed_spread_cost), float(net)
```

Create `fixing/backtest.py` with:

```python
def run_fixing_event(
    bars: pd.DataFrame,
    *,
    symbol: str,
    window: FixingWindow,
    roundtrip_commission_bps: float,
    cost_multiplier: float,
) -> dict[str, object] | None:
    ...


def run_fixing_backtest(
    bars: pd.DataFrame,
    *,
    symbol: str,
    event_dates: Iterable[pd.Timestamp],
    timezone_name: str,
    local_time: str,
    mode: Literal["pre_continuation", "post_reversal"],
    roundtrip_commission_bps: float,
    cost_multiplier: float,
) -> pd.DataFrame:
    ...
```

For each event:

1. Select exact rows keyed by `observation_start`, `signal_bar_end`,
   `entry_bar_end`, and `exit_bar_end`.
2. Use completed-bar midpoint closes at observation start and signal time.
3. Set direction to sign of the midpoint move for continuation and the
   opposite sign for reversal; return `None` when the move is exactly zero.
4. Use the next bar's executable open and the exit bar's executable close.
5. Record gross midpoint return, observed spread cost, commission, net return,
   direction, all timestamps, all executable prices, and quality flags.
6. Raise `NO_EXECUTABLE_QUOTE` for any missing required row or side and
   `CROSSED_QUOTE` for malformed sides.
7. Preserve one row per event and never net overlapping trades.

- [ ] **Step 5: Run GREEN and commit**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_fixing_calendar_resolves_london_dst_and_fixed_tokyo_offset tests/test_forex_edge_research.py::test_fixing_window_enters_next_bar_and_uses_executable_sides tests/test_forex_edge_research.py::test_fixing_event_fails_closed_when_required_bar_is_missing -q
git add -- athena_research/forex_edge/fixing tests/test_forex_edge_research.py
git commit -m "feat(forex-edge): backtest exact fixing windows"
```

---

## Task 10: Independent Validation, Trial Registry, And Result Classification

**Files:**
- Create: `athena_research/forex_edge/validation.py`
- Modify: `tests/test_forex_edge_research.py`

**Boundary:** Do not import `athena_research.ase.bootstrap` or
`athena_research.ase.dsr_pbo`. Reproduce the small formulas locally so the
standalone package keeps its no-ASE runtime contract. Cite the source formula
in comments; do not copy ASE package dependencies.

The frozen registry has 48 rows:

```text
portfolio: 4 configurations x 3 cost multipliers = 12
fixing:    4 window configurations x 3 pairs x 3 cost multipliers = 36
```

Use `n_trials=48` for every DSR result. Build CSCV matrices separately:
12 portfolio columns on a common daily index and 36 fixing columns on a
common event-date index. Mixing daily and event observations in one matrix is
forbidden.

- [ ] **Step 1: Add failing validation tests**

Append:

```python
def test_chronological_splits_are_fixed_and_do_not_move() -> None:
    from athena_research.forex_edge.validation import chronological_split

    dates = pd.date_range("2018-12-30", "2019-01-02", freq="D", tz="UTC")
    development, holdout = chronological_split(
        dates, development_end="2018-12-31", holdout_start="2019-01-01",
    )
    assert list(development) == list(dates[:2])
    assert list(holdout) == list(dates[2:])

    late = pd.date_range("2020-01-01", periods=5, freq="D", tz="UTC")
    with pytest.raises(ValueError, match="BLOCKED_DATA"):
        chronological_split(
            late, development_end="2018-12-31", holdout_start="2019-01-01",
        )


def test_bootstrap_and_dsr_are_deterministic_and_use_all_48_trials() -> None:
    from athena_research.forex_edge.validation import (
        block_bootstrap_lower_bound,
        deflated_sharpe,
    )

    returns = pd.Series([0.01, -0.002, 0.008, 0.004] * 20)
    first = block_bootstrap_lower_bound(
        returns, block_size=10, resamples=500, seed=42,
    )
    second = block_bootstrap_lower_bound(
        returns, block_size=10, resamples=500, seed=42,
    )
    assert first == second
    dsr = deflated_sharpe(returns, n_trials=48)
    assert dsr["n_trials"] == 48
    assert dsr["n_obs"] == 80


def test_cscv_pbo_uses_lane_matrix_and_reports_unavailable() -> None:
    from athena_research.forex_edge.validation import cscv_pbo

    too_short = cscv_pbo(np.ones((8, 12)), n_partitions=16)
    assert too_short["pbo"] is None
    assert too_short["reason_code"] == "PBO_UNAVAILABLE"

    rng = np.random.default_rng(7)
    result = cscv_pbo(rng.normal(size=(64, 12)), n_partitions=16)
    assert result["n_configs"] == 12
    assert 0.0 <= result["pbo"] <= 1.0


def test_candidate_classification_requires_every_registered_gate() -> None:
    from athena_research.forex_edge.validation import classify_result

    passing = {
        "holdout_net_return": 0.10,
        "bootstrap_lb": 0.001,
        "cost_2x_net_return": 0.03,
        "dsr_z": 1.80,
        "pbo": 0.25,
        "max_positive_contribution_share": 0.30,
        "positive_holdout_years": 3,
    }
    result = classify_result(
        passing, quality_passed=True,
        evidence_flags=["PROXY_TRANSACTION_COSTS"],
    )
    assert result["study_status"] == "RESEARCH_CANDIDATE"
    assert result["production_eligible"] is False

    unavailable = dict(passing, pbo=None)
    failed = classify_result(
        unavailable, quality_passed=True, evidence_flags=[],
    )
    assert failed["study_status"] == "COMPLETED_NO_EDGE"
    assert "PBO_UNAVAILABLE" in failed["evidence_flags"]
```

- [ ] **Step 2: Run RED**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_chronological_splits_are_fixed_and_do_not_move tests/test_forex_edge_research.py::test_bootstrap_and_dsr_are_deterministic_and_use_all_48_trials tests/test_forex_edge_research.py::test_cscv_pbo_uses_lane_matrix_and_reports_unavailable tests/test_forex_edge_research.py::test_candidate_classification_requires_every_registered_gate -q
```

Expected: import failure for `validation`.

- [ ] **Step 3: Implement split and return metrics**

Create `validation.py` with constants:

```python
TOTAL_REGISTERED_TRIALS = 48
PORTFOLIO_REGISTERED_TRIALS = 12
FIXING_REGISTERED_TRIALS = 36

CANDIDATE_DSR_Z = 1.645
CANDIDATE_MAX_PBO = 0.50
CANDIDATE_MAX_CONTRIBUTION = 0.40
CANDIDATE_MIN_POSITIVE_YEARS = 3
```

Implement:

```python
def chronological_split(
    timestamps: Iterable[pd.Timestamp],
    *,
    development_end: str,
    holdout_start: str,
) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    index = pd.DatetimeIndex(timestamps)
    index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    dev_end = pd.Timestamp(development_end, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    holdout = pd.Timestamp(holdout_start, tz="UTC")
    development = index[index <= dev_end]
    final = index[index >= holdout]
    if len(development) == 0 or len(final) == 0:
        raise ValueError("BLOCKED_DATA: fixed chronological split unavailable")
    return development, final
```

Also implement `return_metrics(returns, periods_per_year)` returning:
`n_obs`, arithmetic return, compounded return, annualized volatility, Sharpe,
Sortino, max drawdown, Calmar, win rate, and expectancy. Use JSON `None` plus
an ordered `undefined_metrics` mapping when a denominator or sample size is
invalid. Do not convert undefined values to zero.

- [ ] **Step 4: Implement deterministic bootstrap and approximate DSR**

Implement `block_bootstrap_lower_bound()` using contiguous blocks and a fixed
NumPy generator:

```python
def block_bootstrap_lower_bound(
    returns: pd.Series,
    *,
    block_size: int,
    resamples: int,
    seed: int,
    alpha: float = 0.05,
) -> dict[str, object]:
    values = returns.to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return {
            "mean": None, "ci_low": None, "ci_high": None,
            "lower_bound": None, "reason_code": "INSUFFICIENT_HISTORY",
        }
    ...
```

The resampling loop must match the repository's approximate contiguous-block
method: repeatedly select a valid block start, append that block, truncate to
the original length, compute the mean, and return the two-sided 95% interval.

Implement `deflated_sharpe()` with the same equations as
`athena_research/ase/dsr_pbo.py:deflated_sharpe_ratio`, but return a plain
JSON-safe mapping. The output field is `dsr_z`, because the repository helper
returns the standardized deflated-Sharpe statistic, not a probability. Include
`observed_sharpe`, `n_trials`, `n_obs`, `skew`, and `kurtosis`.

- [ ] **Step 5: Implement lane-specific CSCV PBO**

Implement `cscv_pbo(performance_matrix, n_partitions=16)` with the repository
algorithm:

1. Validate a two-dimensional `n_observations x n_configurations` matrix.
2. Return `pbo=None` and `PBO_UNAVAILABLE` when observations are fewer than
   partitions or configurations are fewer than two.
3. Split chronological row indices into 16 partitions.
4. For each half-partition combination, select the best in-sample
   configuration by mean return and record its out-of-sample rank logit.
5. Return the fraction of logits greater than zero, the logit distribution,
   partition count, and configuration count.

Before calling this function, runner code must outer-join every registered
configuration to the lane's common observation index. A row is eligible for
PBO only when all registered columns have finite returns. Report how many rows
were dropped; do not fill missing returns with zero.

- [ ] **Step 6: Implement concentration, annual evidence, and classification**

Implement:

```python
def max_positive_contribution_share(
    returns: pd.Series, groups: pd.Series,
) -> float | None:
    positive_total = float(returns[returns > 0].sum())
    if positive_total <= 0:
        return None
    contribution = returns.clip(lower=0).groupby(groups).sum()
    return float(contribution.max() / positive_total)


def classify_result(
    metrics: dict[str, object],
    *,
    quality_passed: bool,
    evidence_flags: Iterable[str],
) -> dict[str, object]:
    ...
```

`classify_result()` must:

- return `BLOCKED_DATA` if `quality_passed` is false;
- append `PBO_UNAVAILABLE` when PBO is null;
- require strict positivity for holdout result, bootstrap lower bound, and
  2x-cost result;
- require `dsr_z > 1.645`, `pbo < 0.50`,
  contribution share `<= 0.40`, and at least three positive holdout years;
- return `RESEARCH_CANDIDATE` only when every condition passes;
- otherwise return `COMPLETED_NO_EDGE`;
- always return `production_eligible: false`;
- return an ordered `criteria` mapping showing each threshold, observed value,
  and pass/fail result.

For portfolio results compute concentration by currency and year. For fixing
results compute it by pair, event, and year. Use the maximum of all applicable
shares as `max_positive_contribution_share`.

- [ ] **Step 7: Run GREEN and commit**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_chronological_splits_are_fixed_and_do_not_move tests/test_forex_edge_research.py::test_bootstrap_and_dsr_are_deterministic_and_use_all_48_trials tests/test_forex_edge_research.py::test_cscv_pbo_uses_lane_matrix_and_reports_unavailable tests/test_forex_edge_research.py::test_candidate_classification_requires_every_registered_gate -q
git add -- athena_research/forex_edge/validation.py tests/test_forex_edge_research.py
git commit -m "feat(forex-edge): validate registered research trials"
```

---

## Task 11: Deterministic Runner, Quality Report, And Run Artifacts

**Files:**
- Create: `athena_research/forex_edge/reporting.py`
- Create: `athena_research/forex_edge/runner.py`
- Modify: `athena_research/forex_edge/quality.py`
- Modify: `tests/test_forex_edge_research.py`

- [ ] **Step 1: Add failing run-artifact tests**

Append:

```python
def test_trial_registry_is_frozen_complete_and_deterministic() -> None:
    from athena_research.forex_edge.runner import build_trial_registry

    first = build_trial_registry()
    second = build_trial_registry()
    assert first == second
    assert len(first) == 48
    assert sum(row["lane"] == "portfolio" for row in first) == 12
    assert sum(row["lane"] == "fixing" for row in first) == 36
    assert {row["cost_multiplier"] for row in first} == {1.0, 1.5, 2.0}
    assert len({row["trial_id"] for row in first}) == 48


def test_runner_requires_exact_manifest_ids_not_latest(tmp_path: Path) -> None:
    from athena_research.forex_edge.runner import RunRequest

    with pytest.raises(ValueError, match="PINNED_MANIFEST_REQUIRED"):
        RunRequest(
            lane="portfolio",
            dataset_manifests={},
            output_root=tmp_path,
        )


def test_reporting_writes_complete_deterministic_artifact_set(tmp_path: Path) -> None:
    from athena_research.forex_edge.reporting import write_run_artifacts

    payload = {
        "run_id": "run-test",
        "manifest": {"config_hash": "abc", "dataset_manifests": {"fred": "v1"}},
        "eligibility": {"eligible": True, "reason_codes": []},
        "quality": {"passed": True, "issues": []},
        "trials": [{"trial_id": "portfolio:momentum:1.0"}],
        "metrics": {"study_status": "COMPLETED_NO_EDGE", "production_eligible": False},
        "returns": pd.DataFrame({
            "timestamp": pd.to_datetime(["2021-01-01"], utc=True),
            "net_return": [0.001],
        }),
    }
    first = write_run_artifacts(tmp_path, payload)
    second = write_run_artifacts(tmp_path, payload)
    expected = {
        "run_manifest.json", "eligibility.json", "quality.json",
        "trials.jsonl", "metrics.json",
        "equity_or_event_returns.parquet", "report.md",
    }
    assert {path.name for path in first} == expected
    assert {
        path.name: path.read_bytes() for path in first
    } == {
        path.name: path.read_bytes() for path in second
    }
```

- [ ] **Step 2: Run RED**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_trial_registry_is_frozen_complete_and_deterministic tests/test_forex_edge_research.py::test_runner_requires_exact_manifest_ids_not_latest tests/test_forex_edge_research.py::test_reporting_writes_complete_deterministic_artifact_set -q
```

Expected: import failures for runner/reporting.

- [ ] **Step 3: Complete quality-report contracts**

Extend `quality.py` with:

```python
def evaluate_daily_quality(
    *,
    decision_time: pd.Timestamp,
    factors: dict[str, pd.DataFrame],
    eligible_currencies: set[str],
    min_currencies: int,
    staleness_days: dict[str, int],
) -> EligibilityResult:
    ...


def evaluate_m5_event_quality(
    bars: pd.DataFrame,
    *,
    required_timestamps: Iterable[pd.Timestamp],
    max_spread_bps: float,
) -> EligibilityResult:
    ...


def build_universe_quality_report(
    *,
    frozen_pairs: tuple[str, ...],
    daily_coverage: pd.DataFrame,
    m5_coverage: pd.DataFrame,
    source_issues: Iterable[QualityIssue],
) -> dict[str, object]:
    ...
```

Daily evaluation must check availability time, unit, staleness, complete
lookback, breadth, and both long/short basket feasibility. M5 evaluation must
check every exact required timestamp, both executable sides, finite spread,
non-crossed quotes, duplicate conflicts, and configured data-error spread cap.

The universe report must always emit one row for each of the 21 frozen pairs,
including missing rows with `MISSING_PAIR`. It must separately list currency
eligibility and COT mapping coverage.

- [ ] **Step 4: Build the exact registry and pinned request**

Create `runner.py`:

```python
PORTFOLIO_CONFIGS = (
    "carry_proxy",
    "momentum_12_1",
    "reer_value_5y",
    "equal_weight_three_factor_blend",
)
FIXING_CONFIGS = (
    ("london", "pre_continuation"),
    ("london", "post_reversal"),
    ("tokyo", "pre_continuation"),
    ("tokyo", "post_reversal"),
)
FIXING_PAIRS = ("EURUSD", "GBPUSD", "USDJPY")
COST_MULTIPLIERS = (1.0, 1.5, 2.0)


@dataclass(frozen=True)
class RunRequest:
    lane: Literal["portfolio", "fixing", "both"]
    dataset_manifests: dict[str, str]
    output_root: Path

    def __post_init__(self) -> None:
        required = (
            {"fred_spot", "fred_rates", "bis_reer"}
            if self.lane == "portfolio"
            else {"dukascopy_m5"}
            if self.lane == "fixing"
            else {"fred_spot", "fred_rates", "bis_reer", "dukascopy_m5"}
        )
        if not required.issubset(self.dataset_manifests):
            raise BlockedDataError("PINNED_MANIFEST_REQUIRED")


def build_trial_registry() -> list[dict[str, object]]:
    ...
```

Trial IDs are stable lower-case colon-delimited strings. Generate rows in this
order: portfolio configuration, cost multiplier; then fixing location, mode,
pair, cost multiplier. Serialize numeric multipliers as `1.0`, `1.5`, `2.0`.

Add `load_pinned_datasets(store, request)` that resolves only exact manifest
identifiers through `ResearchStore`. Do not expose a `latest` argument.

- [ ] **Step 5: Orchestrate both bounded studies**

Implement:

```python
def run_portfolio_study(
    request: RunRequest, config: dict[str, object],
) -> dict[str, object]:
    ...


def run_fixing_study(
    request: RunRequest, config: dict[str, object],
) -> dict[str, object]:
    ...


def run_research(
    request: RunRequest, config: dict[str, object],
) -> list[Path]:
    ...
```

`run_portfolio_study()` must:

1. Load pinned H.10, rate, and BIS manifests.
2. Reconstruct canonical currency returns with quotation orientation from
   `universe.py`.
3. Build month-end decisions for the four frozen configurations.
4. Apply the same decision weights to all three frozen cost multipliers.
5. Preserve eligibility rows for every decision date, including blocked dates.
6. Split at the fixed 2018-12-31/2019-01-01 boundary.
7. Build the 12-column daily PBO matrix, bootstrap each baseline holdout, use
   DSR `n_trials=48`, and classify each baseline configuration against its
   2x-cost sibling.
8. Attach `NON_PROMOTABLE_PROXY_CARRY` to carry/blend,
   `NON_PROMOTABLE_REVISION_RISK` to value/blend, and
   `PROXY_TRANSACTION_COSTS` to every result.
9. Register carry and blend trials even when rate publication timing is
   unverified, but classify those trials `BLOCKED_DATA` with
   `UNVERIFIED_AVAILABILITY`. Do not block independent momentum or value
   trials for a carry-source limitation.

`run_fixing_study()` must:

1. Load the pinned Dukascopy M5 manifest.
2. Generate London and Tokyo event timestamps without nearest-bar repair.
3. Run the four windows for each initial pair and all cost multipliers.
4. Preserve every skipped event and its reason code in eligibility output.
5. Split at the fixed 2020-12-31/2021-01-01 boundary.
6. Build the 36-column common-event PBO matrix, report dropped incomplete
   rows, bootstrap each baseline holdout, use DSR `n_trials=48`, and classify
   each baseline pair/window against its 2x-cost sibling.
7. Attach `PROXY_TRANSACTION_COSTS`; do not add carry or BIS flags.

`run_research()` must use the exact effective config and pinned manifest
hashes to derive a deterministic run ID. It must not mutate production config,
discover datasets implicitly, download data, or invoke execution.

- [ ] **Step 6: Write deterministic artifacts**

Create `reporting.py`:

```python
RUN_FILES = (
    "run_manifest.json",
    "eligibility.json",
    "quality.json",
    "trials.jsonl",
    "metrics.json",
    "equity_or_event_returns.parquet",
    "report.md",
)


def write_run_artifacts(
    output_root: Path, payload: dict[str, object],
) -> tuple[Path, ...]:
    ...
```

Requirements:

- write into `runs/<run_id>/`;
- canonicalize JSON with sorted keys and stable separators;
- write trials one canonical JSON object per line in registry order;
- sort return rows and columns before Parquet output;
- write Parquet with stable schema and compression;
- use atomic temporary-file replacement;
- redact environment values and URL secret query parameters;
- record code commit, branch, dirty state, diff hash, Python/package versions,
  effective config/hash, exact dataset IDs/hashes, universe hash, registry
  hash, splits, timezone database version when available, bootstrap seeds, and
  redacted arguments;
- make `report.md` state `production_eligible: false` prominently and list
  blocked data, numerical criteria, evidence flags, concentration, annual
  results, and cost stresses;
- refuse to overwrite an existing run directory when generated bytes differ.

- [ ] **Step 7: Run GREEN and commit**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_trial_registry_is_frozen_complete_and_deterministic tests/test_forex_edge_research.py::test_runner_requires_exact_manifest_ids_not_latest tests/test_forex_edge_research.py::test_reporting_writes_complete_deterministic_artifact_set -q
git add -- athena_research/forex_edge/quality.py athena_research/forex_edge/reporting.py athena_research/forex_edge/runner.py tests/test_forex_edge_research.py
git commit -m "feat(forex-edge): produce reproducible research runs"
```

---

## Task 12: Research-Only CLI And Fail-Closed Exit Codes

**Files:**
- Create: `forex_edge_cli.py`
- Modify: `tests/test_forex_edge_research.py`

- [ ] **Step 1: Add failing parser and exit-code tests**

Append:

```python
def test_forex_edge_cli_exposes_research_commands_only() -> None:
    from forex_edge_cli import build_parser

    parser = build_parser()
    choices = parser._subparsers._group_actions[0].choices
    assert set(choices) == {
        "ingest-bis", "ingest-cftc", "ingest-fred", "import-dukascopy",
        "quality-report", "run-portfolio", "run-fixing", "run-both",
    }
    forbidden = {"promote", "execute", "trade", "order", "demo", "shadow"}
    assert set(choices).isdisjoint(forbidden)


def test_forex_edge_cli_returns_blocked_code_as_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import forex_edge_cli

    def blocked(*args, **kwargs):
        raise forex_edge_cli.BlockedDataError("MISSING_PAIR")

    monkeypatch.setattr(forex_edge_cli, "_dispatch", blocked)
    assert forex_edge_cli.main(["quality-report"]) == 3
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "BLOCKED_DATA"
    assert output["reason"] == "MISSING_PAIR"


def test_forex_edge_cli_redacts_provider_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import forex_edge_cli

    monkeypatch.setenv("FRED_API_KEY", "never-print-this")

    def failed(*args, **kwargs):
        raise requests.RequestException(
            "https://api.stlouisfed.org/fred?api_key=never-print-this"
        )

    monkeypatch.setattr(forex_edge_cli, "_dispatch", failed)
    assert forex_edge_cli.main(["ingest-fred"]) == 5
    assert "never-print-this" not in capsys.readouterr().out
```

Add `json` and `requests` to the test imports.

- [ ] **Step 2: Run RED**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_forex_edge_cli_exposes_research_commands_only tests/test_forex_edge_research.py::test_forex_edge_cli_returns_blocked_code_as_json tests/test_forex_edge_research.py::test_forex_edge_cli_redacts_provider_errors -q
```

Expected: import failure for `forex_edge_cli`.

- [ ] **Step 3: Implement the parser and command arguments**

Create `forex_edge_cli.py`:

```python
EXIT_COMPLETED = 0
EXIT_NO_EDGE = 2
EXIT_BLOCKED = 3
EXIT_INVALID = 4
EXIT_PROVIDER = 5


from athena_research.forex_edge.models import BlockedDataError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forex_edge_cli",
        description="Standalone research-only forex edge studies",
    )
    parser.add_argument(
        "--config", default="configs/forex_edge_research.yaml",
    )
    parser.add_argument("--store-root", default="")
    sub = parser.add_subparsers(dest="command", required=True)
    ...
    return parser
```

Arguments:

```text
ingest-bis:
  --start, --end, --series-key (repeatable)
ingest-cftc:
  --year (repeatable)
ingest-fred:
  --dataset {spot,rates}, --start, --end, --series-id (repeatable)
import-dukascopy:
  --file, --symbol, --timezone, --schema, --delimiter
quality-report:
  --manifest source=id (repeatable), --out
run-portfolio:
  --manifest source=id (repeatable), --out
run-fixing:
  --manifest source=id (repeatable), --out
run-both:
  --manifest source=id (repeatable), --out
```

There is no default manifest and no `latest` option. `--file` is required for
Dukascopy and must be treated read-only.

- [ ] **Step 4: Implement dispatch and structured failure handling**

Implement `_dispatch(args)` by importing only from
`athena_research.forex_edge`. It may call source fetch/import functions,
quality reporting, and `run_research`; it must not import ASE, production
config, broker, risk, or execution modules.

Implement `main(argv=None)`:

```python
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        result = _dispatch(args)
        print(json.dumps(redact_secrets(result), indent=2, sort_keys=True))
        statuses = {
            item.get("study_status")
            for item in result.get("results", [])
            if isinstance(item, dict)
        }
        return EXIT_NO_EDGE if statuses == {"COMPLETED_NO_EDGE"} else EXIT_COMPLETED
    except BlockedDataError as exc:
        print(json.dumps({"status": "BLOCKED_DATA", "reason": str(exc)}))
        return EXIT_BLOCKED
    except (ValueError, KeyError, pd.errors.ParserError) as exc:
        print(json.dumps({
            "status": "INVALID_INPUT",
            "reason": redact_text(str(exc)),
        }))
        return EXIT_INVALID
    except requests.RequestException as exc:
        print(json.dumps({
            "status": "PROVIDER_FAILURE",
            "reason": redact_text(str(exc)),
        }))
        return EXIT_PROVIDER
```

If a result contains both candidates and no-edge results, exit zero because
the operation completed and at least one candidate exists. Blocked required
inputs take precedence over numerical status and exit three.

- [ ] **Step 5: Run GREEN and commit**

```powershell
py -m pytest tests/test_forex_edge_research.py::test_forex_edge_cli_exposes_research_commands_only tests/test_forex_edge_research.py::test_forex_edge_cli_returns_blocked_code_as_json tests/test_forex_edge_research.py::test_forex_edge_cli_redacts_provider_errors -q
git add -- forex_edge_cli.py tests/test_forex_edge_research.py
git commit -m "feat(forex-edge): add research-only CLI"
```

---

## Task 13: End-To-End Synthetic Verification And Documentation

**Files:**
- Modify: `tests/test_forex_edge_research.py`
- Modify: `docs/FOREX_EDGE_RESEARCH_STUDY_2026-06-14.md`
- Modify: `docs/superpowers/specs/2026-06-15-forex-edge-research-design.md`

- [ ] **Step 1: Add one synthetic end-to-end test**

Build synthetic pinned FRED/BIS and Dukascopy manifests through
`ResearchStore`, then call `run_research()` for `lane="both"`. The fixture
must span both fixed split boundaries, contain at least 12 currencies for the
portfolio lane, and contain exact London/Tokyo M5 bars for all three fixing
pairs.

Assert:

```python
def test_synthetic_run_both_is_reproducible_and_research_only(tmp_path: Path) -> None:
    ...
    first = run_research(request, config)
    second = run_research(request, config)
    assert {path.name: path.read_bytes() for path in first} == {
        path.name: path.read_bytes() for path in second
    }
    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert metrics["production_eligible"] is False
    assert len(json.loads((run_dir / "run_manifest.json").read_text())[
        "dataset_manifests"
    ]) == 4
    assert sum(1 for _ in (run_dir / "trials.jsonl").open()) == 48
```

Generate deterministic periodic returns directly in the fixture. This test
proves orchestration and artifact contracts, not that a forex edge exists.

- [ ] **Step 2: Run the single permitted verification file**

```powershell
py -m pytest tests/test_forex_edge_research.py -q
```

Expected: all tests pass. Do not run `pytest tests/` or a research matrix.

- [ ] **Step 3: Run static boundary checks**

```powershell
git diff --check
rg -n "athena_ase|execution|auto_trader|risk_engine|guardian|mt5_executor|bybit_executor" athena_research/forex_edge forex_edge_cli.py
py forex_edge_cli.py --help
```

Expected:

- `git diff --check` has no output.
- The import-boundary search has no executable imports. It may match explicit
  deny-list text in tests or documentation only.
- CLI help lists exactly the eight research commands and no execution or
  promotion command.

- [ ] **Step 4: Update research documentation with implemented commands**

Append an implementation-status section to both named documents containing:

- package and CLI paths;
- exact focused test command and result;
- input source limitations;
- the requirement for explicit manifest IDs;
- the meaning of `BLOCKED_DATA`, `COMPLETED_NO_EDGE`, and
  `RESEARCH_CANDIDATE`;
- a statement that no empirical edge is claimed by synthetic tests;
- a statement that production eligibility remains false.

Do not replace the approved design or revise thresholds after seeing results.

- [ ] **Step 5: Run a bounded read-only smoke check**

Run only:

```powershell
py forex_edge_cli.py --help
py forex_edge_cli.py quality-report
```

Expected: help exits zero; quality-report without pinned manifests exits three
with structured `BLOCKED_DATA`. Do not trigger broad live downloads.

Live-source verification is conditional:

- FRED/ALFRED live ingest requires `FRED_API_KEY` to exist, but the command
  must never print its value.
- Dukascopy verification requires user-exported files.
- If either input is absent, report that live ingestion and empirical edge are
  **not verified**. Absence does not justify synthetic fallback data in an
  empirical report.

- [ ] **Step 6: Review the final diff and commit**

```powershell
git diff --stat
git diff -- configs/forex_edge_research.yaml forex_edge_cli.py athena_research/forex_edge tests/test_forex_edge_research.py docs/FOREX_EDGE_RESEARCH_STUDY_2026-06-14.md docs/superpowers/specs/2026-06-15-forex-edge-research-design.md
git diff --check
git add -- configs/forex_edge_research.yaml forex_edge_cli.py athena_research/forex_edge tests/test_forex_edge_research.py docs/FOREX_EDGE_RESEARCH_STUDY_2026-06-14.md docs/superpowers/specs/2026-06-15-forex-edge-research-design.md
git commit -m "feat(forex-edge): complete standalone research harness"
```

The final implementation report must list:

- exact files changed;
- the fresh focused pytest result;
- static boundary-check result;
- whether official live data was actually ingested;
- whether a final holdout edge was actually measured;
- all unverified inputs and remaining evidence flags.
