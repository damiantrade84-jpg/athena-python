"""Engine A V4 commodity data expansion — read-only provider audit helpers.

Import submodules directly (for example ``commodity_data_audit.registry``).
This package intentionally avoids eager imports so research-only freeze tools
do not load production config or execution modules at import time.
"""

__all__ = [
    "INDEPENDENT_CLUSTER_MIN",
    "audit_candle_series",
    "build_acquisition_manifest",
    "build_coverage_matrix",
    "independent_cluster_count",
    "load_commodity_pairs",
    "modelled_costs_for_instrument",
    "recommended_acquisition_batch",
    "resolve_provider_aliases",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "INDEPENDENT_CLUSTER_MIN": (
        "athena_research.commodity_data_audit.registry",
        "INDEPENDENT_CLUSTER_MIN",
    ),
    "audit_candle_series": (
        "athena_research.commodity_data_audit.quality",
        "audit_candle_series",
    ),
    "build_acquisition_manifest": (
        "athena_research.commodity_data_audit.manifest",
        "build_acquisition_manifest",
    ),
    "build_coverage_matrix": (
        "athena_research.commodity_data_audit.registry",
        "build_coverage_matrix",
    ),
    "independent_cluster_count": (
        "athena_research.commodity_data_audit.registry",
        "independent_cluster_count",
    ),
    "load_commodity_pairs": (
        "athena_research.commodity_data_audit.registry",
        "load_commodity_pairs",
    ),
    "modelled_costs_for_instrument": (
        "athena_research.commodity_data_audit.costs",
        "modelled_costs_for_instrument",
    ),
    "recommended_acquisition_batch": (
        "athena_research.commodity_data_audit.registry",
        "recommended_acquisition_batch",
    ),
    "resolve_provider_aliases": (
        "athena_research.commodity_data_audit.registry",
        "resolve_provider_aliases",
    ),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = __import__(module_name, fromlist=[attr_name])
    return getattr(module, attr_name)
