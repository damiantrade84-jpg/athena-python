from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import requests

from athena_research.forex_edge.config import load_config, redact_secrets
from athena_research.forex_edge.models import (
    BlockedDataError,
    InvalidResearchInputError,
)
from athena_research.forex_edge.runner import RunRequest, run_research
from athena_research.forex_edge.sources.bis import fetch_bis_reer, parse_bis_reer_csv
from athena_research.forex_edge.sources.cftc import (
    fetch_cftc_year,
    normalize_cftc_frame,
    parse_cftc_zip,
)
from athena_research.forex_edge.sources.common import requests_get
from athena_research.forex_edge.sources.dukascopy import (
    fetch_bi5_hour,
    import_dukascopy,
    parse_bi5_ticks,
    ticks_to_m5_bars,
)
from athena_research.forex_edge.sources.fred import (
    fetch_fred_series,
    normalize_fred_observations,
)
from athena_research.forex_edge.store import ResearchStore
from athena_research.reproducibility import hash_stable_json


EXIT_COMPLETED = 0
EXIT_NO_EDGE = 2
EXIT_BLOCKED = 3
EXIT_INVALID = 4
EXIT_PROVIDER = 5


def redact_text(text: str) -> str:
    return str(redact_secrets(text))


def _manifest_map(values: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in values or []:
        if "=" not in item:
            raise ValueError("manifest must be source=id")
        key, value = item.split("=", 1)
        result[key] = value
    return result


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidResearchInputError(f"{name} must be a mapping")
    return value


def _manifest_path(store: ResearchStore, manifest: object) -> str:
    dataset = getattr(manifest, "dataset")
    key = getattr(manifest, "key")
    version = getattr(manifest, "version")
    return str(store.root / "manifests" / dataset / key / f"{version}.json")


def _cmd_ingest_fred(
    args: argparse.Namespace,
    config: Mapping[str, Any],
) -> dict[str, object]:
    source_cfg = _require_mapping(
        _require_mapping(config.get("sources"), "sources").get("fred"),
        "sources.fred",
    )
    api_base = str(source_cfg.get("api_base", "")).strip()
    api_key_env = str(source_cfg.get("api_key_env", "")).strip()
    if not api_base or not api_key_env:
        raise InvalidResearchInputError("FRED api_base/api_key_env required")

    dataset = str(args.dataset)
    series_cfg_name = "spot_series" if dataset == "spot" else "rate_series"
    series_cfg = _require_mapping(source_cfg.get(series_cfg_name), series_cfg_name)
    requested = set(args.series_id or [])
    store = ResearchStore(args.store_root or None)
    manifests = []

    for currency, raw_spec in sorted(series_cfg.items()):
        if dataset == "spot":
            spec = _require_mapping(raw_spec, f"{series_cfg_name}.{currency}")
            series_id = str(spec.get("series_id", "")).strip()
            usd_per_currency = bool(spec.get("usd_per_currency"))
            kind = "spot"
            unit = "FRED exchange rate"
        else:
            series_id = str(raw_spec).strip()
            usd_per_currency = None
            kind = "rate"
            unit = "Percent"
        if not series_id or (requested and series_id not in requested):
            continue

        try:
            raw = fetch_fred_series(
                series_id,
                api_base=api_base,
                api_key_env=api_key_env,
                observation_start=args.start or "2000-01-01",
                observation_end=args.end or None,
            )
        except RuntimeError as exc:
            raise requests.RequestException(str(exc)) from exc
        raw_artifact = store.write_raw("FRED", f"{dataset}_{currency}", raw)
        payload = json.loads(raw.decode("utf-8"))
        frame = normalize_fred_observations(
            payload,
            series_id=series_id,
            currency=str(currency),
            kind=kind,
            unit=unit,
            usd_per_currency=usd_per_currency,
        )
        manifest = store.write_normalized(
            dataset=dataset,
            key=str(currency),
            frame=frame,
            source="FRED",
            source_url=f"{api_base.rstrip('/')}/series/observations?series_id={series_id}",
            raw_hashes=(raw_artifact.sha256,),
            metadata={
                "config_hash": hash_stable_json(config),
                "series_id": series_id,
                "kind": kind,
                "unit": unit,
                "usd_per_currency": usd_per_currency,
            },
        )
        item = manifest.to_dict()
        item["manifest_path"] = _manifest_path(store, manifest)
        manifests.append(item)

    if not manifests:
        raise InvalidResearchInputError("no configured FRED series matched request")
    return {
        "status": "COMPLETED",
        "dataset": dataset,
        "manifests": manifests,
    }


def _cmd_ingest_bis(
    args: argparse.Namespace,
    config: Mapping[str, Any],
) -> dict[str, object]:
    source_cfg = _require_mapping(
        _require_mapping(config.get("sources"), "sources").get("bis"),
        "sources.bis",
    )
    base_url = str(source_cfg.get("base_url", "")).strip()
    configured = _require_mapping(source_cfg.get("reer_series"), "reer_series")
    requested = set(args.series_key or [])
    store = ResearchStore(args.store_root or None)
    manifests = []
    for currency, series_key_raw in sorted(configured.items()):
        series_key = str(series_key_raw).strip()
        if not series_key or (requested and series_key not in requested):
            continue
        url, raw = fetch_bis_reer(
            base_url,
            series_key,
            start=args.start or "2000-01",
            end=args.end or "",
        )
        raw_artifact = store.write_raw("BIS", f"reer_{currency}", raw)
        frame = parse_bis_reer_csv(raw, currency=str(currency), series_key=series_key)
        manifest = store.write_normalized(
            dataset="reer",
            key=str(currency),
            frame=frame,
            source="BIS",
            source_url=url,
            raw_hashes=(raw_artifact.sha256,),
            metadata={
                "config_hash": hash_stable_json(config),
                "series_id": series_key,
                "revision_history_verified": False,
            },
        )
        item = manifest.to_dict()
        item["manifest_path"] = _manifest_path(store, manifest)
        manifests.append(item)
    if not manifests:
        raise InvalidResearchInputError("no configured BIS series matched request")
    return {"status": "COMPLETED", "dataset": "reer", "manifests": manifests}


def _cmd_ingest_cftc(
    args: argparse.Namespace,
    config: Mapping[str, Any],
) -> dict[str, object]:
    source_cfg = _require_mapping(
        _require_mapping(config.get("sources"), "sources").get("cftc"),
        "sources.cftc",
    )
    url_template = str(source_cfg.get("historical_url", "")).strip()
    mappings = {
        str(key): str(value)
        for key, value in _require_mapping(source_cfg.get("mappings"), "mappings").items()
    }
    years = sorted(set(args.year or []))
    if not years:
        raise InvalidResearchInputError("at least one CFTC --year is required")
    store = ResearchStore(args.store_root or None)
    frames = []
    raw_hashes = []
    urls = []
    for year in years:
        url, raw = fetch_cftc_year(url_template, int(year))
        raw_artifact = store.write_raw("CFTC", f"cot_{year}", raw)
        raw_hashes.append(raw_artifact.sha256)
        urls.append(url)
        frames.append(parse_cftc_zip(raw))
    normalized = normalize_cftc_frame(pd.concat(frames, ignore_index=True), mappings)
    manifest = store.write_normalized(
        dataset="cot",
        key="noncommercial",
        frame=normalized,
        source="CFTC",
        source_url=",".join(urls),
        raw_hashes=tuple(raw_hashes),
        metadata={
            "config_hash": hash_stable_json(config),
            "years": years,
        },
    )
    item = manifest.to_dict()
    item["manifest_path"] = _manifest_path(store, manifest)
    return {"status": "COMPLETED", "dataset": "cot", "manifests": [item]}


def _cmd_import_dukascopy(
    args: argparse.Namespace,
    config: Mapping[str, Any],
) -> dict[str, object]:
    symbol = args.symbol or Path(args.file).stem.split("_")[0].upper()
    frame = import_dukascopy(
        Path(args.file),
        symbol=symbol,
        timezone_name=args.timezone,
        schema=args.schema,
        delimiter=args.delimiter,
    )
    raw = Path(args.file).read_bytes()
    store = ResearchStore(args.store_root or None)
    raw_artifact = store.write_raw("DUKASCOPY", f"m5_{symbol}", raw)
    manifest = store.write_normalized(
        dataset="m5",
        key=symbol,
        frame=frame,
        source="DUKASCOPY",
        source_url=str(Path(args.file).resolve()),
        raw_hashes=(raw_artifact.sha256,),
        metadata={
            "config_hash": hash_stable_json(config),
            "symbol": symbol,
            "schema": args.schema,
            "timezone": args.timezone,
        },
    )
    item = manifest.to_dict()
    item["manifest_path"] = _manifest_path(store, manifest)
    return {"status": "COMPLETED", "dataset": "m5", "manifests": [item]}


def _cmd_download_dukascopy(
    args: argparse.Namespace,
    config: Mapping[str, Any],
) -> dict[str, object]:
    symbol = str(args.symbol).upper().strip()
    if not symbol:
        raise InvalidResearchInputError("--symbol is required")
    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")
    if end < start:
        raise InvalidResearchInputError("--end must be >= --start")
    allowed_hours = (
        {int(hour) for hour in args.hour}
        if args.hour
        else set(range(24))
    )
    store = ResearchStore(args.store_root or None)
    raw_hashes = []
    urls = []
    tick_frames = []
    for hour in pd.date_range(start.floor("h"), end.ceil("h"), freq="h", tz="UTC"):
        if int(hour.hour) not in allowed_hours:
            continue
        try:
            url, raw = fetch_bi5_hour(symbol, hour, http_get=requests_get)
        except RuntimeError:
            continue
        raw_artifact = store.write_raw(
            "DUKASCOPY",
            f"bi5_{symbol}_{hour:%Y%m%d%H}",
            raw,
        )
        raw_hashes.append(raw_artifact.sha256)
        urls.append(url)
        try:
            ticks = parse_bi5_ticks(raw, symbol=symbol, hour_start=hour)
        except ValueError:
            continue
        if not ticks.empty:
            tick_frames.append(ticks)
    if not tick_frames:
        raise BlockedDataError("NO_EXECUTABLE_QUOTE")
    ticks = pd.concat(tick_frames, ignore_index=True)
    ticks = ticks[
        (ticks["timestamp"] >= start)
        & (ticks["timestamp"] <= end + pd.Timedelta(minutes=5))
    ].copy()
    frame = ticks_to_m5_bars(ticks, symbol=symbol)
    manifest = store.write_normalized(
        dataset="m5",
        key=symbol,
        frame=frame,
        source="DUKASCOPY",
        source_url=",".join(urls),
        raw_hashes=tuple(raw_hashes),
        metadata={
            "config_hash": hash_stable_json(config),
            "symbol": symbol,
            "schema": "bi5_tick_bid_ask",
            "hours": sorted(allowed_hours),
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
    )
    item = manifest.to_dict()
    item["manifest_path"] = _manifest_path(store, manifest)
    return {"status": "COMPLETED", "dataset": "m5", "manifests": [item]}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forex_edge_cli",
        description="Standalone research-only forex edge studies",
    )
    parser.add_argument("--config", default="configs/forex_edge_research.yaml")
    parser.add_argument("--store-root", default="")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest_bis = sub.add_parser("ingest-bis")
    ingest_bis.add_argument("--start", default="")
    ingest_bis.add_argument("--end", default="")
    ingest_bis.add_argument("--series-key", action="append")

    ingest_cftc = sub.add_parser("ingest-cftc")
    ingest_cftc.add_argument("--year", action="append", type=int)

    ingest_fred = sub.add_parser("ingest-fred")
    ingest_fred.add_argument("--dataset", choices=["spot", "rates"], default="spot")
    ingest_fred.add_argument("--start", default="")
    ingest_fred.add_argument("--end", default="")
    ingest_fred.add_argument("--series-id", action="append")

    dukascopy = sub.add_parser("import-dukascopy")
    dukascopy.add_argument("--file", required=True)
    dukascopy.add_argument("--symbol", default="")
    dukascopy.add_argument("--timezone", default="")
    dukascopy.add_argument("--schema", default="")
    dukascopy.add_argument("--delimiter", default=",")

    download_dukascopy = sub.add_parser("download-dukascopy")
    download_dukascopy.add_argument("--symbol", required=True)
    download_dukascopy.add_argument("--start", required=True)
    download_dukascopy.add_argument("--end", required=True)
    download_dukascopy.add_argument("--hour", action="append", type=int)

    for command in ("quality-report", "run-portfolio", "run-fixing", "run-both"):
        child = sub.add_parser(command)
        child.add_argument("--manifest", action="append")
        child.add_argument("--out", default="")
    return parser


def _dispatch(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.config)
    if args.command == "ingest-fred":
        return _cmd_ingest_fred(args, config)
    if args.command == "ingest-bis":
        return _cmd_ingest_bis(args, config)
    if args.command == "ingest-cftc":
        return _cmd_ingest_cftc(args, config)
    if args.command == "import-dukascopy":
        return _cmd_import_dukascopy(args, config)
    if args.command == "download-dukascopy":
        return _cmd_download_dukascopy(args, config)
    lane = {
        "quality-report": "both",
        "run-portfolio": "portfolio",
        "run-fixing": "fixing",
        "run-both": "both",
    }[args.command]
    out = Path(args.out) if args.out else Path("athena_research/forex_edge/output")
    paths = run_research(
        RunRequest(
            lane=lane,
            dataset_manifests=_manifest_map(args.manifest),
            output_root=out,
        ),
        config,
    )
    return {"status": "COMPLETED", "artifacts": [str(path) for path in paths]}


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
        print(
            json.dumps(
                {"status": "INVALID_INPUT", "reason": redact_text(str(exc))}
            )
        )
        return EXIT_INVALID
    except (requests.RequestException, RuntimeError) as exc:
        print(
            json.dumps(
                {"status": "PROVIDER_FAILURE", "reason": redact_text(str(exc))}
            )
        )
        return EXIT_PROVIDER


if __name__ == "__main__":
    sys.exit(main())
