// AI Chart Review v1 — frontend helper.
//
// Responsibilities:
//   1. Downscale a canvas to <= 1280x720 preserving aspect ratio.
//   2. Encode a canvas to a "data:image/png;base64,..." URL.
//   3. POST { symbol, timeframe, provider, screenshot_base64, screenshot_meta }
//      to /api/ai/chart-review.
//
// SECURITY CONTRACT (spec test F1):
//   The request body sent by this helper contains exactly five keys —
//   symbol, timeframe, provider, screenshot_base64, screenshot_meta.
//   No server-trusted scoring field of any kind may appear in the request.
//   The backend re-runs Engine A from server state and never trusts the
//   browser for diagnostics. See spec section 5.2 and routes_ai_chart_review.

import { apiClient } from './apiClient';
import type {
  AIChartReviewChartSnapshot,
  AIChartReviewRequest,
  AIChartReviewResponse,
  AIChartReviewScreenshotMeta,
} from '@/types/athena';

const ENDPOINT = '/api/ai/chart-review';

export const AI_CHART_REVIEW_DEFAULTS = Object.freeze({
  MAX_WIDTH: 1280,
  MAX_HEIGHT: 720,
});

/**
 * Downscale a source canvas to fit within (maxW, maxH) preserving aspect ratio.
 * If the source already fits, returns the source canvas unchanged.
 */
export function downscaleToCap(
  source: HTMLCanvasElement,
  maxW: number = AI_CHART_REVIEW_DEFAULTS.MAX_WIDTH,
  maxH: number = AI_CHART_REVIEW_DEFAULTS.MAX_HEIGHT,
): HTMLCanvasElement {
  const w = source.width;
  const h = source.height;
  if (w <= maxW && h <= maxH) return source;

  const scale = Math.min(maxW / w, maxH / h);
  const targetW = Math.max(1, Math.round(w * scale));
  const targetH = Math.max(1, Math.round(h * scale));

  const off = document.createElement('canvas');
  off.width = targetW;
  off.height = targetH;
  const ctx = off.getContext('2d');
  if (!ctx) {
    throw new Error('downscaleToCap: 2D context unavailable on offscreen canvas');
  }
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(source, 0, 0, w, h, 0, 0, targetW, targetH);
  return off;
}

/**
 * Encode a canvas to a data URL "data:image/png;base64,...".
 * Resolves with the data URL string or rejects on encoding failure.
 */
export function canvasToDataUrl(canvas: HTMLCanvasElement): Promise<string> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error('canvasToDataUrl: PNG export returned no blob'));
        return;
      }
      const reader = new FileReader();
      reader.onerror = () =>
        reject(new Error('canvasToDataUrl: FileReader failed to read blob'));
      reader.onload = () => {
        const result = reader.result;
        if (typeof result !== 'string') {
          reject(new Error('canvasToDataUrl: FileReader produced non-string result'));
          return;
        }
        resolve(result);
      };
      reader.readAsDataURL(blob);
    }, 'image/png');
  });
}

/**
 * Build a screenshot_meta object. The backend trusts ONLY `native_chart` and
 * `captured_at` for safety logic; the rest is for diagnostics in the persisted
 * review row. All Engine A data comes from the server, never from this object.
 */
export function buildScreenshotMeta(args: {
  width: number;
  height: number;
  chart_timeframe: string;
  overlays: string[];
  visible_range_start?: string;
  visible_range_end?: string;
  chart_provider?: string;
  chart_snapshot?: AIChartReviewChartSnapshot;
  analyze_style?: string;
  signal_style?: string;
  scoring_timeframes?: string[];
  momentum_timeframe?: string;
  regime_timeframe?: string;
  execution_timeframe?: string;
}): AIChartReviewScreenshotMeta {
  const provider = args.chart_provider;
  return {
    width: args.width,
    height: args.height,
    native_chart: true,
    chart_timeframe: args.chart_timeframe,
    overlays: [...args.overlays],
    visible_range_start: args.visible_range_start,
    visible_range_end: args.visible_range_end,
    chart_snapshot: args.chart_snapshot,
    captured_at: new Date().toISOString(),
    ...(provider ? { provider, chart_provider: provider } : {}),
    ...(args.analyze_style ? { analyze_style: args.analyze_style } : {}),
    ...(args.signal_style ? { signal_style: args.signal_style } : {}),
    ...(args.scoring_timeframes?.length ? { scoring_timeframes: [...args.scoring_timeframes] } : {}),
    ...(args.momentum_timeframe ? { momentum_timeframe: args.momentum_timeframe } : {}),
    ...(args.regime_timeframe ? { regime_timeframe: args.regime_timeframe } : {}),
    ...(args.execution_timeframe ? { execution_timeframe: args.execution_timeframe } : {}),
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function normalizeAIChartReviewResponse(
  raw: AIChartReviewResponse,
): AIChartReviewResponse {
  const record = asRecord(raw);
  const summary = record.aiReviewSummary ?? record.ai_review_summary;
  const contextCompleteness =
    record.contextCompleteness ?? record.context_completeness;
  const missingContextDetailed =
    record.missingContextDetailed ?? record.missing_context_detailed;
  const fundingOi = record.fundingOi ?? record.funding_oi;
  const atrDiagnostics = record.atrDiagnostics ?? record.atr_diagnostics;
  const resistanceMap = record.resistanceMap ?? record.resistance_map;
  const engineAVerdictComparison =
    record.engineAVerdictComparison ?? record.engine_a_verdict_comparison;
  const derivativesContext = record.derivativesContext ?? record.derivatives_context;
  const nonVisualContext =
    record.nonVisualContext ??
    record.engineANonVisualContext ??
    record.engine_a_non_visual_context ??
    asRecord(record.engine_a_context).non_visual_context ??
    asRecord(record.engine_a_context).engine_a_non_visual_context;
  const scoreAttribution =
    record.scoreAttribution ??
    record.engineAScoreAttribution ??
    record.engine_a_score_attribution ??
    asRecord(record.engine_a_context).score_attribution;

  return {
    ...raw,
    aiReviewSummary: summary as AIChartReviewResponse['aiReviewSummary'],
    ai_review_summary: summary as AIChartReviewResponse['ai_review_summary'],
    engineAVerdictComparison:
      engineAVerdictComparison as AIChartReviewResponse['engineAVerdictComparison'],
    engine_a_verdict_comparison:
      engineAVerdictComparison as AIChartReviewResponse['engine_a_verdict_comparison'],
    contextCompleteness: contextCompleteness as AIChartReviewResponse['contextCompleteness'],
    missingContextDetailed: missingContextDetailed as AIChartReviewResponse['missingContextDetailed'],
    fundingOi: fundingOi as AIChartReviewResponse['fundingOi'],
    derivativesContext: (derivativesContext ?? fundingOi) as AIChartReviewResponse['derivativesContext'],
    nonVisualContext: nonVisualContext as AIChartReviewResponse['nonVisualContext'],
    engineANonVisualContext: nonVisualContext as AIChartReviewResponse['engineANonVisualContext'],
    scoreAttribution: scoreAttribution as AIChartReviewResponse['scoreAttribution'],
    engineAScoreAttribution: scoreAttribution as AIChartReviewResponse['engineAScoreAttribution'],
    atrDiagnostics: atrDiagnostics as AIChartReviewResponse['atrDiagnostics'],
    resistanceMap: resistanceMap as AIChartReviewResponse['resistanceMap'],
  };
}

/**
 * POST to /api/ai/chart-review. The request body intentionally contains only
 * symbol, timeframe, provider, screenshot_base64, screenshot_meta — no Engine A
 * fields. Throws on non-2xx (apiClient surfaces backend `error` strings).
 */
export async function postChartReview(
  body: AIChartReviewRequest,
): Promise<AIChartReviewResponse> {
  const response = await apiClient.post<AIChartReviewResponse>(
    ENDPOINT,
    body as unknown as Record<string, unknown>,
  );
  return normalizeAIChartReviewResponse(response);
}
