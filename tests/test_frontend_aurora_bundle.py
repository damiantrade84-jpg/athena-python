"""Production frontend bundle contract tests for Aurora redesign."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
INDEX_HTML = STATIC / "index.html"
ASSETS = STATIC / "assets"


def _read_index_html() -> str:
    assert INDEX_HTML.is_file(), "static/index.html is missing"
    return INDEX_HTML.read_text(encoding="utf-8")


def _active_bundle_refs(html: str) -> tuple[list[str], list[str]]:
    js_refs = re.findall(r'src="/static/assets/([^"]+\.js)"', html)
    css_refs = re.findall(r'href="/static/assets/([^"]+\.css)"', html)
    assert js_refs, "static/index.html does not reference a JS bundle"
    assert css_refs, "static/index.html does not reference a CSS bundle"
    for ref in js_refs + css_refs:
        path = ASSETS / ref
        assert path.is_file(), f"static/index.html references missing asset: assets/{ref}"
    return js_refs, css_refs


def _bundle_text(refs: list[str]) -> str:
    return "\n".join((ASSETS / ref).read_text(encoding="utf-8") for ref in refs)


def _lazy_chunks_referenced_by_main(js_text: str) -> set[str]:
    chunks: set[str] = set()
    for match in re.finditer(r"([A-Za-z0-9_-]+-[A-Za-z0-9_-]+\.js)", js_text):
        candidate = match.group(1)
        if not candidate.startswith("index-"):
            chunks.add(candidate)
    return chunks


def test_index_html_has_build_meta_and_active_assets():
    html = _read_index_html()
    meta = re.search(r'<meta name="athena-frontend-build" content="([^"]+)"', html)
    assert meta, "athena-frontend-build meta tag missing"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T", meta.group(1)), "build meta is not ISO timestamp"
    _active_bundle_refs(html)


def test_active_css_includes_aurora_design_system_markers():
    html = _read_index_html()
    _, css_refs = _active_bundle_refs(html)
    css = _bundle_text(css_refs)
    for marker in ("258 90% 68", "app-shell-bg", "panel-glass", "surface-glass", "panel-header-title"):
        assert marker in css, f"production CSS missing Aurora marker: {marker}"


def test_active_js_includes_chart_redesign_markers():
    html = _read_index_html()
    js_refs, _ = _active_bundle_refs(html)
    js = _bundle_text(js_refs)
    for marker in ("EquityAreaChart", "dashEquityStroke", "stat-card-top-line"):
        assert marker in js, f"production JS missing redesign marker: {marker}"


def test_only_active_index_bundles_on_disk():
    html = _read_index_html()
    js_refs, css_refs = _active_bundle_refs(html)
    active = set(js_refs + css_refs)
    on_disk = {
        path.name
        for path in ASSETS.iterdir()
        if path.is_file() and re.match(r"^index-.*\.(js|css)$", path.name)
    }
    extra = sorted(on_disk - active)
    missing = sorted(active - on_disk)
    assert not extra and not missing, (
        "static/assets must contain only index bundles referenced by static/index.html; "
        f"extra={extra}, missing={missing}"
    )


def test_lazy_chunks_match_main_bundle_references():
    html = _read_index_html()
    js_refs, _ = _active_bundle_refs(html)
    js = _bundle_text(js_refs)
    referenced = _lazy_chunks_referenced_by_main(js)

    for chunk in referenced:
        assert (ASSETS / chunk).is_file(), f"main bundle references missing lazy chunk: {chunk}"

    on_disk_lazy = [
        path.name
        for path in ASSETS.iterdir()
        if path.is_file() and re.match(r"^(?!index-)[A-Za-z0-9_-]+-[A-Za-z0-9_-]+\.js$", path.name)
    ]
    orphans = sorted(set(on_disk_lazy) - referenced)
    assert not orphans, f"orphaned lazy chunk(s) on disk: {orphans}"
