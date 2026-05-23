// Engine D scalp chart review — frontend helper.
//
// SECURITY CONTRACT:
//   Request body contains exactly five keys:
//   symbol, timeframe, provider, screenshot_base64, screenshot_meta.

import { apiClient } from './apiClient';
import {
  canvasToDataUrl,
  downscaleToCap,
  AI_CHART_REVIEW_DEFAULTS,
} from './aiChartReview';
import type {
  ScalpAIChartReviewRequest,
  ScalpAIChartReviewResponse,
  ScalpAIChartReviewScreenshotMeta,
} from '@/types/athena';

export { downscaleToCap, canvasToDataUrl, AI_CHART_REVIEW_DEFAULTS };

const ENDPOINT = '/api/ai/scalp-chart-review';

export function buildScalpScreenshotMeta(args: {
  width: number;
  height: number;
  chart_timeframe: string;
  overlays: string[];
  captured_at?: string;
  execution_tf?: string;
  visible_range_start?: string;
  visible_range_end?: string;
  chart_provider?: string;
  chart_snapshot?: import('@/types/athena').ScalpChartSnapshot;
  rendered_layers?: Record<string, boolean>;
  missing_layers?: string[];
  source_fidelity_label?: string;
}): ScalpAIChartReviewScreenshotMeta {
  return {
    width: args.width,
    height: args.height,
    native_chart: true,
    chart_timeframe: args.chart_timeframe,
    overlays: [...args.overlays],
    captured_at: args.captured_at ?? new Date().toISOString(),
    execution_tf: args.execution_tf,
    visible_range_start: args.visible_range_start,
    visible_range_end: args.visible_range_end,
    ...(args.chart_provider ? { chart_provider: args.chart_provider } : {}),
    ...(args.chart_snapshot ? { chart_snapshot: args.chart_snapshot } : {}),
    ...(args.rendered_layers ? { rendered_layers: args.rendered_layers } : {}),
    ...(args.missing_layers?.length ? { missing_layers: [...args.missing_layers] } : {}),
    ...(args.source_fidelity_label ? { source_fidelity_label: args.source_fidelity_label } : {}),
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function normalizeScalpAIChartReviewResponse(
  raw: ScalpAIChartReviewResponse,
): ScalpAIChartReviewResponse {
  const record = asRecord(raw);
  const summary = record.aiReviewSummary ?? record.ai_review_summary;
  const verdictComparison =
    record.scalpVerdictComparison ?? record.scalp_verdict_comparison;
  const contextCompleteness =
    record.contextCompleteness ?? record.context_completeness;
  const engineDContext = record.engine_d_context ?? record.engineDContext;

  return {
    ...raw,
    aiReviewSummary: summary as ScalpAIChartReviewResponse['aiReviewSummary'],
    ai_review_summary: summary as ScalpAIChartReviewResponse['ai_review_summary'],
    scalpVerdictComparison:
      verdictComparison as ScalpAIChartReviewResponse['scalpVerdictComparison'],
    scalp_verdict_comparison:
      verdictComparison as ScalpAIChartReviewResponse['scalp_verdict_comparison'],
    contextCompleteness:
      contextCompleteness as ScalpAIChartReviewResponse['contextCompleteness'],
    context_completeness:
      contextCompleteness as ScalpAIChartReviewResponse['context_completeness'],
    engine_d_context: engineDContext as ScalpAIChartReviewResponse['engine_d_context'],
    engineDContext: engineDContext as ScalpAIChartReviewResponse['engineDContext'],
  };
}

export async function postScalpChartReview(
  body: ScalpAIChartReviewRequest,
): Promise<ScalpAIChartReviewResponse> {
  const response = await apiClient.post<ScalpAIChartReviewResponse>(
    ENDPOINT,
    body as unknown as Record<string, unknown>,
  );
  return normalizeScalpAIChartReviewResponse(response);
}
