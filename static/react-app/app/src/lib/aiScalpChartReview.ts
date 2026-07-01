// Engine D scalp chart review — frontend helper.
//
// SECURITY CONTRACT:
//   Request body contains exactly five keys:
//   symbol, timeframe, provider, screenshot_base64, screenshot_meta.

import { apiClient } from './apiClient';
import { streamSse, type SseHandlers } from './sseStream';
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

function mergeReviewDiagnostics(
  record: Record<string, unknown>,
  summary: unknown,
): ScalpAIChartReviewResponse['aiReviewSummary'] {
  const base = asRecord(summary);
  const selected =
    record.selectedProvider ??
    record.selected_provider ??
    base.selectedProvider;
  const failure =
    record.providerFailure ??
    record.provider_failure ??
    base.providerFailure ??
    base.provider_failure;
  const fallbackUsed =
    record.fallbackUsed ??
    record.fallback_used ??
    base.fallbackUsed;
  return {
    ...base,
    ...(selected ? { selectedProvider: String(selected) } : {}),
    ...(failure && typeof failure === 'object'
      ? { providerFailure: failure, provider_failure: failure }
      : {}),
    ...(fallbackUsed != null ? { fallbackUsed: Boolean(fallbackUsed) } : {}),
  } as ScalpAIChartReviewResponse['aiReviewSummary'];
}

export function normalizeScalpAIChartReviewResponse(
  raw: ScalpAIChartReviewResponse,
): ScalpAIChartReviewResponse {
  const record = asRecord(raw);
  const summary = mergeReviewDiagnostics(
    record,
    record.aiReviewSummary ?? record.ai_review_summary,
  );
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

const STREAM_ENDPOINT = '/api/ai/scalp-chart-review/stream';

export interface StreamScalpChartReviewHandlers {
  onStart?: SseHandlers['onStart'];
  onToken?: SseHandlers['onToken'];
  onError?: SseHandlers['onError'];
  onReview?: (response: ScalpAIChartReviewResponse) => void;
}

/**
 * Stream the scalp chart review narrative over SSE. Falls back to the
 * non-streaming `postScalpChartReview` on HTTP 410 (streaming disabled).
 * `onReview` is invoked exactly once with the normalized full response.
 */
export async function streamScalpChartReview(
  body: ScalpAIChartReviewRequest,
  handlers: StreamScalpChartReviewHandlers,
  signal?: AbortSignal,
): Promise<{ fallback: boolean }> {
  const result = await streamSse(
    STREAM_ENDPOINT,
    body as unknown as Record<string, unknown>,
    {
      onStart: handlers.onStart,
      onToken: handlers.onToken,
      onError: handlers.onError,
      onDone: (data) => {
        const payload = (data || {}) as { full_payload?: ScalpAIChartReviewResponse };
        if (payload.full_payload) {
          handlers.onReview?.(normalizeScalpAIChartReviewResponse(payload.full_payload));
        }
      },
    },
    signal,
  );
  if (result.fallback) {
    const response = await postScalpChartReview(body);
    handlers.onReview?.(response);
    return { fallback: true };
  }
  return { fallback: false };
}
