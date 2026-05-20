type CandlePayload = {
  candles: unknown[];
  candles_d1?: unknown[];
  candles_h1?: unknown[];
  chart_generated_at: string;
  latest_candle_ts?: string | number;
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

export async function fetchVisionCandlePayload(symbol: string): Promise<CandlePayload> {
  const [d1, h4, h1] = await Promise.all([
    fetchCandles(symbol, 'D1'),
    fetchCandles(symbol, 'H4'),
    fetchCandles(symbol, 'H1'),
  ]);

  if (h4.length === 0) {
    throw new Error(`no H4 candles for ${symbol}`);
  }

  const authoritativeCandles = h1.length > 0 ? h1 : h4;
  const payload: CandlePayload = {
    candles: h4,
    chart_generated_at: new Date().toISOString(),
    latest_candle_ts: candleTimestamp(authoritativeCandles[authoritativeCandles.length - 1]),
  };
  if (d1.length > 0) payload.candles_d1 = d1;
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
