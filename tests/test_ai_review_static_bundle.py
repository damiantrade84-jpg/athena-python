"""Static frontend bundle contract tests for AI chart review panels."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "static" / "react-app" / "app" / "src"


def _active_js_assets() -> list[Path]:
    index_html = ROOT / "static" / "index.html"
    html = index_html.read_text(encoding="utf-8")
    refs = re.findall(r'src="/static/([^"]+\.js)"', html)
    assert refs, "static/index.html does not reference a JS bundle"
    assets = [ROOT / "static" / ref for ref in refs]
    missing = [str(path) for path in assets if not path.exists()]
    assert not missing, f"static/index.html references missing JS asset(s): {missing}"
    return assets


def _active_bundle_refs() -> set[str]:
    index_html = ROOT / "static" / "index.html"
    html = index_html.read_text(encoding="utf-8")
    js_refs = set(re.findall(r'src="/static/([^"]+\.js)"', html))
    css_refs = set(re.findall(r'href="/static/([^"]+\.css)"', html))
    refs = js_refs | css_refs
    assert js_refs, "static/index.html does not reference a JS bundle"
    assert css_refs, "static/index.html does not reference a CSS bundle"
    missing = [ref for ref in refs if not (ROOT / "static" / ref).exists()]
    assert not missing, f"static/index.html references missing asset(s): {missing}"
    return refs


def test_static_assets_contain_only_active_bundle_files():
    active_refs = _active_bundle_refs()
    assets_dir = ROOT / "static" / "assets"
    on_disk = {
        f"assets/{path.name}"
        for path in assets_dir.iterdir()
        if path.is_file() and re.match(r"^index-.*\.(js|css)$", path.name)
    }
    extra = sorted(on_disk - active_refs)
    missing = sorted(active_refs - on_disk)
    assert not extra and not missing, (
        "static/assets must contain only the hashed bundles referenced by static/index.html; "
        f"extra={extra}, missing={missing}"
    )


def test_active_bundle_contains_ai_review_summary_and_context_markers():
    text = "\n".join(path.read_text(encoding="utf-8") for path in _active_js_assets())
    assert "Review summary" in text
    assert "Context completeness" in text
    assert "aiReviewSummary" in text
    assert "contextCompleteness" in text
    assert "Engine A vs AI verdict" in text
    assert "engineAVerdictComparison" in text


def test_frontend_normalizes_camel_and_snake_review_keys_once():
    text = (FRONTEND_SRC / "lib" / "aiChartReview.ts").read_text(encoding="utf-8")
    assert "normalizeAIChartReviewResponse" in text
    assert "record.aiReviewSummary ?? record.ai_review_summary" in text
    assert "record.contextCompleteness ?? record.context_completeness" in text
    assert "record.missingContextDetailed ?? record.missing_context_detailed" in text
    assert "record.fundingOi ?? record.funding_oi" in text
    assert "record.atrDiagnostics ?? record.atr_diagnostics" in text
    assert "record.resistanceMap ?? record.resistance_map" in text
    assert "record.engineAVerdictComparison ?? record.engine_a_verdict_comparison" in text


def test_frontend_renders_summary_and_context_before_detail_rows():
    text = (FRONTEND_SRC / "components" / "athena" / "AIReviewCard.tsx").read_text(
        encoding="utf-8"
    )
    summary_idx = text.index("<AIReviewSummaryStrip")
    verdict_idx = text.index("<AIReviewEngineAVerdictPanel")
    context_idx = text.index("<AIReviewContextCompletenessPanel")
    details_idx = text.index('label="Visual confirmation"')
    assert summary_idx < details_idx
    assert verdict_idx < details_idx
    assert context_idx < details_idx
    assert summary_idx < verdict_idx
