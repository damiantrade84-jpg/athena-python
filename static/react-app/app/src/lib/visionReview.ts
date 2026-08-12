type CandlePayload = {
  candles: unknown[];
  candles_d1?: unknown[];
  candles_h4?: unknown[];
  candles_h1?: unknown[];
  chart_generated_at: string;
  latest_candle_ts?: string | number;
};

type VisionSignalLike = {
  setupTf?: string;
  triggerTf?: string;
  entryTimeframe?: string | null;
  executionTf?: string;
  structureTf?: string;
  timeframe?: string;
  style?: string;
  horizon?: string;
  requestedStyle?: string;
  requested_style?: string;
  timeframe_route?: { autoSelectTf?: string };
  naked_data?: unknown;
  engine_b?: unknown;
};

export type VisionReviewImageTimeframes = {
  structureTf: string;
  entryTf: string;
};

async function fetchCandles(symbol: string, tf: string, limit = 300): Promise<unknown[]> {
  const res = await fetch(`/api/candles?symbol=${encodeURIComponent(symbol)}&tf=${encodeURIComponent(tf)}&limit=${limit}`);
  const json = await res.json();
  if (!res.ok || !Array.isArray(json?.candles)) return [];
  return json.candles;
}

function candleTimestamp(candle: unknown): string | number | undefined {
  if (!candle || typeof candle !== 'object') return undefined;
  const row = candle as Record<string, unknown>;
  const ts = row.t ?? row.time ?? row.ts;
  return typeof ts === 'string' || typeof ts === 'number' ? ts : undefined;
}

export function preferredVisionReviewTf(signal: VisionSignalLike): string {
  const nestedEngineB = signal.naked_data || signal.engine_b;
  const engineB = nestedEngineB && typeof nestedEngineB === 'object'
    ? nestedEngineB as Record<string, unknown>
    : {};
  const route = signal.timeframe_route?.autoSelectTf;
  const direct = signal.setupTf
    || (engineB.setup_tf as string | undefined)
    || signal.triggerTf
    || (engineB.trigger_timeframe_actual as string | undefined)
    || (engineB.entry_tf as string | undefined)
    || signal.entryTimeframe
    || signal.executionTf
    || signal.structureTf
    || signal.timeframe
    || route;
  if (direct && typeof direct === 'string') return direct.toUpperCase();

  const style = String(
    signal.style || signal.requestedStyle || signal.requested_style || signal.horizon || '',
  ).toLowerCase();
  if (style === 'scalp') return 'M15';
  if (style === 'intraday') return 'M30';
  return 'H4';
}

export function visionReviewImageTimeframes(signal: VisionSignalLike): VisionReviewImageTimeframes {
  const nestedEngineB = signal.naked_data || signal.engine_b;
  const engineB = nestedEngineB && typeof nestedEngineB === 'object'
    ? nestedEngineB as Record<string, unknown>
    : {};
  const structure = signal.structureTf
    || (engineB.structure_timeframe as string | undefined)
    || (engineB.zone_tf as string | undefined)
    || signal.setupTf
    || (engineB.setup_tf as string | undefined)
    || signal.timeframe;
  const entry = signal.triggerTf
    || (engineB.trigger_timeframe_actual as string | undefined)
    || (engineB.trigger_timeframe as string | undefined)
    || (engineB.entry_tf as string | undefined)
    || signal.entryTimeframe
    || signal.executionTf;

  return {
    structureTf: String(structure || '').toUpperCase(),
    entryTf: String(entry || '').toUpperCase(),
  };
}

export async function fetchVisionCandlePayload(
  symbol: string,
  primaryTf = 'H4',
): Promise<CandlePayload> {
  const normalizedPrimaryTf = String(primaryTf || 'H4').toUpperCase();
  const needsExtraPrimary = !['D1', 'H4', 'H1'].includes(normalizedPrimaryTf);
  const [d1, h4, h1, extraPrimary] = await Promise.all([
    fetchCandles(symbol, 'D1'),
    fetchCandles(symbol, 'H4'),
    fetchCandles(symbol, 'H1'),
    needsExtraPrimary ? fetchCandles(symbol, normalizedPrimaryTf) : Promise.resolve([]),
  ]);

  if (h4.length === 0) {
    throw new Error(`no H4 candles for ${symbol}`);
  }

  const primary = normalizedPrimaryTf === 'D1'
    ? d1
    : normalizedPrimaryTf === 'H1'
      ? h1
      : normalizedPrimaryTf === 'H4'
        ? h4
        : extraPrimary;
  if (primary.length === 0) {
    throw new Error(`no ${normalizedPrimaryTf} candles for ${symbol}`);
  }

  const payload: CandlePayload = {
    candles: primary,
    chart_generated_at: new Date().toISOString(),
    latest_candle_ts: candleTimestamp(primary[primary.length - 1]),
  };
  if (d1.length > 0) payload.candles_d1 = d1;
  if (h4.length > 0) payload.candles_h4 = h4;
  if (h1.length > 0) payload.candles_h1 = h1;
  return payload;
}

export function chartImageSrc(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const src = raw.trim();
  if (!src) return null;
  if (/^(data:image\/|https?:\/\/|blob:|\/)/i.test(src)) return src;
  return `data:image/png;base64,${src}`;
}
