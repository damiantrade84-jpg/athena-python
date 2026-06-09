import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPaneApi,
  type IPriceLine,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type ISeriesPrimitive,
  type CandlestickData,
  type HistogramData,
  type LineData,
  type SeriesAttachedParameter,
  type SeriesMarker,
  type UTCTimestamp,
  type Time,
} from 'lightweight-charts';
import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import { BarChart3, Camera, Layers, Play, Sparkles, SlidersHorizontal } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { useLivePrices } from '@/hooks/useLivePrices';
import { useApiPoll } from '@/hooks/useApiData';
import { useStore } from '@/hooks/useStore';
import { apiClient } from '@/lib/apiClient';
import {
  isFrontendDebugVisible,
  resolveAtrProvenanceRows,
  resolveCandleFetchMetaRows,
  resolveDirectionalRampDisplay,
  resolveFeedAddonDisplay,
  resolveFrontendBuildLabel,
  resolveNumericDisplay,
  resolveTrendCoherenceRows,
  type DiagnosticDisplay,
} from '@/lib/engineADiagnosticsDisplay';
import { fmtNum, toNum } from '@/lib/utils';
import {
  AI_CHART_REVIEW_DEFAULTS,
  buildScreenshotMeta,
  canvasToDataUrl,
  downscaleToCap,
  postChartReview,
} from '@/lib/aiChartReview';
import VolumeModeField from '@/components/execution/VolumeModeField';
import ExitModeField from '@/components/execution/ExitModeField';
import { useExitModeState } from '@/hooks/useExitModeState';
import {
  fetchRiskPreview,
  formatRiskPreviewLine,
  useExecutionVolumeState,
} from '@/hooks/useExecutionVolumeState';
import {
  buildQuickExecutePayload,
  evaluateTvChartExecuteBlock,
  aiReviewWarningForExecute,
} from '@/lib/manualExecuteHelpers';
import { runnerBadgeClass, runnerBadgeLabel } from '@/lib/suggestedTradeRunnerDisplay';
import { useSuggestedTradeRunnerStatus } from '@/hooks/useSuggestedTradeRunnerStatus';
import AIReviewCard from '@/components/athena/AIReviewCard';
import { AIReviewProviderToggle } from '@/components/athena/AIReviewProviderToggle';
import ChartFeedHeaderChips, { type ChartFeedHeaderChipSpec } from '@/components/athena/ChartFeedHeaderChips';
import CompactSuggestedWatchStatus from '@/components/athena/CompactSuggestedWatchStatus';
import { ChartRightEdgeLabelPrimitive } from '@/lib/chartRightEdgeLabelPrimitive';
import {
  labelPriorityForText,
  RIGHT_EDGE_LABEL_PRIORITY,
  type RightEdgeLabel,
} from '@/lib/chartRightEdgeLabels';
import type {
  AIChartReviewChartSnapshot,
  AIChartReviewResponse,
  EngineASignal,
  SuggestedTradePlan,
  SuggestedTradeWatch,
  TimeframeRoute,
} from '@/types/athena';

const TIMEFRAMES = ['1', '5', '15', '30', '60', '240', 'D', 'W'];

// Map TradingView-style interval codes to the backend /api/candles ?tf= values.
const TF_BACKEND_MAP: Record<string, string> = {
  '1': 'M1',
  '5': 'M5',
  '15': 'M15',
  '30': 'M30',
  '60': 'H1',
  '240': 'H4',
  D: 'D1',
  W: 'W1',
};

const TF_UI_CODE_MAP: Record<string, string> = {
  M1: '1',
  M5: '5',
  M15: '15',
  M30: '30',
  H1: '60',
  H4: '240',
  D1: 'D',
  W1: 'W',
};

// Indicator colors — chosen to be distinct and high-contrast on the dark chart background.
const INDICATOR_COLORS = {
  ema20: 'hsl(200, 95%, 55%)',
  ema21: 'hsl(190, 95%, 58%)',
  ema50: 'hsl(45, 95%, 58%)',
  ema200: 'hsl(280, 80%, 65%)',
  dema200: 'hsl(15, 90%, 60%)',
  vwap: 'hsl(120, 70%, 55%)',
  rsi14: 'hsl(45, 95%, 58%)',
  adx14: 'hsl(24, 95%, 58%)',
  atr14: 'hsl(200, 95%, 55%)',
  volume: 'hsl(210, 20%, 48%)',
  volumeMa: 'hsl(340, 85%, 62%)',
} as const;

type IndicatorKey = keyof typeof INDICATOR_COLORS;

interface IndicatorDefinition {
  key: IndicatorKey;
  label: string;
  period?: number;
  color: string;
}

const PRICE_PANEL_INDICATORS = {
  ema20: { key: 'ema20', label: 'EMA20', period: 20, color: INDICATOR_COLORS.ema20 },
  ema21: { key: 'ema21', label: 'EMA21', period: 21, color: INDICATOR_COLORS.ema21 },
  ema50: { key: 'ema50', label: 'EMA50', period: 50, color: INDICATOR_COLORS.ema50 },
  ema200: { key: 'ema200', label: 'EMA200', period: 200, color: INDICATOR_COLORS.ema200 },
  dema200: { key: 'dema200', label: 'DEMA200', period: 200, color: INDICATOR_COLORS.dema200 },
  vwap: { key: 'vwap', label: 'VWAP', color: INDICATOR_COLORS.vwap },
} satisfies Record<string, IndicatorDefinition>;

const STUDY_PANEL_INDICATORS = {
  rsi14: { key: 'rsi14', label: 'RSI14 70/30', period: 14, color: INDICATOR_COLORS.rsi14 },
  adx14: { key: 'adx14', label: 'ADX14', period: 14, color: INDICATOR_COLORS.adx14 },
  atr14: { key: 'atr14', label: 'ATR14', period: 14, color: INDICATOR_COLORS.atr14 },
  volume: { key: 'volume', label: 'Volume', color: INDICATOR_COLORS.volume },
  volumeMa: { key: 'volumeMa', label: 'Volume MA20', period: 20, color: INDICATOR_COLORS.volumeMa },
} satisfies Record<string, IndicatorDefinition>;

// Advisory-only: stale overlay shows a UI badge but does not block execute.
// TV Chart execute uses Engine A signal (/api/quick-execute); overlay fetch is visual-only.
const ENGINE_B_OVERLAY_STALE_SEC = 300;

function buildStudyIndicatorDefs(periods: { rsi: number; adx: number; atr: number }) {
  return {
    rsi14: { ...STUDY_PANEL_INDICATORS.rsi14, label: `RSI${periods.rsi} 70/30`, period: periods.rsi },
    adx14: { ...STUDY_PANEL_INDICATORS.adx14, label: `ADX${periods.adx}`, period: periods.adx },
    atr14: { ...STUDY_PANEL_INDICATORS.atr14, label: `ATR${periods.atr}`, period: periods.atr },
    volume: STUDY_PANEL_INDICATORS.volume,
    volumeMa: STUDY_PANEL_INDICATORS.volumeMa,
  };
}

function overlayAgeSeconds(computedAt: string | null | undefined): number | null {
  if (!computedAt) return null;
  const ts = Date.parse(computedAt);
  if (!Number.isFinite(ts)) return null;
  return Math.max(0, (Date.now() - ts) / 1000);
}

const PRESET_OPTIONS = [
  { value: 'custom', label: 'Custom' },
  { value: 'all', label: 'All indicators' },
] as const;
type PresetValue = (typeof PRESET_OPTIONS)[number]['value'];

const CHART_HISTORY_LIMIT = 1000;
const VISIBLE_BAR_COUNT = 180;
const PRICE_CHART_HEIGHT_PX = 340;
const STUDY_PANE_HEIGHT_PX = 110;
const LIVE_TICK_MAX_AGE_SEC = 20;
const AUTO_REVIEW_CHART_SETTLE_MS = 10_000;

const TF_SECONDS: Record<string, number> = {
  M1: 60,
  M5: 5 * 60,
  M15: 15 * 60,
  M30: 30 * 60,
  H1: 60 * 60,
  H4: 4 * 60 * 60,
  D1: 24 * 60 * 60,
  W1: 7 * 24 * 60 * 60,
};

interface ChartPricePrecision {
  score_group: string;
  asset_type: string;
  precision: number;
  min_move: number;
  // Per-group EMA/RSI periods Engine A actually scores with (config
  // ENGINE_A_EMA_PERIODS_BY_CLASS / ENGINE_A_RSI_PERIOD_BY_CLASS). Drives the
  // chart's trend/momentum/long EMA lines so they match Engine A scoring.
  ema_periods?: { trend?: number; long?: number; momentum?: number };
  rsi_period?: number;
}

interface CandleApiRow {
  t?: string | number;
  o?: number | string;
  h?: number | string;
  l?: number | string;
  c?: number | string;
  v?: number | string;
  volume?: number | string;
  volume_ma?: number | string | null;
  vwap?: number | string | null;
  ema_trend?: number | string | null;
  ema_momentum?: number | string | null;
  ema_long?: number | string | null;
  ema21?: number | string | null;
  ema50?: number | string | null;
  ema200?: number | string | null;
  rsi?: number | string | null;
  rsi14?: number | string | null;
  adx?: number | string | null;
  adx14?: number | string | null;
  atr?: number | string | null;
  atr14?: number | string | null;
  provider?: string;
  confirmed?: boolean | null;
}
interface CandleApiResponse {
  candles?: CandleApiRow[];
  error?: string;
  symbol?: string;
  display?: string;
  tf?: string;
  candlesSource?: string;
  asset_group?: string;
  execution_provider?: string;
  chart_provider?: string;
  candle_provider?: string;
  live_tick_provider?: string;
  live_tick_source?: string;
  websocket_active?: boolean;
  websocket_stale?: boolean;
  websocket_last_message_ts?: number | null;
  fallback_reason?: string | null;
  bybit_symbol?: string;
  bybit_category?: string;
  volume_provider?: string;
  turnover_provider?: string;
  vwap_provider?: string;
  scoring_provider?: string;
  atr_provider?: string;
  indicator_provider?: string;
  provider_mismatch?: boolean;
  provider_mismatch_reason?: string | null;
  fallback_used?: boolean;
  fallback_chain?: string[];
  candle_confirmed_policy?: string;
  last_candle_ts?: string | number | null;
  live_tick_age_seconds?: number | null;
  chart_status?: string;
  pairType?: string;
  atr_timeframe?: string;
  liveTick?: Record<string, unknown> | null;
  price_precision?: ChartPricePrecision;
  indicator_periods?: { rsi?: number; adx?: number; atr?: number; ema?: { trend?: number; momentum?: number; long?: number } };
  engine_a_vwap_filter_enabled?: boolean;
}

interface EngineBZone {
  lower?: number | string | null;
  upper?: number | string | null;
  level?: number | string | null;
}

interface EngineBRangeZone {
  type?: string | null;
  top?: number | string | null;
  bottom?: number | string | null;
  mitigated?: boolean | null;
}

interface EngineBBreakerBlock {
  type?: string | null;
  level?: number | string | null;
}

interface EngineBOverlayPayload {
  symbol?: string;
  timeframe?: string;
  price_precision?: ChartPricePrecision;
  overlay_source?: string;
  overlay_version?: string;
  computed_at?: string;
  warnings?: string[];
  nearest_support_zone?: EngineBZone | null;
  nearest_resistance_zone?: EngineBZone | null;
  bos_data?: Record<string, unknown>;
  choch_data?: Record<string, unknown>;
  order_blocks?: EngineBRangeZone[];
  active_fvgs?: EngineBRangeZone[];
  breaker_block?: EngineBBreakerBlock | null;
  current_swing_sequence?: string | null;
  macro_swing_sequence?: string | null;
  bos_confirmed?: boolean;
  choch_confirmed?: boolean;
  liquidity_sweep?: boolean;
  struct_atr?: number | string | null;
}

interface EngineBOverlayLine {
  label: string;
  price: number;
  color: string;
  style?: LineStyle;
}

interface LiveTick {
  price: number;
  ts: number;
  ageSec: number;
  source?: string;
}

function toTimestamp(raw: string | number | undefined): UTCTimestamp | null {
  if (raw == null) return null;
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    return (raw > 1e12 ? Math.floor(raw / 1000) : Math.floor(raw)) as UTCTimestamp;
  }
  if (typeof raw === 'string' && raw.length > 0) {
    const ms = Date.parse(raw);
    if (Number.isFinite(ms)) return Math.floor(ms / 1000) as UTCTimestamp;
  }
  return null;
}

function liveTickFromEntry(entry: unknown, nowSeconds = Date.now() / 1000): LiveTick | null {
  const record = asRecord(entry);
  const price = toNum(record.price, NaN);
  if (!(Number.isFinite(price) && price > 0)) return null;

  const ageCandidate = toNum(record.ageSec, NaN);
  const tsCandidate = toTimestamp(
    typeof record.ts === 'string' || typeof record.ts === 'number' ? record.ts : undefined,
  );
  const ts = tsCandidate ?? (Number.isFinite(ageCandidate) ? Math.floor(nowSeconds - Math.max(0, ageCandidate)) : null);
  if (ts == null) return null;

  const computedAge = Math.max(0, nowSeconds - ts);
  const ageSec = Number.isFinite(ageCandidate) && ageCandidate >= 0 ? ageCandidate : computedAge;
  const source = typeof record.source === 'string' && record.source.length > 0 ? record.source : undefined;
  return { price, ts, ageSec, source };
}

function liveTickFromChartPayload(chartPayload: CandleApiResponse | null, nowSeconds = Date.now() / 1000): LiveTick | null {
  const record = asRecord(chartPayload?.liveTick);
  const price = toNum(record.price, NaN);
  if (!(Number.isFinite(price) && price > 0)) return null;
  const rawTs = record.timestamp;
  const tsCandidate = toTimestamp(typeof rawTs === 'number' || typeof rawTs === 'string' ? rawTs : undefined);
  const ageCandidate = toNum(record.age_seconds ?? chartPayload?.live_tick_age_seconds, NaN);
  const ts = tsCandidate ?? (Number.isFinite(ageCandidate) ? Math.floor(nowSeconds - Math.max(0, ageCandidate)) : null);
  if (ts == null) return null;
  const ageSec = Number.isFinite(ageCandidate) && ageCandidate >= 0 ? ageCandidate : Math.max(0, nowSeconds - ts);
  const source = typeof record.source === 'string' && record.source.length > 0 ? record.source : chartPayload?.live_tick_provider;
  return { price, ts, ageSec, source };
}

function timeToEpochSeconds(time: Time): number | null {
  if (typeof time === 'number' && Number.isFinite(time)) return Math.floor(time);
  if (typeof time === 'string' && time.length > 0) {
    const ms = Date.parse(time);
    if (Number.isFinite(ms)) return Math.floor(ms / 1000);
  }
  if (typeof time === 'object' && time !== null && 'year' in time && 'month' in time && 'day' in time) {
    const businessDay = time as { year: number; month: number; day: number };
    const ms = Date.UTC(businessDay.year, businessDay.month - 1, businessDay.day);
    if (Number.isFinite(ms)) return Math.floor(ms / 1000);
  }
  return null;
}

function bucketStartForTick(backendTf: string, tickSec: number, lastTime: number): UTCTimestamp | null {
  const tfSec = TF_SECONDS[backendTf];
  if (!(Number.isFinite(tfSec) && tfSec > 0)) return null;
  const offset = ((lastTime % tfSec) + tfSec) % tfSec;
  return (Math.floor((tickSec - offset) / tfSec) * tfSec + offset) as UTCTimestamp;
}

function buildLiveCandleRows(
  baseRows: CandlestickData[],
  liveTick: LiveTick | null,
  backendTf: string,
): CandlestickData[] {
  if (!liveTick || baseRows.length === 0) return baseRows;
  if (liveTick.ageSec > LIVE_TICK_MAX_AGE_SEC) return baseRows;

  const last = baseRows[baseRows.length - 1];
  const lastTime = timeToEpochSeconds(last.time);
  if (lastTime == null) return baseRows;

  const bucketStart = bucketStartForTick(backendTf, liveTick.ts, lastTime);
  if (bucketStart == null || bucketStart < lastTime) return baseRows;

  const out = [...baseRows];
  if (bucketStart > lastTime) {
    out.push({
      time: bucketStart,
      open: last.close,
      high: Math.max(last.close, liveTick.price),
      low: Math.min(last.close, liveTick.price),
      close: liveTick.price,
    });
    return out;
  }

  out[out.length - 1] = {
    ...last,
    high: Math.max(last.high, liveTick.price),
    low: Math.min(last.low, liveTick.price),
    close: liveTick.price,
  };
  return out;
}

// --- Indicator math --------------------------------------------------

export function ema(values: number[], period: number): (number | null)[] {
  if (period <= 1 || values.length === 0) return values.map(() => null);
  const k = 2 / (period + 1);
  const out: (number | null)[] = [];
  let prev: number | null = null;
  let seed = 0;
  let seedCount = 0;
  for (let i = 0; i < values.length; i += 1) {
    const v = values[i];
    if (!Number.isFinite(v)) {
      out.push(prev);
      continue;
    }
    if (i < period) {
      seed += v;
      seedCount += 1;
      if (i === period - 1) {
        prev = seed / seedCount;
        out.push(prev);
      } else {
        out.push(null);
      }
      continue;
    }
    if (prev == null) prev = v;
    else prev = v * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
}

export function dema(values: number[], period: number): (number | null)[] {
  const e1 = ema(values, period);
  const e1Numeric = e1.map((v) => (v == null ? NaN : v));
  const e2 = ema(e1Numeric, period);
  return e1.map((v1, i) => {
    const v2 = e2[i];
    if (v1 == null || v2 == null) return null;
    return 2 * v1 - v2;
  });
}

export function rsi(values: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (values.length <= period) return out;
  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i += 1) {
    const change = values[i] - values[i - 1];
    if (change >= 0) avgGain += change;
    else avgLoss -= change;
  }
  avgGain /= period;
  avgLoss /= period;
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = period + 1; i < values.length; i += 1) {
    const change = values[i] - values[i - 1];
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? -change : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

export function atr(highs: number[], lows: number[], closes: number[], period = 14): (number | null)[] {
  const n = closes.length;
  const out: (number | null)[] = new Array(n).fill(null);
  if (n <= period) return out;
  const tr: number[] = new Array(n).fill(0);
  tr[0] = highs[0] - lows[0];
  for (let i = 1; i < n; i += 1) {
    const hl = highs[i] - lows[i];
    const hc = Math.abs(highs[i] - closes[i - 1]);
    const lc = Math.abs(lows[i] - closes[i - 1]);
    tr[i] = Math.max(hl, hc, lc);
  }
  let prev = 0;
  for (let i = 1; i <= period; i += 1) prev += tr[i];
  prev /= period;
  out[period] = prev;
  for (let i = period + 1; i < n; i += 1) {
    prev = (prev * (period - 1) + tr[i]) / period;
    out[i] = prev;
  }
  return out;
}

export function sma(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (period <= 0) return out;
  for (let i = period - 1; i < values.length; i += 1) {
    let sum = 0;
    let count = 0;
    for (let j = i - period + 1; j <= i; j += 1) {
      if (Number.isFinite(values[j])) {
        sum += values[j];
        count += 1;
      }
    }
    out[i] = count > 0 ? sum / count : null;
  }
  return out;
}

export function vwapFromRows(
  highs: number[],
  lows: number[],
  closes: number[],
  volumes: number[],
  times?: number[],
): (number | null)[] {
  const out: (number | null)[] = [];
  let cumValue = 0;
  let cumVolume = 0;
  let prevDay: number | null = null;
  for (let i = 0; i < closes.length; i += 1) {
    // Session-anchor: reset the cumulative sums at each UTC-day boundary when
    // bar times are supplied, so VWAP is a daily measure rather than a
    // window-cumulative line drifting from the first fetched bar.
    if (times && Number.isFinite(times[i])) {
      const day = Math.floor(times[i] / 86400);
      if (prevDay != null && day !== prevDay) {
        cumValue = 0;
        cumVolume = 0;
      }
      prevDay = day;
    }
    const volume = Number.isFinite(volumes[i]) ? volumes[i] : 0;
    if (volume <= 0) {
      out.push(cumVolume > 0 ? cumValue / cumVolume : null);
      continue;
    }
    const typical = (highs[i] + lows[i] + closes[i]) / 3;
    cumValue += typical * volume;
    cumVolume += volume;
    out.push(cumVolume > 0 ? cumValue / cumVolume : null);
  }
  return out;
}

export function adx(highs: number[], lows: number[], closes: number[], period = 14): (number | null)[] {
  const n = closes.length;
  const out: (number | null)[] = new Array(n).fill(null);
  if (n < period * 2 + 1) return out;
  const tr: number[] = [];
  const plusDm: number[] = [];
  const minusDm: number[] = [];
  for (let i = 1; i < n; i += 1) {
    const upMove = highs[i] - highs[i - 1];
    const downMove = lows[i - 1] - lows[i];
    plusDm.push(upMove > downMove && upMove > 0 ? upMove : 0);
    minusDm.push(downMove > upMove && downMove > 0 ? downMove : 0);
    tr.push(Math.max(highs[i] - lows[i], Math.abs(highs[i] - closes[i - 1]), Math.abs(lows[i] - closes[i - 1])));
  }
  let smoothTr = tr.slice(0, period).reduce((a, b) => a + b, 0);
  let smoothPlus = plusDm.slice(0, period).reduce((a, b) => a + b, 0);
  let smoothMinus = minusDm.slice(0, period).reduce((a, b) => a + b, 0);
  const dx: number[] = [];
  for (let i = period; i < tr.length; i += 1) {
    smoothTr = smoothTr - smoothTr / period + tr[i];
    smoothPlus = smoothPlus - smoothPlus / period + plusDm[i];
    smoothMinus = smoothMinus - smoothMinus / period + minusDm[i];
    const plusDi = smoothTr ? (smoothPlus / smoothTr) * 100 : 0;
    const minusDi = smoothTr ? (smoothMinus / smoothTr) * 100 : 0;
    const sum = plusDi + minusDi;
    dx.push(sum ? (Math.abs(plusDi - minusDi) / sum) * 100 : 0);
  }
  if (dx.length >= period) {
    let avg = dx.slice(0, period).reduce((a, b) => a + b, 0) / period;
    const first = period * 2 + 1;
    if (first < n) out[first] = avg;
    for (let i = period; i < dx.length; i += 1) {
      avg = (avg * (period - 1) + dx[i]) / period;
      const idx = first + (i - period + 1);
      if (idx < n) out[idx] = avg;
    }
  }
  return out;
}

// --- Engine A candidate helpers (unchanged from prior version) -------

function displaySymbol(signal: EngineASignal | null): string | null {
  if (!signal) return null;
  return signal.display || signal.symbol || signal.pair || null;
}

function normalizeDirection(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const normalized = value.toUpperCase();
  if (normalized === 'LONG' || normalized === 'BUY') return 'LONG';
  if (normalized === 'SHORT' || normalized === 'SELL') return 'SHORT';
  return normalized || null;
}

function firstNumber(...values: unknown[]): number | null {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() !== '') {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === 'string' && value.trim() !== '') return value;
  }
  return null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function pickEngineACandidate(rows: unknown[]): EngineASignal | null {
  const candidates = rows.filter((row): row is EngineASignal => Boolean(row && typeof row === 'object'));
  return candidates.find((row) => Boolean(normalizeDirection(row.direction))) || candidates[0] || null;
}

function symbolKey(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const upper = value.trim().toUpperCase();
  if (!upper) return null;
  const withoutProvider = upper.includes(':') ? upper.split(':').pop() || upper : upper;
  const withoutYahooFxSuffix = withoutProvider.replace(/=X$/, '');
  const key = withoutYahooFxSuffix.replace(/[^A-Z0-9]/g, '');
  return key || null;
}

function isEngineSignalLike(value: unknown): value is EngineASignal {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const signal = value as EngineASignal;
  const hasSymbol = Boolean(symbolKey(displaySymbol(signal) || signal.symbol || signal.pair));
  const hasTradeShape = Boolean(
    normalizeDirection(signal.direction)
      || signal.trade !== undefined
      || signal.executable !== undefined
      || signal.confluenceScore !== undefined
      || signal.score !== undefined
      || signal.engine_b
      || signal.naked_data,
  );
  return hasSymbol && hasTradeShape;
}

function findEngineACandidateForSymbol(rows: EngineASignal[], symbol: string): EngineASignal | null {
  const chartKey = symbolKey(symbol);
  if (!chartKey) return null;
  return rows.find((row) => {
    const keys = [displaySymbol(row), row.symbol, row.pair].map(symbolKey);
    return keys.includes(chartKey);
  }) || null;
}

function reviewTimeframeFor(signal: EngineASignal | null): string {
  const rawTimeframe = firstString(signal?.timeframe, signal?.tf, signal?.interval)?.toUpperCase();
  if (rawTimeframe === 'H1' || rawTimeframe === '1H' || rawTimeframe === '60') return '60';
  if (rawTimeframe === 'H4' || rawTimeframe === '4H' || rawTimeframe === '240') return '240';
  const style = firstString(signal?.style, signal?.trade_style)?.toLowerCase();
  return style === 'scalp' || style === 'intraday' ? '60' : '240';
}

function normalizeBackendTf(tf: string | null | undefined): string | null {
  if (!tf) return null;
  const raw = tf.trim().toUpperCase().replace(/\s+/g, '');
  if (!raw) return null;
  if (raw === '240' || raw === '4H') return 'H4';
  if (raw === '60' || raw === '1H') return 'H1';
  if (raw === '15' || raw === '15M') return 'M15';
  if (raw === '5' || raw === '5M') return 'M5';
  if (raw === '1' || raw === '1M') return 'M1';
  if (raw === 'D' || raw === '1D') return 'D1';
  if (raw === 'W' || raw === '1W') return 'W1';
  return raw;
}

function tfCodeForBackend(tf: string | null | undefined): string | null {
  const normalized = normalizeBackendTf(tf);
  if (!normalized) return null;
  return TF_UI_CODE_MAP[normalized] || null;
}

function reviewTimeframeRoute(response: AIChartReviewResponse | null): TimeframeRoute | null {
  if (!response) return null;
  const route =
    response.timeframeRoute ??
    response.timeframe_route ??
    response.engine_a_context?.timeframe_route;
  if (!route || route.enabled === false) return route ?? null;
  return route;
}

function timeframeRouteDisplay(route: TimeframeRoute | null): string | null {
  if (!route?.contextTf || !route.entryTf || !route.executionTf) return null;
  return `${route.contextTf} Context -> ${route.entryTf} Entry -> ${route.executionTf} Execution`;
}

function timeframeRouteKey(symbol: string | null, route: TimeframeRoute): string {
  return [
    symbol || '',
    route.contextTf || '',
    route.entryTf || '',
    route.autoSelectTf || '',
    route.mode || '',
  ].join(':');
}

const CHART_CAPTURE_BACKGROUND = { r: 11, g: 15, b: 20 };

function waitForChartPaint(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => resolve());
    });
  });
}

function chartRenderGenerationKey(
  pair: string,
  backendTf: string | undefined,
  candleCount: number,
): string {
  return `${pair}:${backendTf || ''}:${candleCount}`;
}

function resolveChartReviewBlockReason(args: {
  loading: boolean;
  engineBOverlayPending: boolean;
  hasCandles: boolean;
  chartPaintReady: boolean;
  chartIndicatorsReady: boolean;
}): string | null {
  if (args.loading) return 'Loading chart candles…';
  if (!args.hasCandles) return 'Waiting for chart candles…';
  if (args.engineBOverlayPending) return 'Loading Engine B overlays…';
  if (!args.chartPaintReady) return 'Waiting for chart paint…';
  if (!args.chartIndicatorsReady) return 'Waiting for chart indicators…';
  return null;
}

function canvasHasNonBackgroundContent(
  canvas: HTMLCanvasElement,
  background = CHART_CAPTURE_BACKGROUND,
  tolerance = 8,
): boolean {
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  if (!ctx) return false;
  const sampleW = Math.min(canvas.width, 96);
  const sampleH = Math.min(canvas.height, 96);
  if (sampleW <= 0 || sampleH <= 0) return false;
  const { data } = ctx.getImageData(0, 0, sampleW, sampleH);
  for (let i = 0; i < data.length; i += 4) {
    const alpha = data[i + 3];
    if (alpha < 10) continue;
    if (
      Math.abs(data[i] - background.r) > tolerance
      || Math.abs(data[i + 1] - background.g) > tolerance
      || Math.abs(data[i + 2] - background.b) > tolerance
    ) {
      return true;
    }
  }
  return false;
}

interface ChartStudySnapshot {
  rows: CandlestickData[];
  highs: number[];
  lows: number[];
  closes: number[];
  volumes: number[];
  seriesValues: Partial<Record<IndicatorKey, (number | null)[]>>;
  latest: Partial<Record<IndicatorKey, number>> & { close?: number };
}

function latestFinite(values: (number | null)[]): number | null {
  for (let i = values.length - 1; i >= 0; i -= 1) {
    const value = values[i];
    if (value != null && Number.isFinite(value)) return value;
  }
  return null;
}

function buildChartStudySnapshot(
  candles: CandleApiRow[] | null,
  liveTick: LiveTick | null,
  backendTf: string | undefined,
  isCryptoChart: boolean,
  showVwapOnChart: boolean,
  emaPeriods: { trend: number; momentum: number; long: number },
  indicatorPeriods: { rsi: number; adx: number; atr: number },
): ChartStudySnapshot {
  const empty: ChartStudySnapshot = { rows: [], highs: [], lows: [], closes: [], volumes: [], seriesValues: {}, latest: {} };
  if (!candles?.length || !backendTf) return empty;

  const baseRows: CandlestickData[] = [];
  const baseVolumes: number[] = [];
  const baseConfirmed: (boolean | null)[] = [];
  const apiVwap: (number | null)[] = [];
  const apiVolumeMa: (number | null)[] = [];
  const apiAdx14: (number | null)[] = [];
  const apiEmaTrend: (number | null)[] = [];
  const apiEmaMomentum: (number | null)[] = [];
  const apiEmaLong: (number | null)[] = [];
  const apiRsi14: (number | null)[] = [];
  const apiAtr14: (number | null)[] = [];
  for (const c of candles) {
    const t = toTimestamp(c.t);
    const o = toNum(c.o, NaN);
    const h = toNum(c.h, NaN);
    const l = toNum(c.l, NaN);
    const cl = toNum(c.c, NaN);
    if (t == null) continue;
    if (![o, h, l, cl].every(Number.isFinite)) continue;
    baseRows.push({ time: t, open: o, high: h, low: l, close: cl });
    baseVolumes.push(toNum(c.volume ?? c.v, 0));
    baseConfirmed.push(typeof c.confirmed === 'boolean' ? c.confirmed : null);
    apiVwap.push(Number.isFinite(toNum(c.vwap, NaN)) ? toNum(c.vwap, NaN) : null);
    apiVolumeMa.push(Number.isFinite(toNum(c.volume_ma, NaN)) ? toNum(c.volume_ma, NaN) : null);
    apiAdx14.push(Number.isFinite(toNum(c.adx14 ?? c.adx, NaN)) ? toNum(c.adx14 ?? c.adx, NaN) : null);
    apiEmaTrend.push(Number.isFinite(toNum(c.ema_trend ?? c.ema21, NaN)) ? toNum(c.ema_trend ?? c.ema21, NaN) : null);
    apiEmaMomentum.push(Number.isFinite(toNum(c.ema_momentum ?? c.ema50, NaN)) ? toNum(c.ema_momentum ?? c.ema50, NaN) : null);
    apiEmaLong.push(Number.isFinite(toNum(c.ema_long ?? c.ema200, NaN)) ? toNum(c.ema_long ?? c.ema200, NaN) : null);
    apiRsi14.push(Number.isFinite(toNum(c.rsi ?? c.rsi14, NaN)) ? toNum(c.rsi ?? c.rsi14, NaN) : null);
    apiAtr14.push(Number.isFinite(toNum(c.atr14, NaN)) ? toNum(c.atr14, NaN) : null);
  }

  const rows = buildLiveCandleRows(baseRows, liveTick, backendTf);
  const highs = rows.map((row) => row.high);
  const lows = rows.map((row) => row.low);
  const closes = rows.map((row) => row.close);
  const volumes = rows.map((_row, idx) => baseVolumes[idx] ?? 0);
  const cHighs = baseRows.map((row) => row.high);
  const cLows = baseRows.map((row) => row.low);
  const cCloses = baseRows.map((row) => row.close);
  const cVolumes = baseVolumes.slice();
  const cTimes = baseRows.map((row) => timeToEpochSeconds(row.time) ?? 0);
  // Confirmed-only indicator inputs: exclude trailing forming bars (both the
  // tick-built live bar and an unconfirmed last API candle) so chart indicators
  // match Engine A's confirmed-only series. Forming bars are still drawn for
  // price; they just carry no indicator point. The server applies the same
  // policy, so API indicator series are already null on forming bars.
  let confirmedCount = baseRows.length;
  while (confirmedCount > 0 && baseConfirmed[confirmedCount - 1] === false) confirmedCount -= 1;
  const iHighs = cHighs.slice(0, confirmedCount);
  const iLows = cLows.slice(0, confirmedCount);
  const iCloses = cCloses.slice(0, confirmedCount);
  const iVolumes = cVolumes.slice(0, confirmedCount);
  // Prefer server-computed indicators (same calc_* library as Engine A) for all
  // asset classes; fall back to local math only when the API omitted them.
  const useApiIndicators = true;
  // Draw the trend/momentum/long EMA lines at the per-group periods Engine A
  // scores with (see ChartPricePrecision.ema_periods); the API rows carry the
  // same per-group series under ema_trend/ema_momentum/ema_long. The local
  // ema() matches the server calc_ema exactly, so the fallback stays
  // parity-faithful. The fast trend line (ema20 non-crypto / ema21 crypto) is
  // unified onto the resolved trend period; DEMA tracks long.
  const { trend: trendPeriod, momentum: momentumPeriod, long: longPeriod } = emaPeriods;
  const { rsi: rsiPeriod, adx: adxPeriod, atr: atrPeriod } = indicatorPeriods;
  const emaTrendValues =
    useApiIndicators && apiEmaTrend.some((v) => v != null) ? apiEmaTrend : ema(iCloses, trendPeriod);
  const ema20Values = emaTrendValues;
  const ema21Values = emaTrendValues;
  const ema50Values =
    useApiIndicators && apiEmaMomentum.some((v) => v != null) ? apiEmaMomentum : ema(iCloses, momentumPeriod);
  const ema200Values =
    useApiIndicators && apiEmaLong.some((v) => v != null) ? apiEmaLong : ema(iCloses, longPeriod);
  const dema200Values = dema(iCloses, longPeriod);
  const rsi14Values = useApiIndicators && apiRsi14.some((v) => v != null) ? apiRsi14 : rsi(iCloses, rsiPeriod);
  const atr14Values = useApiIndicators && apiAtr14.some((v) => v != null) ? apiAtr14 : atr(iHighs, iLows, iCloses, atrPeriod);
  // VWAP stays full-series (matches the server): per-bar cumulative, not an
  // Engine A scoring input, and the forming bar's VWAP point is meaningful.
  const vwapAnchorTimes =
    backendTf && (TF_SECONDS[backendTf] ?? 0) > 0 && (TF_SECONDS[backendTf] as number) < 86400 ? cTimes : undefined;
  const vwapValues = apiVwap.some((v) => v != null) ? apiVwap : vwapFromRows(cHighs, cLows, cCloses, cVolumes, vwapAnchorTimes);
  const adx14Values = apiAdx14.some((v) => v != null) ? apiAdx14 : adx(iHighs, iLows, iCloses, adxPeriod);
  const volumeMaValues = apiVolumeMa.some((v) => v != null) ? apiVolumeMa : sma(iVolumes, 20);

  return {
    rows,
    highs,
    lows,
    closes,
    volumes,
    seriesValues: {
      ema20: ema20Values,
      ema21: ema21Values,
      ema50: ema50Values,
      ema200: ema200Values,
      dema200: dema200Values,
      vwap: vwapValues,
      rsi14: rsi14Values,
      adx14: adx14Values,
      atr14: atr14Values,
      volumeMa: volumeMaValues,
    },
    latest: {
      close: closes.length ? closes[closes.length - 1] : undefined,
      ema20: latestFinite(ema20Values) ?? undefined,
      ema21: latestFinite(ema21Values) ?? undefined,
      ema50: latestFinite(ema50Values) ?? undefined,
      ema200: latestFinite(ema200Values) ?? undefined,
      dema200: latestFinite(dema200Values) ?? undefined,
      vwap: showVwapOnChart ? latestFinite(vwapValues) ?? undefined : undefined,
      rsi14: latestFinite(rsi14Values) ?? undefined,
      adx14: latestFinite(adx14Values) ?? undefined,
      atr14: latestFinite(atr14Values) ?? undefined,
      volume: isCryptoChart ? latestFinite(cVolumes) ?? undefined : undefined,
      volumeMa: isCryptoChart ? latestFinite(volumeMaValues) ?? undefined : undefined,
    },
  };
}

function formatIndicatorValue(value: number | undefined, precision = 5): string {
  return Number.isFinite(value) ? fmtNum(value, precision) : '-';
}

function formatUtcLabel(value: string | number | null | undefined): string {
  if (value == null || value === '') return 'last candle unavailable';
  const ts = toTimestamp(value);
  if (ts == null) return `last candle ${String(value)}`;
  const iso = new Date(ts * 1000).toISOString().replace('T', ' ').replace(/\.\d{3}Z$/, ' UTC');
  return `last candle ${iso.slice(0, 16)} UTC`;
}

function formatLastCandleChip(value: string | number | null | undefined): string {
  if (value == null || value === '') return 'Last: unavailable';
  const ts = toTimestamp(value);
  if (ts == null) return `Last: ${String(value)}`;
  const iso = new Date(ts * 1000).toISOString();
  return `Last: ${iso.slice(11, 16)} UTC`;
}

function titleCaseProvider(value: string | null | undefined): string {
  if (!value) return 'unknown';
  const lower = value.toLowerCase();
  if (lower === 'mt5') return 'MT5';
  if (lower === 'eodhd') return 'EODHD';
  if (lower === 'binance' || lower === 'binance_ws') return 'Binance WS';
  if (lower === 'binance_rest') return 'Binance REST';
  if (lower === 'bybit' || lower === 'bybit_ws') return 'Bybit WS';
  if (lower === 'bybit_rest') return 'Bybit REST';
  if (lower.startsWith('binance')) return 'Binance';
  if (lower.startsWith('bybit')) return 'Bybit';
  return value;
}

function addEngineBZoneLines(
  lines: EngineBOverlayLine[],
  label: string,
  zone: EngineBZone | null | undefined,
  color: string,
) {
  if (!zone) return;
  const lower = firstNumber(zone.lower, zone.level);
  const upper = firstNumber(zone.upper, zone.level);
  if (lower != null) lines.push({ label: `${label} low`, price: lower, color, style: LineStyle.Dashed });
  if (upper != null && upper !== lower) lines.push({ label: `${label} high`, price: upper, color, style: LineStyle.Dashed });
}

function addEngineBRangeLines(
  lines: EngineBOverlayLine[],
  prefix: string,
  zones: EngineBRangeZone[] | undefined,
  limit: number,
  colorForType: (type: string) => string,
) {
  for (const zone of (zones || []).filter((item) => !item?.mitigated).slice(0, limit)) {
    const top = firstNumber(zone.top);
    const bottom = firstNumber(zone.bottom);
    if (top == null || bottom == null) continue;
    const type = typeof zone.type === 'string' && zone.type ? zone.type : 'zone';
    const color = colorForType(type.toLowerCase());
    lines.push({ label: `${prefix} ${type} top`, price: top, color, style: LineStyle.Dotted });
    if (bottom !== top) lines.push({ label: `${prefix} ${type} bottom`, price: bottom, color, style: LineStyle.Dotted });
  }
}

const ENGINE_B_MERGED_LABEL_MAX = 28;

function stripEngineBPrefix(label: string): string {
  return label.startsWith('Engine B ') ? label.slice('Engine B '.length) : label;
}

function compactEngineBLabel(label: string): string {
  let text = stripEngineBPrefix(label);
  text = text.replace(/\bsupport\b/gi, 'Sup').replace(/\bresistance\b/gi, 'Res');
  if (text.length <= ENGINE_B_MERGED_LABEL_MAX) return text;
  return `${text.slice(0, ENGINE_B_MERGED_LABEL_MAX - 1)}…`;
}

function engineBLinePriority(label: string): number {
  const text = stripEngineBPrefix(label).toLowerCase();
  if (/choch/i.test(text)) return 1;
  if (/breaker/i.test(text)) return 2;
  if (/bos/i.test(text)) return 3;
  if (/support|resistance/i.test(text)) return 4;
  if (/\bob\b/i.test(text)) return 5;
  if (/fvg/i.test(text)) return 6;
  return 99;
}

function engineBOverlayCollisionThreshold(
  payload: EngineBOverlayPayload,
  lines: EngineBOverlayLine[],
): number {
  const prices = lines.map((line) => line.price).filter((price) => Number.isFinite(price));
  const referencePrice = prices.length > 0
    ? prices.reduce((sum, price) => sum + price, 0) / prices.length
    : 1;
  const structAtr = firstNumber(payload.struct_atr);
  if (structAtr != null && structAtr > 0) return structAtr * 0.20;
  return 0.0008 * Math.abs(referencePrice);
}

function pickHigherPriorityEngineBLine(members: EngineBOverlayLine[]): EngineBOverlayLine {
  return members.reduce((best, current) =>
    (engineBLinePriority(current.label) < engineBLinePriority(best.label) ? current : best),
  );
}

function formatMergedEngineBLabel(members: EngineBOverlayLine[]): string {
  const compacted = members.map((member) => compactEngineBLabel(member.label));
  if (members.length === 2) {
    const joined = `${compacted[0]} / ${compacted[1]}`;
    if (joined.length <= ENGINE_B_MERGED_LABEL_MAX) return joined;
    return `${compacted[0].slice(0, 12)} / ${compacted[1].slice(0, 12)}`;
  }
  const first = compacted[0] ?? 'level';
  const suffix = ` +${members.length - 1}`;
  const maxFirst = ENGINE_B_MERGED_LABEL_MAX - suffix.length;
  const trimmed = first.length > maxFirst ? `${first.slice(0, maxFirst - 1)}…` : first;
  return `${trimmed}${suffix}`;
}

function mergeEngineBOverlayLines(lines: EngineBOverlayLine[], threshold: number): EngineBOverlayLine[] {
  if (lines.length <= 1) {
    return lines.map((line) => ({ ...line, label: stripEngineBPrefix(line.label) }));
  }

  const sorted = [...lines].sort((a, b) => a.price - b.price);
  const clusters: EngineBOverlayLine[][] = [];
  let cluster: EngineBOverlayLine[] = [sorted[0]];
  for (let i = 1; i < sorted.length; i += 1) {
    const line = sorted[i];
    const lastPrice = cluster[cluster.length - 1].price;
    if (line.price - lastPrice <= threshold) {
      cluster.push(line);
    } else {
      clusters.push(cluster);
      cluster = [line];
    }
  }
  clusters.push(cluster);

  return clusters.map((members) => {
    if (members.length === 1) {
      const member = members[0];
      return { ...member, label: stripEngineBPrefix(member.label) };
    }
    const lead = pickHigherPriorityEngineBLine(members);
    const meanPrice = members.reduce((sum, member) => sum + member.price, 0) / members.length;
    return {
      label: formatMergedEngineBLabel(members),
      price: meanPrice,
      color: lead.color,
      style: lead.style,
    };
  });
}

function engineBOverlayLines(payload: EngineBOverlayPayload | null, enabled: boolean): EngineBOverlayLine[] {
  if (!enabled || payload?.overlay_source !== 'engine_b') return [];
  const lines: EngineBOverlayLine[] = [];
  addEngineBZoneLines(lines, 'Engine B support', payload.nearest_support_zone, 'rgba(16, 185, 129, 0.80)');
  addEngineBZoneLines(lines, 'Engine B resistance', payload.nearest_resistance_zone, 'rgba(244, 63, 94, 0.80)');

  const bos = payload.bos_data || {};
  const bosHigh = firstNumber(bos.last_broken_high, bos.level, bos.price);
  const bosLow = firstNumber(bos.last_broken_low);
  if (bosHigh != null) lines.push({ label: 'Engine B BOS high', price: bosHigh, color: 'rgba(250, 204, 21, 0.90)' });
  if (bosLow != null && bosLow !== bosHigh) lines.push({ label: 'Engine B BOS low', price: bosLow, color: 'rgba(250, 204, 21, 0.80)' });

  const choch = payload.choch_data || {};
  const chochLevel = firstNumber(choch.choch_level, choch.level, choch.price);
  if (chochLevel != null) lines.push({ label: 'Engine B CHOCH', price: chochLevel, color: 'rgba(168, 85, 247, 0.90)' });

  addEngineBRangeLines(lines, 'Engine B OB', payload.order_blocks, 2, (type) =>
    type.includes('bull') ? 'rgba(20, 184, 166, 0.75)' : 'rgba(251, 113, 133, 0.75)',
  );
  addEngineBRangeLines(lines, 'Engine B FVG', payload.active_fvgs, 2, (type) =>
    type.includes('bull') ? 'rgba(34, 197, 94, 0.68)' : 'rgba(248, 113, 113, 0.68)',
  );

  const breakerLevel = firstNumber(payload.breaker_block?.level);
  if (breakerLevel != null) {
    lines.push({
      label: `Engine B ${payload.breaker_block?.type || 'breaker'}`,
      price: breakerLevel,
      color: 'rgba(56, 189, 248, 0.85)',
      style: LineStyle.Dashed,
    });
  }
  const threshold = engineBOverlayCollisionThreshold(payload, lines);
  return mergeEngineBOverlayLines(lines, threshold);
}

// --- Engine B clean-mode zone rendering ------------------------------
// Visual-only: replaces the dense stack of right-axis text labels with translucent
// horizontal bands (support/resistance) and boxes (FVG, order blocks). The payload
// itself is untouched — these are render-time helpers built from the same fields
// the existing line-based rendering consumes.

type EngineBZoneCategory =
  | 'support'
  | 'resistance'
  | 'fvg_bull'
  | 'fvg_bear'
  | 'ob_bull'
  | 'ob_bear';

interface EngineBZoneFill {
  top: number;
  bottom: number;
  fill: string;
  stroke: string;
  category: EngineBZoneCategory;
}

interface EngineBSingleLevel {
  label: string;
  price: number;
  color: string;
  style?: LineStyle;
}

// Do not replace filled support/resistance/FVG zones with line-only overlays;
// this filled-zone style is the preferred readable Engine B visual.
const ENGINE_B_ZONE_STYLE: Record<EngineBZoneCategory, { fill: string; stroke: string }> = {
  support: { fill: 'rgba(16, 185, 129, 0.18)', stroke: 'rgba(16, 185, 129, 0.55)' },
  resistance: { fill: 'rgba(244, 63, 94, 0.18)', stroke: 'rgba(244, 63, 94, 0.55)' },
  fvg_bull: { fill: 'rgba(34, 197, 94, 0.16)', stroke: 'rgba(34, 197, 94, 0.50)' },
  fvg_bear: { fill: 'rgba(248, 113, 113, 0.16)', stroke: 'rgba(248, 113, 113, 0.50)' },
  ob_bull: { fill: 'rgba(20, 184, 166, 0.14)', stroke: 'rgba(20, 184, 166, 0.50)' },
  ob_bear: { fill: 'rgba(251, 113, 133, 0.14)', stroke: 'rgba(251, 113, 133, 0.50)' },
};

function pushZoneFromPair(
  zones: EngineBZoneFill[],
  category: EngineBZoneCategory,
  upper: number | null,
  lower: number | null,
) {
  if (upper == null && lower == null) return;
  const top = upper ?? lower!;
  const bottom = lower ?? upper!;
  if (!Number.isFinite(top) || !Number.isFinite(bottom)) return;
  const style = ENGINE_B_ZONE_STYLE[category];
  zones.push({
    top: Math.max(top, bottom),
    bottom: Math.min(top, bottom),
    fill: style.fill,
    stroke: style.stroke,
    category,
  });
}

function buildEngineBZones(payload: EngineBOverlayPayload | null, enabled: boolean): EngineBZoneFill[] {
  if (!enabled || payload?.overlay_source !== 'engine_b') return [];
  const zones: EngineBZoneFill[] = [];

  const support = payload.nearest_support_zone;
  if (support) {
    pushZoneFromPair(
      zones,
      'support',
      firstNumber(support.upper, support.level),
      firstNumber(support.lower, support.level),
    );
  }

  const resistance = payload.nearest_resistance_zone;
  if (resistance) {
    pushZoneFromPair(
      zones,
      'resistance',
      firstNumber(resistance.upper, resistance.level),
      firstNumber(resistance.lower, resistance.level),
    );
  }

  for (const zone of (payload.active_fvgs || []).filter((item) => !item?.mitigated).slice(0, 2)) {
    const top = firstNumber(zone.top);
    const bottom = firstNumber(zone.bottom);
    if (top == null || bottom == null) continue;
    const type = typeof zone.type === 'string' ? zone.type.toLowerCase() : '';
    pushZoneFromPair(zones, type.includes('bull') ? 'fvg_bull' : 'fvg_bear', top, bottom);
  }

  for (const zone of (payload.order_blocks || []).filter((item) => !item?.mitigated).slice(0, 2)) {
    const top = firstNumber(zone.top);
    const bottom = firstNumber(zone.bottom);
    if (top == null || bottom == null) continue;
    const type = typeof zone.type === 'string' ? zone.type.toLowerCase() : '';
    pushZoneFromPair(zones, type.includes('bull') ? 'ob_bull' : 'ob_bear', top, bottom);
  }

  return zones;
}

// Engine B single-level markers that survive in clean mode as thin colored lines
// (no axis labels). Zone-style overlays (support/resistance/FVG/OB) are skipped
// here because the band primitive covers them.
function engineBSingleLevelLines(
  payload: EngineBOverlayPayload | null,
  enabled: boolean,
): EngineBSingleLevel[] {
  if (!enabled || payload?.overlay_source !== 'engine_b') return [];
  const lines: EngineBSingleLevel[] = [];

  const bos = payload.bos_data || {};
  const bosHigh = firstNumber(bos.last_broken_high, bos.level, bos.price);
  const bosLow = firstNumber(bos.last_broken_low);
  if (bosHigh != null) lines.push({ label: 'BOS', price: bosHigh, color: 'rgba(96, 165, 250, 0.85)' });
  if (bosLow != null && bosLow !== bosHigh) {
    lines.push({ label: 'BOS', price: bosLow, color: 'rgba(96, 165, 250, 0.65)' });
  }

  const choch = payload.choch_data || {};
  const chochLevel = firstNumber(choch.choch_level, choch.level, choch.price);
  if (chochLevel != null) {
    lines.push({ label: 'CHOCH', price: chochLevel, color: 'rgba(168, 85, 247, 0.85)' });
  }

  const breakerLevel = firstNumber(payload.breaker_block?.level);
  if (breakerLevel != null) {
    lines.push({
      label: 'Breaker',
      price: breakerLevel,
      color: 'rgba(56, 189, 248, 0.80)',
      style: LineStyle.Dashed,
    });
  }
  return lines;
}

interface RightEdgeLabelInput {
  id: string;
  text: string;
  price: number;
  color: string;
  priority: number;
}

function compactOverlayAxisLabel(label: string): string {
  const text = stripEngineBPrefix(label);
  if (/choch/i.test(text)) return 'CHOCH';
  if (/bos/i.test(text)) return 'BOS';
  if (/breaker/i.test(text)) return 'Breaker';
  if (/support/i.test(text)) return 'SUP';
  if (/resistance/i.test(text)) return 'RES';
  if (/fvg/i.test(text)) return 'FVG';
  if (/\bob\b/i.test(text)) return 'OB';
  return compactEngineBLabel(text);
}

function buildEngineBRightEdgeLabelInputs(
  payload: EngineBOverlayPayload | null,
  enabled: boolean,
  cleanMode: boolean,
): RightEdgeLabelInput[] {
  if (!enabled || payload?.overlay_source !== 'engine_b') return [];
  const lines = cleanMode
    ? engineBSingleLevelLines(payload, enabled).map((line) => ({
        label: line.label,
        price: line.price,
        color: line.color,
      }))
    : engineBOverlayLines(payload, enabled).map((line) => ({
        label: line.label,
        price: line.price,
        color: line.color,
      }));

  return lines.map((line, index) => ({
    id: `engine-b-${index}-${line.label}-${line.price}`,
    text: compactOverlayAxisLabel(line.label),
    price: line.price,
    color: line.color,
    priority: labelPriorityForText(compactOverlayAxisLabel(line.label)),
  }));
}

function resolveRightEdgeLabels(
  series: ISeriesApi<'Candlestick', Time> | null,
  inputs: RightEdgeLabelInput[],
  currentPrice: number | null | undefined,
  paneHeightPx: number,
): RightEdgeLabel[] {
  if (!series) return [];
  const labels: RightEdgeLabel[] = [];
  for (const input of inputs) {
    const y = series.priceToCoordinate(input.price);
    if (y == null || !Number.isFinite(y)) continue;
    labels.push({
      id: input.id,
      text: input.text,
      y,
      priority: input.priority,
      color: input.color,
      side: 'right',
    });
  }
  if (currentPrice != null && Number.isFinite(currentPrice)) {
    const y = series.priceToCoordinate(currentPrice);
    if (y != null && Number.isFinite(y)) {
      labels.push({
        id: 'current-price',
        text: 'PRICE',
        y,
        priority: RIGHT_EDGE_LABEL_PRIORITY.currentPrice,
        color: 'rgba(245, 240, 232, 0.92)',
        side: 'right',
      });
    }
  }
  return labels;
}

// ISeriesPrimitive that paints translucent horizontal bands for Engine B zones
// across the full pane width. Drawn at z-order 'bottom' so candles remain
// readable on top of the band fill.
class EngineBZonePaneView implements IPrimitivePaneView {
  private readonly primitive: EngineBZonePrimitive;

  constructor(primitive: EngineBZonePrimitive) {
    this.primitive = primitive;
  }

  zOrder(): 'bottom' { return 'bottom'; }

  renderer(): IPrimitivePaneRenderer | null {
    return new EngineBZoneRenderer(this.primitive);
  }
}

class EngineBZoneRenderer implements IPrimitivePaneRenderer {
  private readonly primitive: EngineBZonePrimitive;

  constructor(primitive: EngineBZonePrimitive) {
    this.primitive = primitive;
  }

  draw(target: CanvasRenderingTarget2D): void {
    const zones = this.primitive.zones();
    const series = this.primitive.series();
    if (!series || zones.length === 0) return;

    // Do not replace filled support/resistance/FVG zones with line-only overlays;
    // this filled-zone style is the preferred readable Engine B visual.
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      for (const zone of zones) {
        const yTop = series.priceToCoordinate(zone.top);
        const yBot = series.priceToCoordinate(zone.bottom);
        if (yTop == null || yBot == null) continue;
        const y1 = Math.min(yTop, yBot);
        const y2 = Math.max(yTop, yBot);
        const h = Math.max(1, y2 - y1);
        context.save();
        context.fillStyle = zone.fill;
        context.fillRect(0, y1, mediaSize.width, h);
        context.strokeStyle = zone.stroke;
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(0, y1 + 0.5);
        context.lineTo(mediaSize.width, y1 + 0.5);
        context.moveTo(0, y2 - 0.5);
        context.lineTo(mediaSize.width, y2 - 0.5);
        context.stroke();
        context.restore();
      }
    });
  }
}

class EngineBZonePrimitive implements ISeriesPrimitive<Time> {
  private _series: ISeriesApi<'Candlestick', Time> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _zones: EngineBZoneFill[] = [];
  private readonly _paneViews: readonly IPrimitivePaneView[];

  constructor() {
    this._paneViews = [new EngineBZonePaneView(this)];
  }

  attached({ series, requestUpdate }: SeriesAttachedParameter<Time>): void {
    this._series = series as ISeriesApi<'Candlestick', Time>;
    this._requestUpdate = requestUpdate;
  }

  detached(): void {
    this._series = null;
    this._requestUpdate = null;
  }

  setZones(zones: EngineBZoneFill[]): void {
    this._zones = zones;
    this._requestUpdate?.();
  }

  zones(): EngineBZoneFill[] { return this._zones; }
  series(): ISeriesApi<'Candlestick', Time> | null { return this._series; }

  updateAllViews(): void { /* views read directly from primitive state */ }

  paneViews(): readonly IPrimitivePaneView[] { return this._paneViews; }
}

function normalizeSwingText(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const text = value.replace(/_/g, ' ').trim();
  return text ? text.slice(0, 28) : null;
}

function engineBMarkers(
  payload: EngineBOverlayPayload | null,
  rows: CandlestickData<Time>[],
  enabled: boolean,
): SeriesMarker<Time>[] {
  if (!enabled || payload?.overlay_source !== 'engine_b' || rows.length === 0) return [];
  const last = rows[rows.length - 1];
  const markers: SeriesMarker<Time>[] = [];
  const swing = normalizeSwingText(payload.current_swing_sequence);
  if (swing) {
    markers.push({
      time: last.time,
      position: 'aboveBar',
      color: 'rgba(250, 204, 21, 0.95)',
      shape: 'circle',
      text: swing,
    });
  }
  const macroSwing = normalizeSwingText(payload.macro_swing_sequence);
  if (macroSwing && macroSwing !== swing) {
    markers.push({
      time: last.time,
      position: 'belowBar',
      color: 'rgba(56, 189, 248, 0.95)',
      shape: 'circle',
      text: macroSwing,
    });
  }
  if (payload.bos_confirmed) {
    markers.push({
      time: last.time,
      position: 'belowBar',
      color: 'rgba(250, 204, 21, 0.95)',
      shape: 'arrowUp',
      text: 'BOS',
    });
  }
  if (payload.choch_confirmed) {
    markers.push({
      time: last.time,
      position: 'aboveBar',
      color: 'rgba(168, 85, 247, 0.95)',
      shape: 'arrowDown',
      text: 'CHOCH',
    });
  }
  if (payload.liquidity_sweep) {
    markers.push({
      time: last.time,
      position: 'inBar',
      color: 'rgba(56, 189, 248, 0.95)',
      shape: 'square',
      text: 'Sweep',
    });
  }
  return markers.slice(0, 6);
}

function isBybitLiveTickProvider(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.toLowerCase().startsWith('bybit');
}

function isBybitCryptoExecution(payload: CandleApiResponse | null | undefined): boolean {
  return payload?.asset_group === 'crypto' && payload?.execution_provider === 'bybit';
}

function cryptoAtr14LegendLabel(
  payload: CandleApiResponse | null | undefined,
  atrPeriod = 14,
): string {
  const tf = payload?.atr_timeframe || payload?.tf;
  const provider = titleCaseProvider(payload?.atr_provider || payload?.chart_provider);
  const base = `ATR${atrPeriod}`;
  return tf ? `${base} ${tf} ${provider}` : base;
}

function buildChartFeedSummary(payload: CandleApiResponse | null | undefined): string | null {
  if (!payload) return null;
  const parts: string[] = [];
  const candleProvider = payload.candle_provider || payload.chart_provider;
  if (candleProvider) parts.push(`candles ${titleCaseProvider(candleProvider)}`);
  if (payload.scoring_provider) parts.push(`scoring ${titleCaseProvider(payload.scoring_provider)}`);
  if (payload.vwap_provider) parts.push(`vwap ${titleCaseProvider(payload.vwap_provider)}`);
  if (payload.live_tick_provider) {
    let live = `live ${titleCaseProvider(payload.live_tick_provider)}`;
    if (payload.fallback_used && payload.fallback_reason) {
      live += ` (${payload.fallback_reason})`;
    }
    parts.push(live);
  }
  if (payload.execution_provider) {
    parts.push(`exec ${titleCaseProvider(payload.execution_provider)}`);
  }
  return parts.length > 0 ? parts.join(' | ') : null;
}

function readableCandlePolicy(value: string | null | undefined, lastConfirmed: boolean | null): string {
  if (lastConfirmed === true) return 'confirmed';
  if (lastConfirmed === false) return 'forming';
  if (!value) return 'policy unknown';
  return value.replace(/_/g, '-');
}

function parityDelta(chartVal: number | undefined, signalVal: number | null | undefined, precision = 5): string {
  if (typeof chartVal !== 'number' || !Number.isFinite(chartVal) || signalVal == null || !Number.isFinite(signalVal)) {
    return 'n/a';
  }
  const delta = Math.abs(chartVal - signalVal);
  const rel = signalVal !== 0 ? delta / Math.abs(signalVal) : delta;
  if (delta < 10 ** -precision || rel < 0.001) return 'match';
  return `delta ${fmtNum(delta, precision)}`;
}

function buildEngineAParityRows(
  signal: EngineASignal | null,
  chartLatest?: ChartStudySnapshot['latest'],
  chartPayload?: CandleApiResponse | null,
): { label: string; value: string }[] {
  const diagnostics = asRecord(signal?.factorDiagnostics);
  const trendCoherence = asRecord(diagnostics.trendCoherence);
  const factorScores = asRecord(signal?.factorScores);
  const candleFetchMeta = asRecord(signal?.candleFetchMeta);
  const directionalRamp = resolveDirectionalRampDisplay(signal);
  const trendCoherenceRows = resolveTrendCoherenceRows(diagnostics);
  const timeframeMap = ['D1', 'H4', 'H1']
    .map((tf) => {
      const row = asRecord(candleFetchMeta[tf]);
      const feed = firstString(row.signalFeed, row.primary_provider, row.provider, row.source);
      return `${tf}:${feed || '-'}`;
    })
    .join(' ');
  const signalPrice = firstNumber(signal?.price, signal?.entry);
  const signalAtr = firstNumber(signal?.atr, signal?.atrDiagnostics?.atr_value);
  const signalAdx = firstNumber(diagnostics.adxValue, trendCoherence.adx);
  const numericRows: { label: string; value: string }[] = chartLatest
    ? [
        {
          label: 'close',
          value: `${formatIndicatorValue(chartLatest.close as number | undefined)} vs ${formatIndicatorValue(signalPrice ?? undefined)} (${parityDelta(chartLatest.close as number | undefined, signalPrice)})`,
        },
        {
          label: 'ema21',
          value: `${formatIndicatorValue(chartLatest.ema21)} vs scan n/a (${parityDelta(chartLatest.ema21, null)})`,
        },
        {
          label: 'ema50',
          value: formatIndicatorValue(chartLatest.ema50),
        },
        {
          label: 'vwap',
          value: formatIndicatorValue(chartLatest.vwap),
        },
        {
          label: 'adx14',
          value: `${formatIndicatorValue(chartLatest.adx14, 2)} vs ${formatIndicatorValue(signalAdx ?? undefined, 2)} (${parityDelta(chartLatest.adx14, signalAdx, 2)})`,
        },
        {
          label: 'atr14',
          value: `${formatIndicatorValue(chartLatest.atr14)} vs level ${formatIndicatorValue(signalAtr ?? undefined)} (${parityDelta(chartLatest.atr14, signalAtr)})`,
        },
        {
          label: 'volume',
          value: formatIndicatorValue(chartLatest.volume, 0),
        },
        {
          label: 'volume_ma',
          value: formatIndicatorValue(chartLatest.volumeMa, 0),
        },
      ]
    : [];
  const scoringProvider = chartPayload?.scoring_provider
    ? titleCaseProvider(chartPayload.scoring_provider)
    : null;

  return [
    ...(scoringProvider ? [{ label: 'scoring_provider', value: scoringProvider }] : []),
    { label: 'TF map', value: timeframeMap },
    ...numericRows,
    { label: 'EMA votes', value: JSON.stringify(trendCoherence.votes || trendCoherence.ema_votes || trendCoherence.emaVotes || []) },
    { label: 'agreement_count', value: trendCoherenceRows.agreement.text },
    { label: 'coherence_ratio', value: trendCoherenceRows.ratio.text },
    { label: 'trend_score', value: formatIndicatorValue(firstNumber(factorScores.trend, diagnostics.directionalScore) ?? undefined, 4) },
    { label: 'directional_ramp', value: directionalRamp.text },
    { label: 'price_inside_ema_cluster', value: String(trendCoherence.price_inside_ema_cluster ?? 'Unavailable') },
    { label: 'at_or_below_resistance', value: String(trendCoherence.at_or_below_resistance ?? 'Unavailable') },
    { label: 'at_or_above_support', value: String(trendCoherence.at_or_above_support ?? 'Unavailable') },
    {
      label: 'nearest_ema_resistance_distance_atr',
      value: formatIndicatorValue(firstNumber(trendCoherence.nearest_ema_resistance_distance_atr) ?? undefined, 4),
    },
    {
      label: 'nearest_ema_support_distance_atr',
      value: formatIndicatorValue(firstNumber(trendCoherence.nearest_ema_support_distance_atr) ?? undefined, 4),
    },
  ];
}

// --- Engine A side panel UI (unchanged behavior) ---------------------

function NumberRow({ label, value }: { label: string; value: unknown }) {
  const numeric = firstNumber(value);
  return (
    <div className="flex items-center justify-between gap-3 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono text-foreground">{numeric === null ? 'Unavailable' : fmtNum(numeric, 5)}</span>
    </div>
  );
}

function TextRow({ label, value }: { label: string; value: unknown }) {
  const text = firstString(value);
  return (
    <div className="flex items-center justify-between gap-3 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="break-words text-right text-foreground">{text || 'Unavailable'}</span>
    </div>
  );
}

function DiagnosticRow({ label, display }: { label: string; display: DiagnosticDisplay }) {
  return (
    <div className="flex items-center justify-between gap-3 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span
        className={`break-words text-right ${display.isUnavailable ? 'text-muted-foreground' : 'font-mono text-foreground'}`}
      >
        {display.text}
      </span>
    </div>
  );
}

function DiagnosticBlock({ label, value }: { label: string; value: unknown }) {
  const record = asRecord(value);
  if (!Object.keys(record).length) return null;
  return (
    <div className="space-y-1 text-xs">
      <div className="text-muted-foreground">{label}</div>
      <pre className="whitespace-pre-wrap break-words rounded border border-border/50 bg-background/60 p-2 text-[11px] text-foreground">
        {JSON.stringify(record, null, 2)}
      </pre>
    </div>
  );
}

function EngineASidePanel({
  signal,
  liveTick,
  chartPayload,
}: {
  signal: EngineASignal | null;
  liveTick: LiveTick | null;
  chartPayload: CandleApiResponse | null;
}) {
  const factorScores = asRecord(signal?.factorScores);
  const diagnostics = asRecord(signal?.factorDiagnostics);
  const trendCoherence = asRecord(diagnostics.trendCoherence);
  const feedStatus = asRecord(diagnostics.feedStatus);
  const engineAAssetDiagnostics = asRecord(diagnostics.engineAAssetDiagnostics);
  const cryptoEngineADiagnostics = asRecord(
    diagnostics.cryptoEngineADiagnostics ?? diagnostics.crypto_engine_a_diagnostics,
  );
  const isCryptoSignal = firstString(signal?.type)?.toLowerCase() === 'crypto';
  const momentumQuality = firstNumber(diagnostics.momentumQuality, diagnostics.momentum_quality, factorScores.momentum);
  const addonUnsupported = Boolean(diagnostics.addonUnsupported || diagnostics.addon_unsupported);
  const addonValue = firstNumber(diagnostics.addon_value, diagnostics.addonValue, factorScores.addon);
  const addonStatus = addonUnsupported
    ? 'UNAVAILABLE'
    : firstString(feedStatus.addon) || (addonValue === null ? 'missing' : addonValue > 0 ? 'confirming' : addonValue < 0 ? 'opposing' : 'neutral');
  const addonLabel = firstString(signal?.type)?.toLowerCase() === 'forex' ? 'Carry addon' : 'Addon';
  const atrDiagnostics = asRecord(signal?.atrDiagnostics);
  const directionalRamp = resolveDirectionalRampDisplay(signal);
  const trendCoherenceRows = resolveTrendCoherenceRows(diagnostics);
  const feedAddon = resolveFeedAddonDisplay(diagnostics);
  const atrProvenance = resolveAtrProvenanceRows(atrDiagnostics);
  const candleFetchRows = resolveCandleFetchMetaRows(signal?.candleFetchMeta);
  const chartFeedSummary = buildChartFeedSummary(chartPayload);
  const showDebugFooter = isFrontendDebugVisible();
  const frontendBuildLabel = showDebugFooter ? resolveFrontendBuildLabel() : null;
  const direction = normalizeDirection(signal?.direction);
  const score = firstNumber(signal?.confluenceScore, signal?.score, signal?.final_score);
  const maxScore = firstNumber(signal?.maxScore, signal?.max_score) ?? 3;
  const threshold = firstNumber(signal?.threshold, signal?.scoreThreshold, signal?.score_threshold);
  const swingLevels = Array.isArray(signal?.swingLevels) ? signal?.swingLevels : Array.isArray(signal?.priorSwingLevels) ? signal?.priorSwingLevels : [];

  return (
    <aside className="min-w-0 space-y-3 rounded-md border bg-card/70 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs uppercase tracking-wide text-muted-foreground">Engine A Candidate</p>
          <h3 className="truncate text-sm font-semibold">{displaySymbol(signal) || 'No candidate'}</h3>
        </div>
        <Badge variant={direction === 'SHORT' ? 'destructive' : 'secondary'}>{direction || 'UNAVAILABLE'}</Badge>
      </div>

      <section className="space-y-2 rounded-md border border-border/60 p-2">
        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">Score</span>
          <span className="font-mono">{score === null ? 'Unavailable' : `${fmtNum(score, 2)} / ${fmtNum(maxScore, 1)}`}</span>
        </div>
        <NumberRow label="Threshold" value={threshold} />
        <NumberRow label="Entry" value={firstNumber(signal?.entry, signal?.price)} />
        <NumberRow label="Live price" value={liveTick?.price} />
        <TextRow
          label="Live tick"
          value={
            liveTick
              ? `${fmtNum(liveTick.ageSec, 0)}s ${titleCaseProvider(liveTick.source) || 'tick'}`
              : null
          }
        />
        <TextRow label="Chart feeds" value={chartFeedSummary} />
        <NumberRow label="SL" value={signal?.sl} />
        <NumberRow label="TP" value={firstNumber(signal?.tp, signal?.tp1)} />
      </section>

      <section className="space-y-2 rounded-md border border-border/60 p-2">
        <div className="flex items-center gap-2 text-xs font-semibold">
          <BarChart3 className="h-3.5 w-3.5" />
          Trend
        </div>
        <NumberRow label="Trend score" value={factorScores.trend} />
        <DiagnosticRow label="Directional ramp" display={directionalRamp} />
        <DiagnosticRow
          label="Min directional"
          display={resolveNumericDisplay(
            firstNumber(diagnostics.minDirectional, diagnostics.minDirectionalThreshold, diagnostics.min_directional_threshold),
            5,
            'factorDiagnostics.minDirectional missing from payload',
          )}
        />
        <DiagnosticRow
          label="Effective min directional"
          display={resolveNumericDisplay(
            firstNumber(diagnostics.effectiveMinDirectional, diagnostics.effective_min_directional),
            5,
            'factorDiagnostics.effectiveMinDirectional missing from payload',
          )}
        />
        <DiagnosticRow label="Agreement count" display={trendCoherenceRows.agreement} />
        <DiagnosticRow label="Coherence ratio" display={trendCoherenceRows.ratio} />
        <DiagnosticBlock label="Trend coherence" value={trendCoherence} />
      </section>

      <section className="space-y-2 rounded-md border border-border/60 p-2">
        <div className="flex items-center gap-2 text-xs font-semibold">
          <Layers className="h-3.5 w-3.5" />
          Momentum / Addon
        </div>
        <NumberRow label="Momentum quality" value={momentumQuality} />
        <NumberRow label="Momentum score" value={factorScores.momentum} />
        <NumberRow label={`${addonLabel} value`} value={addonValue} />
        <TextRow label={`${addonLabel} status`} value={addonStatus} />
        <DiagnosticRow label="Feed addon" display={feedAddon} />
        <DiagnosticBlock label="Feed status" value={feedStatus} />
        <DiagnosticBlock label="Engine A asset diagnostics" value={engineAAssetDiagnostics} />
      </section>

      {isCryptoSignal && Object.keys(cryptoEngineADiagnostics).length > 0 && (
        <section className="space-y-2 rounded-md border border-border/60 p-2">
          <div className="text-xs font-semibold">Crypto late-trend</div>
          <TextRow
            label="Late-trend risk"
            value={String(cryptoEngineADiagnostics.crypto_late_trend_risk ?? cryptoEngineADiagnostics.cryptoLateTrendRisk ?? '-')}
          />
          <TextRow
            label="Adjustment applied"
            value={String(
              cryptoEngineADiagnostics.late_trend_adjustment_applied
                ?? cryptoEngineADiagnostics.lateTrendAdjustmentApplied
                ?? '-',
            )}
          />
          <TextRow
            label="VWAP extended"
            value={String(cryptoEngineADiagnostics.vwap_extended ?? cryptoEngineADiagnostics.vwapExtended ?? '-')}
          />
          <NumberRow
            label="VWAP distance (ATR)"
            value={firstNumber(
              cryptoEngineADiagnostics.vwap_distance_atr,
              cryptoEngineADiagnostics.vwapDistanceAtr,
            )}
          />
          <TextRow
            label="ADX falling"
            value={String(cryptoEngineADiagnostics.adx_falling ?? cryptoEngineADiagnostics.adxFalling ?? '-')}
          />
          <TextRow
            label="Reason"
            value={firstString(
              cryptoEngineADiagnostics.crypto_late_trend_reason,
              cryptoEngineADiagnostics.cryptoLateTrendReason,
            )}
          />
          <DiagnosticBlock label="Crypto engine A diagnostics" value={cryptoEngineADiagnostics} />
        </section>
      )}

      <section className="space-y-2 rounded-md border border-border/60 p-2">
        <div className="text-xs font-semibold">Risk</div>
        <NumberRow label="ATR" value={firstNumber(signal?.atr, atrDiagnostics.atr, atrDiagnostics.atr_value)} />
        <DiagnosticRow label="ATR timeframe" display={atrProvenance.timeframe} />
        <DiagnosticRow label="ATR source" display={atrProvenance.source} />
        <DiagnosticRow label="ATR candle last ts" display={atrProvenance.candleLastTs} />
        <DiagnosticRow label="ATR age seconds" display={atrProvenance.ageSeconds} />
        <DiagnosticRow label="ATR confirmed only" display={atrProvenance.confirmedOnly} />
        <NumberRow label="RR" value={firstNumber(signal?.rr, signal?.rr1)} />
        <DiagnosticBlock label="ATR diagnostics" value={atrDiagnostics} />
      </section>

      <section className="space-y-2 rounded-md border border-border/60 p-2">
        <div className="text-xs font-semibold">Candle Fetch</div>
        {candleFetchRows.map((row) => (
          <DiagnosticRow key={row.label} label={row.label} display={row.display} />
        ))}
      </section>

      <section className="space-y-2 rounded-md border border-border/60 p-2">
        <div className="text-xs font-semibold">Prior Swing Levels</div>
        {swingLevels.length > 0 ? (
          swingLevels.slice(0, 4).map((level, index) => (
            <TextRow key={`${index}-${JSON.stringify(level)}`} label={`Level ${index + 1}`} value={JSON.stringify(level)} />
          ))
        ) : (
          <p className="text-xs text-muted-foreground">Unavailable — Engine A SL/TP is ATR-based; no structural swing levels supplied.</p>
        )}
      </section>

      <p className="text-[11px] leading-4 text-muted-foreground">
        Entry, SL, TP, and swing levels are side-panel values only. The chart is not drawing custom levels.
      </p>
      {showDebugFooter && frontendBuildLabel && (
        <p className="text-[10px] leading-4 text-muted-foreground/70">Frontend bundle: {frontendBuildLabel}</p>
      )}
    </aside>
  );
}

function IndicatorSwitch({
  label,
  checked,
  onCheckedChange,
}: {
  label: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-xs">
      <Switch checked={checked} onCheckedChange={onCheckedChange} />
      {label}
    </label>
  );
}

function ProviderBadge({ payload }: { payload: CandleApiResponse | null }) {
  if (!payload?.chart_provider && !payload?.candle_provider) return null;
  const status = payload.provider_mismatch ? 'fallback' : 'execution-grade';
  const candleLabel = titleCaseProvider(payload.candle_provider || payload.chart_provider);
  const liveLabel = payload.live_tick_provider ? titleCaseProvider(payload.live_tick_provider) : null;
  const execLabel = payload.execution_provider ? titleCaseProvider(payload.execution_provider) : null;
  return (
    <div className="flex flex-wrap items-center gap-1">
      <Badge variant={payload.provider_mismatch ? 'destructive' : 'secondary'} className="h-7 gap-1 text-[10px]">
        candles {candleLabel}
        <span className="text-muted-foreground">/ {status}</span>
      </Badge>
      {liveLabel && (
        <Badge variant="outline" className="h-7 text-[10px]">
          live {liveLabel}
          {payload.fallback_used && payload.fallback_reason ? ` | ${payload.fallback_reason}` : ''}
        </Badge>
      )}
      {execLabel && (
        <Badge variant="outline" className="h-7 text-[10px]">
          exec {execLabel}
        </Badge>
      )}
    </div>
  );
}

function drawCaptureLabels(
  outputCtx: CanvasRenderingContext2D,
  captureEl: HTMLElement,
  captureRect: DOMRect,
) {
  const labels = Array.from(captureEl.querySelectorAll('[data-chart-capture-label]')) as HTMLElement[];
  for (const label of labels) {
    const text = (label.textContent || '').trim();
    if (!text) continue;
    const rect = label.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const style = window.getComputedStyle(label);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    const x = rect.left - captureRect.left;
    const y = rect.top - captureRect.top;
    const bg = style.backgroundColor;
    if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') {
      outputCtx.fillStyle = bg;
      outputCtx.fillRect(x, y, rect.width, rect.height);
    }
    outputCtx.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    outputCtx.fillStyle = style.color || '#f5f0e8';
    outputCtx.textBaseline = 'middle';
    outputCtx.fillText(text, x + 6, y + rect.height / 2, Math.max(10, rect.width - 12));
  }
}

function CaptureLabel({
  children,
  className = '',
  style,
}: {
  children: string;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <span
      data-chart-capture-label
      className={`inline-flex items-center gap-1 rounded border border-border/50 bg-muted/80 px-1.5 py-0.5 text-[10px] font-mono leading-none text-foreground ${className}`}
      style={style}
    >
      {children}
    </span>
  );
}

/** Metadata strip above the candle canvas — never absolutely positioned over price. */
function ChartMetadataStrip({ children }: { children: ReactNode }) {
  return (
    <div className="shrink-0 border-b border-border/50 bg-card/95 px-2.5 py-2">
      <div className="flex min-w-0 flex-col gap-2">{children}</div>
    </div>
  );
}

interface IndicatorLegendValue {
  definition: IndicatorDefinition;
  value?: number;
  precision?: number;
}

function IndicatorLegendItem({ item }: { item: IndicatorLegendValue }) {
  return (
    <CaptureLabel style={{ color: item.definition.color }}>
      {`${item.definition.label} ${formatIndicatorValue(item.value, item.precision ?? 5)}`}
    </CaptureLabel>
  );
}

// Compact color-identity chip used by the clean-mode legend strip. Color square
// + label, no value — values live in the IndicatorLegendItem strip when Quant
// Debug is on.
interface LegendChipSpec {
  key: string;
  label: string;
  color: string;
  swatch: 'line' | 'band';
}

function LegendChip({ spec }: { spec: LegendChipSpec }) {
  // Use a coloured glyph prefix so the chip's identity survives the screenshot
  // rasterizer in drawCaptureLabels (which only paints text + bg, not nested
  // swatch divs). The chip text is single-coloured to match the swatch.
  const glyph = spec.swatch === 'band' ? '■' : '─'; // ■ or ─
  return (
    <CaptureLabel style={{ color: spec.color }}>{`${glyph} ${spec.label}`}</CaptureLabel>
  );
}

function buildCleanLegendChips(args: {
  isCryptoChart: boolean;
  showVwapOnChart: boolean;
  ema20: boolean;
  ema21: boolean;
  ema50: boolean;
  ema200: boolean;
  dema200: boolean;
  vwap: boolean;
  emaLabels: { trend: string; momentum: string; long: string; demaLong: string };
  engineBEnabled: boolean;
  engineBPayload: EngineBOverlayPayload | null;
}): LegendChipSpec[] {
  const chips: LegendChipSpec[] = [];
  if (args.isCryptoChart) {
    if (args.ema21) chips.push({ key: 'ema21', label: args.emaLabels.trend, color: INDICATOR_COLORS.ema21, swatch: 'line' });
  } else if (args.ema20) {
    chips.push({ key: 'ema20', label: args.emaLabels.trend, color: INDICATOR_COLORS.ema20, swatch: 'line' });
  }
  if (args.ema50) chips.push({ key: 'ema50', label: args.emaLabels.momentum, color: INDICATOR_COLORS.ema50, swatch: 'line' });
  if (args.ema200) chips.push({ key: 'ema200', label: args.emaLabels.long, color: INDICATOR_COLORS.ema200, swatch: 'line' });
  if (!args.isCryptoChart && args.dema200) {
    chips.push({ key: 'dema200', label: args.emaLabels.demaLong, color: INDICATOR_COLORS.dema200, swatch: 'line' });
  }
  if (args.showVwapOnChart && args.vwap) {
    chips.push({ key: 'vwap', label: 'VWAP', color: INDICATOR_COLORS.vwap, swatch: 'line' });
  }
  if (args.engineBEnabled && args.engineBPayload?.overlay_source === 'engine_b') {
    const payload = args.engineBPayload;
    if (payload.nearest_support_zone) {
      chips.push({ key: 'support', label: 'Support', color: ENGINE_B_ZONE_STYLE.support.stroke, swatch: 'band' });
    }
    if (payload.nearest_resistance_zone) {
      chips.push({ key: 'resistance', label: 'Resistance', color: ENGINE_B_ZONE_STYLE.resistance.stroke, swatch: 'band' });
    }
    const fvgs = (payload.active_fvgs || []).filter((item) => !item?.mitigated);
    if (fvgs.some((zone) => typeof zone.type === 'string' && zone.type.toLowerCase().includes('bull'))) {
      chips.push({ key: 'fvg_bull', label: 'FVG bull', color: ENGINE_B_ZONE_STYLE.fvg_bull.stroke, swatch: 'band' });
    }
    if (fvgs.some((zone) => !(typeof zone.type === 'string' && zone.type.toLowerCase().includes('bull')))) {
      chips.push({ key: 'fvg_bear', label: 'FVG bear', color: ENGINE_B_ZONE_STYLE.fvg_bear.stroke, swatch: 'band' });
    }
    const obs = (payload.order_blocks || []).filter((item) => !item?.mitigated);
    if (obs.some((zone) => typeof zone.type === 'string' && zone.type.toLowerCase().includes('bull'))) {
      chips.push({ key: 'ob_bull', label: 'OB bull', color: ENGINE_B_ZONE_STYLE.ob_bull.stroke, swatch: 'band' });
    }
    if (obs.some((zone) => !(typeof zone.type === 'string' && zone.type.toLowerCase().includes('bull')))) {
      chips.push({ key: 'ob_bear', label: 'OB bear', color: ENGINE_B_ZONE_STYLE.ob_bear.stroke, swatch: 'band' });
    }
    if (payload.bos_confirmed) {
      chips.push({ key: 'bos', label: 'BOS', color: 'rgba(96, 165, 250, 0.85)', swatch: 'line' });
    }
    if (payload.choch_confirmed) {
      chips.push({ key: 'choch', label: 'CHOCH', color: 'rgba(168, 85, 247, 0.85)', swatch: 'line' });
    }
    if (firstNumber(payload.breaker_block?.level) != null) {
      chips.push({ key: 'breaker', label: 'Breaker', color: 'rgba(56, 189, 248, 0.80)', swatch: 'line' });
    }
  }
  return chips;
}

// --- Main panel ------------------------------------------------------

export default function TVChartPanel() {
  const { scanCacheA, tvChartIntent, clearTvChartIntent, showToast, isTestMode, setActivePanel, aiReviewProvider, setAiReviewProvider } = useStore();
  const { data: health } = useApiPoll<{ paper_mode?: boolean }>('/api/health', 60_000);
  const { priceEntryFor } = useLivePrices();
  const [pair, setPair] = useState('EURUSD');
  const [timeframe, setTimeframe] = useState('240');
  const [ema20, setEma20] = useState(true);
  const [ema21, setEma21] = useState(true);
  const [ema50, setEma50] = useState(true);
  const [ema200, setEma200] = useState(false);
  const [dema200, setDema200] = useState(false);
  const [vwapEnabled, setVwapEnabled] = useState(true);
  const [atr14, setAtr14] = useState(false);
  const [rsi14, setRsi14] = useState(false);
  const [adx14, setAdx14] = useState(true);
  const [volumeBars, setVolumeBars] = useState(true);
  const [volumeMa, setVolumeMa] = useState(true);
  const [engineAParityVisible, setEngineAParityVisible] = useState(false);
  const [showQuantDebug, setShowQuantDebug] = useState(false);
  const [showEngineBOverlays, setShowEngineBOverlays] = useState(true);
  // Full Stack Clean (default) = bands + compact legend, no repeated axis labels.
  // Debug = legacy behaviour where every Engine B line gets a right-axis title.
  const [chartMode, setChartMode] = useState<'clean' | 'debug'>('clean');

  const [candles, setCandles] = useState<CandleApiRow[] | null>(null);
  const [chartPayload, setChartPayload] = useState<CandleApiResponse | null>(null);
  const [chartTickPayload, setChartTickPayload] = useState<CandleApiResponse | null>(null);
  const [engineBOverlay, setEngineBOverlay] = useState<EngineBOverlayPayload | null>(null);
  const [engineBOverlayLoading, setEngineBOverlayLoading] = useState(false);
  const [engineBOverlayError, setEngineBOverlayError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);
  const [aiReview, setAiReview] = useState<AIChartReviewResponse | null>(null);
  const [aiReviewLoading, setAiReviewLoading] = useState<boolean>(false);
  const [aiReviewError, setAiReviewError] = useState<string | null>(null);
  const [intentSourceBadge, setIntentSourceBadge] = useState<string | null>(null);
  const [autoReviewStatus, setAutoReviewStatus] = useState<'idle' | 'pending' | 'running' | 'done'>('idle');
  const [activeWatches, setActiveWatches] = useState<SuggestedTradeWatch[]>([]);
  const [flagStatus, setFlagStatus] = useState<string | null>(null);
  const [flagLoading, setFlagLoading] = useState(false);
  const [confirmExecuteOpen, setConfirmExecuteOpen] = useState(false);
  const [executing, setExecuting] = useState(false);
  const {
    volumeMode,
    setVolumeMode,
    sizingOverride,
    setSizingOverride,
  } = useExecutionVolumeState('min_lot');
  const { exitMode, setExitMode } = useExitModeState('default');
  const [riskPreviewLine, setRiskPreviewLine] = useState<string | null>(null);
  const [timeframeAutoMode, setTimeframeAutoMode] = useState(true);
  const [intentSignal, setIntentSignal] = useState<EngineASignal | null>(null);
  const lastAppliedRouteKeyRef = useRef<string | null>(null);
  const aiReviewSymbolKeyRef = useRef<string | null>(null);
  const currentPairKeyRef = useRef<string | null>(symbolKey(pair));
  const appliedIntentIdRef = useRef<string | null>(null);
  const pendingAutoReviewRef = useRef(false);
  const autoReviewRanForIntentRef = useRef<string | null>(null);
  const autoReviewEarliestRunAtRef = useRef<number | null>(null);
  const chartRenderGenerationRef = useRef('');
  const chartPaintReadyGenerationRef = useRef<string | null>(null);
  const [chartPaintReadyTick, setChartPaintReadyTick] = useState(0);
  const [autoReviewDelayTick, setAutoReviewDelayTick] = useState(0);

  const candidateRows = useMemo(
    () => (Array.isArray(scanCacheA) ? scanCacheA.filter((row): row is EngineASignal => Boolean(row && typeof row === 'object')) : []),
    [scanCacheA],
  );
  const intentCandidateRows = useMemo(
    () => (intentSignal ? [intentSignal, ...candidateRows] : candidateRows),
    [intentSignal, candidateRows],
  );
  const defaultCandidate = useMemo(() => pickEngineACandidate(intentCandidateRows), [intentCandidateRows]);
  const chartCandidate = useMemo(() => findEngineACandidateForSymbol(intentCandidateRows, pair), [intentCandidateRows, pair]);
  const backendTf = TF_BACKEND_MAP[timeframe];
  const currentSymbolKey = symbolKey(pair);
  const timeframeRoute = useMemo(() => reviewTimeframeRoute(aiReview), [aiReview]);
  const timeframeRouteLabel = useMemo(() => timeframeRouteDisplay(timeframeRoute), [timeframeRoute]);
  const engineBDirection = normalizeDirection(chartCandidate?.direction) === 'SHORT' ? 'SHORT' : 'LONG';
  const isCryptoChart = chartPayload?.asset_group === 'crypto' || chartPayload?.pairType === 'crypto';
  const showVwapOnChart = isCryptoChart || Boolean(chartPayload?.engine_a_vwap_filter_enabled);
  const engineBOverlayPendingForReview = showEngineBOverlays && engineBOverlayLoading;
  const engineBOverlayStatus = !showEngineBOverlays
    ? 'disabled'
    : engineBOverlayLoading
      ? 'loading'
      : engineBOverlay?.overlay_source === 'engine_b'
        ? 'ready'
        : engineBOverlayError
          ? 'error'
          : 'unavailable';
  const engineBOverlayAgeSec = overlayAgeSeconds(engineBOverlay?.computed_at);
  const engineBOverlayStale =
    engineBOverlayStatus === 'ready'
    && engineBOverlayAgeSec != null
    && engineBOverlayAgeSec > ENGINE_B_OVERLAY_STALE_SEC;
  const lastCandleConfirmed = candles?.length ? candles[candles.length - 1]?.confirmed : null;
  const chartPayloadLiveTick = useMemo(() => liveTickFromChartPayload(chartPayload), [chartPayload]);
  const chartTickLiveTick = useMemo(() => liveTickFromChartPayload(chartTickPayload), [chartTickPayload]);
  const sharedLiveTick = useMemo(() => liveTickFromEntry(priceEntryFor(pair)), [pair, priceEntryFor]);
  const usesBybitChartTick =
    isBybitCryptoExecution(chartPayload) || isBybitLiveTickProvider(chartPayload?.live_tick_provider);
  const liveTick = usesBybitChartTick ? (chartTickLiveTick ?? chartPayloadLiveTick) : sharedLiveTick;
  const emaPeriods = useMemo(() => {
    const pp = chartPayload?.price_precision ?? engineBOverlay?.price_precision;
    const apiEma = chartPayload?.indicator_periods?.ema;
    const p = pp?.ema_periods ?? apiEma ?? {};
    return {
      trend: typeof p.trend === 'number' ? p.trend : 21,
      momentum: typeof p.momentum === 'number' ? p.momentum : 50,
      long: typeof p.long === 'number' ? p.long : 200,
    };
  }, [chartPayload?.price_precision, chartPayload?.indicator_periods?.ema, engineBOverlay?.price_precision]);
  const indicatorPeriods = useMemo(() => {
    const pp = chartPayload?.price_precision ?? engineBOverlay?.price_precision;
    const api = chartPayload?.indicator_periods;
    return {
      rsi: typeof pp?.rsi_period === 'number' ? pp.rsi_period : (typeof api?.rsi === 'number' ? api.rsi : 14),
      adx: typeof api?.adx === 'number' ? api.adx : 14,
      atr: typeof api?.atr === 'number' ? api.atr : 14,
    };
  }, [chartPayload?.price_precision, chartPayload?.indicator_periods, engineBOverlay?.price_precision]);
  const studyIndicatorDefs = useMemo(
    () => buildStudyIndicatorDefs(indicatorPeriods),
    [indicatorPeriods],
  );
  const emaLabels = useMemo(
    () => ({
      trend: `EMA${emaPeriods.trend}`,
      momentum: `EMA${emaPeriods.momentum}`,
      long: `EMA${emaPeriods.long}`,
      demaLong: `DEMA${emaPeriods.long}`,
    }),
    [emaPeriods],
  );
  const studySnapshot = useMemo(
    () => buildChartStudySnapshot(candles, liveTick, backendTf, isCryptoChart, showVwapOnChart, emaPeriods, indicatorPeriods),
    [candles, liveTick, backendTf, isCryptoChart, showVwapOnChart, emaPeriods, indicatorPeriods],
  );
  const assetGroupLabel = chartPayload?.asset_group || chartPayload?.pairType || 'unknown';
  const chartProviderLabel = titleCaseProvider(chartPayload?.chart_provider || chartPayload?.candlesSource);
  const candleProviderLabel = titleCaseProvider(chartPayload?.candle_provider || chartPayload?.chart_provider || chartPayload?.candlesSource);
  const liveTickProviderLabel = titleCaseProvider(chartPayload?.live_tick_provider || liveTick?.source || chartPayload?.chart_provider);
  const candlePolicyLabel = readableCandlePolicy(chartPayload?.candle_confirmed_policy, lastCandleConfirmed ?? null);
  const lastCandleLabel = formatUtcLabel(chartPayload?.last_candle_ts ?? candles?.[candles.length - 1]?.t);
  const liveTickFallbackHint =
    chartPayload?.fallback_used && chartPayload?.fallback_reason
      ? ` fallback ${chartPayload.fallback_reason}`
      : chartPayload?.websocket_active === false && chartPayload?.websocket_stale
        ? ' ws_stale'
        : '';
  const executionProviderLabel = chartPayload?.execution_provider
    ? titleCaseProvider(chartPayload.execution_provider)
    : null;
  const lastCandleChipLabel = formatLastCandleChip(chartPayload?.last_candle_ts ?? candles?.[candles.length - 1]?.t);
  const chartFeedIdentityChips = useMemo((): ChartFeedHeaderChipSpec[] => [
    { key: 'symbol', label: pair, title: `Symbol ${pair}` },
    { key: 'tf', label: backendTf || timeframe, title: `Timeframe ${backendTf || timeframe}` },
    { key: 'asset', label: assetGroupLabel, title: `Asset group ${assetGroupLabel}` },
    { key: 'engine', label: 'Engine A', title: 'Engine A chart surface' },
  ], [pair, backendTf, timeframe, assetGroupLabel]);
  const chartFeedDiagnosticsChips = useMemo((): ChartFeedHeaderChipSpec[] => {
    const chips: ChartFeedHeaderChipSpec[] = [];
    if (executionProviderLabel) {
      chips.push({
        key: 'exec',
        label: `Exec: ${executionProviderLabel}`,
        title: `Execution provider ${executionProviderLabel}`,
      });
    }
    chips.push(
      { key: 'chart', label: `Chart: ${chartProviderLabel}`, title: `Chart provider ${chartProviderLabel}` },
      { key: 'candles', label: `Candles: ${candleProviderLabel}`, title: `Candle provider ${candleProviderLabel}` },
      {
        key: 'live',
        label: `Live: ${liveTickProviderLabel}${liveTickFallbackHint}`,
        title: `Live tick provider ${liveTickProviderLabel}${liveTickFallbackHint}`,
      },
      { key: 'candle', label: `Candle: ${candlePolicyLabel}`, title: `Candle policy ${candlePolicyLabel}` },
      { key: 'last', label: lastCandleChipLabel, title: lastCandleLabel },
    );
    return chips;
  }, [
    executionProviderLabel,
    chartProviderLabel,
    candleProviderLabel,
    liveTickProviderLabel,
    liveTickFallbackHint,
    candlePolicyLabel,
    lastCandleChipLabel,
    lastCandleLabel,
  ]);
  const quantEma20 = showQuantDebug && ema20;
  const quantEma21 = showQuantDebug && ema21;
  const quantEma50 = showQuantDebug && ema50;
  const quantEma200 = showQuantDebug && ema200;
  const quantDema200 = showQuantDebug && dema200;
  const quantVwap = showQuantDebug && vwapEnabled;
  const quantAtr14 = showQuantDebug && atr14;
  const quantRsi14 = showQuantDebug && rsi14;
  const quantAdx14 = showQuantDebug && adx14;
  const quantVolumeBars = showQuantDebug && volumeBars;
  const quantVolumeMa = showQuantDebug && volumeMa;
  const chartRenderGeneration = studySnapshot.rows.length
    ? chartRenderGenerationKey(pair, backendTf, studySnapshot.rows.length)
    : null;
  const chartPaintReadyForReview = Boolean(
    chartRenderGeneration && chartPaintReadyGenerationRef.current === chartRenderGeneration,
  );
  const chartIndicatorsReadyForReview = useMemo(() => {
    if (!candles?.length) return false;
    const latest = studySnapshot.latest;
    const hasLatest = (key: IndicatorKey) => Number.isFinite(latest[key]);
    if (quantEma20 && !hasLatest('ema20')) return false;
    if (isCryptoChart && quantEma21 && !hasLatest('ema21')) return false;
    if (quantEma50 && !hasLatest('ema50')) return false;
    if (quantEma200 && !hasLatest('ema200')) return false;
    if (!isCryptoChart && quantDema200 && !hasLatest('dema200')) return false;
    if (showVwapOnChart && quantVwap && !hasLatest('vwap')) return false;
    if (quantAtr14 && !hasLatest('atr14')) return false;
    if (quantRsi14 && !hasLatest('rsi14')) return false;
    if (quantAdx14 && !hasLatest('adx14')) return false;
    if (isCryptoChart && quantVolumeBars && !Number.isFinite(latest.volume)) return false;
    if (isCryptoChart && quantVolumeBars && quantVolumeMa && !hasLatest('volumeMa')) return false;
    return true;
  }, [
    candles?.length,
    studySnapshot.latest,
    isCryptoChart,
    showVwapOnChart,
    quantEma20,
    quantEma21,
    quantEma50,
    quantEma200,
    quantDema200,
    quantVwap,
    quantAtr14,
    quantRsi14,
    quantAdx14,
    quantVolumeBars,
    quantVolumeMa,
  ]);
  const chartReviewPendingForReview =
    loading ||
    engineBOverlayPendingForReview ||
    !candles?.length ||
    !chartPaintReadyForReview ||
    !chartIndicatorsReadyForReview;
  const chartReviewBlockReason = chartReviewPendingForReview
    ? resolveChartReviewBlockReason({
        loading,
        engineBOverlayPending: engineBOverlayPendingForReview,
        hasCandles: Boolean(candles?.length),
        chartPaintReady: chartPaintReadyForReview,
        chartIndicatorsReady: chartIndicatorsReadyForReview,
      })
    : null;
  const pricePanelLegendItems = useMemo<IndicatorLegendValue[]>(() => {
    const latest = studySnapshot.latest;
    const items: IndicatorLegendValue[] = [];
    if (isCryptoChart) {
      if (quantEma21) items.push({ definition: { ...PRICE_PANEL_INDICATORS.ema21, label: emaLabels.trend }, value: latest.ema21 });
    } else if (quantEma20) {
      items.push({ definition: { ...PRICE_PANEL_INDICATORS.ema20, label: emaLabels.trend }, value: latest.ema20 });
    }
    if (quantEma50) items.push({ definition: { ...PRICE_PANEL_INDICATORS.ema50, label: emaLabels.momentum }, value: latest.ema50 });
    if (quantEma200) items.push({ definition: { ...PRICE_PANEL_INDICATORS.ema200, label: emaLabels.long }, value: latest.ema200 });
    if (!isCryptoChart && quantDema200) items.push({ definition: { ...PRICE_PANEL_INDICATORS.dema200, label: emaLabels.demaLong }, value: latest.dema200 });
    if (showVwapOnChart && quantVwap) items.push({ definition: PRICE_PANEL_INDICATORS.vwap, value: latest.vwap });
    return items;
  }, [studySnapshot.latest, isCryptoChart, showVwapOnChart, quantEma20, quantEma21, quantEma50, quantEma200, quantDema200, quantVwap, emaLabels]);
  const studyPanelLegendItems = useMemo<IndicatorLegendValue[]>(() => {
    const latest = studySnapshot.latest;
    const items: IndicatorLegendValue[] = [];
    // Study indicators are computed on the visible chart timeframe — tag the
    // label with that TF so they are not read as Engine A's gate values
    // (Engine A trend ADX is D1/H4 and trend EMAs/ATR are H4).
    const tfSuffix = backendTf ? ` ${backendTf}` : '';
    if (quantRsi14) {
      items.push({
        definition: { ...studyIndicatorDefs.rsi14, label: `${studyIndicatorDefs.rsi14.label}${tfSuffix}` },
        value: latest.rsi14,
        precision: 2,
      });
    }
    if (quantAdx14) {
      items.push({
        definition: { ...studyIndicatorDefs.adx14, label: `${studyIndicatorDefs.adx14.label}${tfSuffix}` },
        value: latest.adx14,
        precision: 2,
      });
    }
    if (quantAtr14) {
      items.push({
        definition: {
          ...studyIndicatorDefs.atr14,
          label: isCryptoChart
            ? cryptoAtr14LegendLabel(chartPayload, indicatorPeriods.atr)
            : `${studyIndicatorDefs.atr14.label}${tfSuffix}`,
        },
        value: latest.atr14,
      });
    }
    if (isCryptoChart && quantVolumeBars) items.push({ definition: STUDY_PANEL_INDICATORS.volume, value: latest.volume, precision: 0 });
    if (isCryptoChart && quantVolumeBars && quantVolumeMa) items.push({ definition: STUDY_PANEL_INDICATORS.volumeMa, value: latest.volumeMa, precision: 0 });
    return items;
  }, [studySnapshot.latest, isCryptoChart, chartPayload, backendTf, indicatorPeriods.atr, studyIndicatorDefs, quantRsi14, quantAdx14, quantAtr14, quantVolumeBars, quantVolumeMa]);
  const engineAParityRows = useMemo(
    () => buildEngineAParityRows(chartCandidate, studySnapshot.latest, chartPayload),
    [chartCandidate, studySnapshot.latest, chartPayload],
  );
  // Compact color-identity legend for clean mode. Always shows the lines actually
  // drawn on the chart and the Engine B band/marker colors currently present in
  // the overlay payload, so the AI screenshot includes the colour key.
  const cleanLegendChips = useMemo(
    () => buildCleanLegendChips({
      isCryptoChart,
      showVwapOnChart,
      ema20: quantEma20,
      ema21: quantEma21,
      ema50: quantEma50,
      ema200: quantEma200,
      dema200: quantDema200,
      vwap: quantVwap,
      emaLabels,
      engineBEnabled: showEngineBOverlays,
      engineBPayload: engineBOverlay,
    }),
    [isCryptoChart, showVwapOnChart, quantEma20, quantEma21, quantEma50, quantEma200, quantDema200, quantVwap, emaLabels, showEngineBOverlays, engineBOverlay],
  );

  // Derived preset label: "all" only when every indicator is on, otherwise "custom".
  const activePreset: PresetValue = showQuantDebug && (isCryptoChart ? ema21 && (!showVwapOnChart || vwapEnabled) && adx14 && volumeBars && volumeMa : ema20) && ema50 && ema200 && dema200 && rsi14 && atr14 ? 'all' : 'custom';

  function applyRecommendedTimeframe() {
    const recommendedCode = tfCodeForBackend(timeframeRoute?.autoSelectTf);
    if (!recommendedCode) return;
    setTimeframeAutoMode(true);
    lastAppliedRouteKeyRef.current = null;
    setTimeframe(recommendedCode);
  }

  function handleManualTimeframeSelect(tf: string) {
    setTimeframeAutoMode(false);
    setTimeframe(tf);
  }

  const applyPreset = (value: PresetValue) => {
    if (value === 'all') {
      setEma20(true);
      setEma21(true);
      setEma50(true);
      setEma200(true);
      setDema200(true);
      setVwapEnabled(true);
      setAtr14(true);
      setRsi14(true);
      setAdx14(true);
      setVolumeBars(true);
      setVolumeMa(true);
    }
    // 'custom' is passive — manual switch flips revert the label naturally.
  };

  const applyEngineAReviewLayout = () => {
    const candidate = chartCandidate || defaultCandidate;
    const isCrypto = String(candidate?.type || '').toLowerCase() === 'crypto';
    const candidateSymbol = displaySymbol(candidate);
    if (candidateSymbol) setPair(candidateSymbol);
    setShowQuantDebug(true);
    setTimeframeAutoMode(true);
    lastAppliedRouteKeyRef.current = null;
    setTimeframe(reviewTimeframeFor(candidate));
    setEma20(true);
    setEma21(true);
    setEma50(true);
    setEma200(true);
    setDema200(!isCrypto);
    setVwapEnabled(isCrypto);
    setAtr14(true);
    setRsi14(true);
    setAdx14(isCrypto);
    setVolumeBars(isCrypto);
    setVolumeMa(isCrypto);
  };

  useEffect(() => {
    currentPairKeyRef.current = currentSymbolKey;
  }, [currentSymbolKey]);

  useEffect(() => {
    if (!tvChartIntent?.id || tvChartIntent.id === appliedIntentIdRef.current) return;
    appliedIntentIdRef.current = tvChartIntent.id;
    const intentSymbol = String(tvChartIntent.symbol || '').toUpperCase().trim();
    if (!intentSymbol) return;
    setIntentSignal(isEngineSignalLike(tvChartIntent.signal) ? tvChartIntent.signal : null);
    if (currentSymbolKey && intentSymbol !== currentSymbolKey) {
      setAiReview(null);
      setAiReviewError(null);
      lastAppliedRouteKeyRef.current = null;
    }
    setPair(intentSymbol);
    const preferredCode = tfCodeForBackend(tvChartIntent.preferredTf);
    if (preferredCode) {
      // autoReview: keep auto TF mode so server timeframe_route can apply after review.
      // Otherwise lock to the caller's preferred TF until the user resets manually.
      setTimeframeAutoMode(tvChartIntent.autoReview === true);
      setTimeframe(preferredCode);
      lastAppliedRouteKeyRef.current = null;
    }
    if (tvChartIntent.autoReview) {
      const sig = isEngineSignalLike(tvChartIntent.signal) ? tvChartIntent.signal : null;
      const isCrypto = String(sig?.type || '').toLowerCase() === 'crypto';
      setShowQuantDebug(true);
      setEma20(true);
      setEma21(true);
      setEma50(true);
      setEma200(true);
      setDema200(!isCrypto);
      setRsi14(true);
      setAtr14(true);
      setVwapEnabled(isCrypto);
      setAdx14(isCrypto);
      setVolumeBars(isCrypto);
      setVolumeMa(isCrypto);
    }
    setAiReview(null);
    setAiReviewError(null);
    pendingAutoReviewRef.current = tvChartIntent.autoReview === true;
    autoReviewRanForIntentRef.current = null;
    autoReviewEarliestRunAtRef.current = null;
    setIntentSourceBadge(
      tvChartIntent.source === 'signals' ? 'Opened from Signals' : `Opened from ${tvChartIntent.source}`,
    );
    setAutoReviewStatus(tvChartIntent.autoReview ? 'pending' : 'idle');
    clearTvChartIntent();
  }, [tvChartIntent, clearTvChartIntent, currentSymbolKey]);

  useEffect(() => {
    if (!pendingAutoReviewRef.current || loading || !candles?.length || aiReviewLoading || engineBOverlayPendingForReview) return;
    const generation = chartRenderGeneration;
    if (!generation || !chartPaintReadyForReview || !chartIndicatorsReadyForReview) return;
    const intentId = appliedIntentIdRef.current;
    if (!intentId || autoReviewRanForIntentRef.current === intentId) return;
    if (autoReviewEarliestRunAtRef.current == null) {
      autoReviewEarliestRunAtRef.current = Date.now() + AUTO_REVIEW_CHART_SETTLE_MS;
    }
    const remainingSettleMs = autoReviewEarliestRunAtRef.current - Date.now();
    if (remainingSettleMs > 0) {
      const timerId = window.setTimeout(
        () => setAutoReviewDelayTick((tick) => tick + 1),
        remainingSettleMs,
      );
      return () => window.clearTimeout(timerId);
    }
    autoReviewRanForIntentRef.current = intentId;
    pendingAutoReviewRef.current = false;
    autoReviewEarliestRunAtRef.current = null;
    setAutoReviewStatus('running');
    void runAIReview().finally(() => setAutoReviewStatus('done'));
  }, [
    loading,
    candles,
    aiReviewLoading,
    engineBOverlayPendingForReview,
    chartRenderGeneration,
    chartPaintReadyForReview,
    chartIndicatorsReadyForReview,
    chartPaintReadyTick,
    autoReviewDelayTick,
  ]);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        await apiClient.postJson('/api/suggested-trades/evaluate-now', {});
        const res = await apiClient.getJson('/api/suggested-trades') as { watches?: SuggestedTradeWatch[] };
        if (!cancelled && Array.isArray(res?.watches)) {
          setActiveWatches(res.watches.filter((w) => ['WATCHING', 'READY_FOR_REVIEW', 'LEVEL_REACHED'].includes(String(w.status))));
        }
      } catch {
        /* alert-only poll — ignore transient errors */
      }
    };
    void poll();
    const timer = window.setInterval(poll, 8000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const suggestedPlan = useMemo((): SuggestedTradePlan | null => {
    const top = aiReview?.suggestedTradePlan ?? aiReview?.suggested_trade_plan;
    if (top && typeof top === 'object') return top;
    const nested = aiReview?.ai_review as { suggestedTradePlan?: SuggestedTradePlan } | undefined;
    if (nested?.suggestedTradePlan) return nested.suggestedTradePlan;

    if (!aiReview) return null;
    const review = aiReview.ai_review;
    const dir = String(review?.direction || aiReview.concordance?.engine_a_direction || '').toUpperCase();
    if (dir !== 'LONG' && dir !== 'SHORT') return null;
    return {
      schemaVersion: 'suggested_trade_plan.v1',
      armable: true,
      source: 'ai_chart_review',
      symbol: String(aiReview.engine_a_context?.symbol || pair || '').toUpperCase(),
      direction: dir as 'LONG' | 'SHORT',
      action: 'WATCH_ONLY',
      triggerType: 'ACCEPTANCE_ABOVE',
      reason: review?.waitReason || review?.chartReadSummary || 'AI-reviewed setup',
    };
  }, [aiReview, pair]);

  const canFlagWatch = Boolean(
    suggestedPlan?.armable
    && ['WAIT_FOR_LEVEL', 'WAIT_FOR_ZONE', 'WATCH_ONLY'].includes(String(suggestedPlan?.action || '').toUpperCase()),
  );

  const symbolWatches = useMemo(
    () => activeWatches.filter((watch) => {
      const watchKey = symbolKey(watch.symbol);
      return !currentSymbolKey || !watchKey || watchKey === currentSymbolKey;
    }),
    [activeWatches, currentSymbolKey],
  );
  const isPaper = Boolean(health?.paper_mode);
  const executeBlockReason = useMemo(
    () => evaluateTvChartExecuteBlock({
      signal: chartCandidate,
      chartSymbolKey: currentSymbolKey,
      chartTimeframe: timeframe,
      aiReview,
      suggestedTradePlan: suggestedPlan,
      isTestMode,
      isPaper,
    }),
    [chartCandidate, currentSymbolKey, timeframe, aiReview, suggestedPlan, isTestMode, isPaper],
  );
  const executeWarning = useMemo(() => aiReviewWarningForExecute(aiReview), [aiReview]);
  const showFlagWatchAction = Boolean(aiReview || suggestedPlan || flagStatus);
  const flagWatchDisabledReason = !suggestedPlan
    ? 'AI review did not return a suggested trade plan'
    : !canFlagWatch
      ? 'Suggested trade plan is not watchable'
      : undefined;
  const showViewSuggestedTrades = Boolean(flagStatus) || symbolWatches.length > 0;
  const { runner: suggestedTradeRunner } = useSuggestedTradeRunnerStatus();

  useEffect(() => {
    if (!confirmExecuteOpen || !chartCandidate) {
      setRiskPreviewLine(null);
      return;
    }
    const effectiveStyle = String(chartCandidate.style || 'swing');
    let cancelled = false;
    void fetchRiskPreview({
      pair: chartCandidate.pair || chartCandidate.display || pair,
      display: chartCandidate.display || chartCandidate.pair || pair,
      symbol: chartCandidate.symbol || chartCandidate.pair || pair,
      type: chartCandidate.type,
      direction: chartCandidate.direction,
      entry: chartCandidate.entry ?? chartCandidate.price,
      price: chartCandidate.entry ?? chartCandidate.price,
      sl: chartCandidate.sl,
      volume_mode: volumeMode,
      sizing_override: volumeMode === 'calculated' ? sizingOverride : 1.0,
      style: effectiveStyle,
      confluenceScore: chartCandidate.confluenceScore,
      maxScore: chartCandidate.maxScore,
      combinedConviction: chartCandidate.combinedConviction,
    }).then((preview) => {
      if (!cancelled) {
        setRiskPreviewLine(formatRiskPreviewLine(preview));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [confirmExecuteOpen, chartCandidate, pair, volumeMode, sizingOverride]);

  async function onConfirmExecute() {
    if (!chartCandidate || executeBlockReason) return;
    setExecuting(true);
    try {
      const payload = buildQuickExecutePayload({
        signal: chartCandidate,
        pipMode: String(chartCandidate.style || 'swing'),
        volumeMode,
        sizingOverride,
        exitMode,
        reviewId: aiReview?.review_id ?? null,
      });
      const result = await apiClient.postJson('/api/quick-execute', payload) as {
        success?: boolean;
        ticket?: string;
        error?: string;
        approval?: { approved: boolean; reason: string };
      };
      if (result.approval && !result.approval.approved) {
        showToast(`Rejected: ${result.approval.reason}`, 'error');
      } else if (result.success || result.ticket) {
        showToast(
          `Executed ${chartCandidate.display || chartCandidate.pair || pair} — ticket ${result.ticket || '?'}`,
          'success',
        );
      } else {
        showToast(`Error: ${result.error || 'Unknown'}`, 'error');
      }
    } catch (err) {
      showToast(`Execution failed: ${err instanceof Error ? err.message : 'unknown'}`, 'error');
    } finally {
      setExecuting(false);
      setConfirmExecuteOpen(false);
    }
  }

  async function flagWatchSetup() {
    if (!canFlagWatch || !suggestedPlan || flagLoading) return;
    setFlagLoading(true);
    setFlagStatus(null);
    try {
      const res = await apiClient.postJson('/api/suggested-trades/flag', {
        symbol: (pair || '').toUpperCase(),
        display: chartCandidate?.display || chartCandidate?.pair || pair,
        signal: chartCandidate,
        suggestedTradePlan: suggestedPlan,
        aiReviewSummary: aiReview?.aiReviewSummary ?? aiReview?.ai_review_summary,
        source: 'ai_chart_review',
        createdAt: new Date().toISOString(),
      }) as { success?: boolean; error?: string };
      if (res?.success) {
        setFlagStatus('Watching setup flagged (alert-only)');
        showToast('Setup flagged', 'success');
      } else {
        setFlagStatus(res?.error || 'Flag failed');
      }
    } catch (err) {
      setFlagStatus(err instanceof Error ? err.message : 'Flag failed');
    } finally {
      setFlagLoading(false);
    }
  }

  useEffect(() => {
    if (!aiReview) return;
    const reviewSymbolKey = aiReviewSymbolKeyRef.current || symbolKey(aiReview.engine_a_context?.symbol);
    const reviewTimeframe = normalizeBackendTf(aiReview.engine_a_context?.timeframe);
    const currentTf = normalizeBackendTf(timeframe);
    const symbolChanged = currentSymbolKey && (!reviewSymbolKey || reviewSymbolKey !== currentSymbolKey);
    const timeframeChanged = currentTf && reviewTimeframe && reviewTimeframe !== currentTf;
    if (symbolChanged || timeframeChanged) {
      setAiReview(null);
      setAiReviewError(null);
      lastAppliedRouteKeyRef.current = null;
    }
  }, [aiReview, currentSymbolKey, timeframe]);

  useEffect(() => {
    if (!timeframeAutoMode || !timeframeRoute || timeframeRoute.enabled === false) return;
    const routeSymbolKey = symbolKey(aiReview?.engine_a_context?.symbol);
    if (!routeSymbolKey || !currentSymbolKey || routeSymbolKey !== currentSymbolKey) return;
    const recommendedCode = tfCodeForBackend(timeframeRoute.autoSelectTf);
    if (!recommendedCode) return;
    const routeKey = timeframeRouteKey(currentSymbolKey, timeframeRoute);
    if (routeKey === lastAppliedRouteKeyRef.current) return;
    lastAppliedRouteKeyRef.current = routeKey;
    if (timeframe !== recommendedCode) setTimeframe(recommendedCode);
  }, [aiReview?.engine_a_context?.symbol, currentSymbolKey, timeframe, timeframeAutoMode, timeframeRoute]);

  // --- Fetch candles whenever pair/timeframe changes ---------------
  useEffect(() => {
    if (!pair || !backendTf) {
      setCandles(null);
      setChartPayload(null);
      setChartTickPayload(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setChartError(null);
    const url = `/api/candles?symbol=${encodeURIComponent(pair)}&tf=${encodeURIComponent(backendTf)}&limit=${CHART_HISTORY_LIMIT}`;
    apiClient
      .getJson(url)
      .then((res) => {
        if (cancelled) return;
        const data = res as CandleApiResponse;
        if (data?.error) {
          setChartError(data.error);
          setCandles(null);
          setChartPayload(data);
          return;
        }
        const list = Array.isArray(data?.candles) ? data.candles : [];
        setCandles(list);
        setChartPayload(data);
        if (list.length === 0) setChartError(`No candle data for ${pair} ${backendTf}`);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setChartError(err instanceof Error ? err.message : 'Failed to load candles');
        setCandles(null);
        setChartPayload(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [pair, backendTf]);

  useEffect(() => {
    if (!pair || !backendTf || !isBybitCryptoExecution(chartPayload)) {
      setChartTickPayload(null);
      return;
    }

    let cancelled = false;
    const fetchChartTick = async () => {
      try {
        const data = await apiClient.getJson(`/api/chart-tick?symbol=${encodeURIComponent(pair)}&tf=${encodeURIComponent(backendTf)}`);
        if (!cancelled) setChartTickPayload(data as CandleApiResponse);
      } catch {
        if (!cancelled) setChartTickPayload(null);
      }
    };

    void fetchChartTick();
    const id = window.setInterval(fetchChartTick, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [pair, backendTf, chartPayload?.asset_group, chartPayload?.execution_provider]);

  useEffect(() => {
    if (!pair || !backendTf) {
      setEngineBOverlay(null);
      setEngineBOverlayError(null);
      return;
    }
    let cancelled = false;
    setEngineBOverlayLoading(true);
    setEngineBOverlayError(null);
    const url =
      `/api/engine-b-overlays?symbol=${encodeURIComponent(pair)}` +
      `&tf=${encodeURIComponent(backendTf)}` +
      `&direction=${encodeURIComponent(engineBDirection)}&style=auto`;
    apiClient
      .getJson(url)
      .then((res) => {
        if (cancelled) return;
        const data = res as EngineBOverlayPayload & { error?: string };
        if (data?.error) {
          setEngineBOverlay(null);
          setEngineBOverlayError(data.error);
          return;
        }
        setEngineBOverlay(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setEngineBOverlay(null);
        setEngineBOverlayError(err instanceof Error ? err.message : 'Failed to load Engine B overlays');
      })
      .finally(() => {
        if (!cancelled) setEngineBOverlayLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [pair, backendTf, engineBDirection]);

  // --- Chart lifecycle ---------------------------------------------
  // Pane structure depends on which sub-pane studies are on; recreate the chart
  // whenever rsi14 / atr14 flip so panes appear and disappear cleanly.
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const engineBMarkersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const engineBPriceLinesRef = useRef<IPriceLine[]>([]);
  const engineBZonePrimitiveRef = useRef<EngineBZonePrimitive | null>(null);
  const rightEdgeLabelPrimitiveRef = useRef<ChartRightEdgeLabelPrimitive | null>(null);
  const chartCaptureRef = useRef<HTMLDivElement | null>(null);
  const ema20SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ema21SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ema50SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ema200SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const dema200SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const rsiSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const adxSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const atrSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const volumeMaSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  const subPaneStudyCount = (quantRsi14 ? 1 : 0) + (quantAtr14 ? 1 : 0) + (isCryptoChart && quantAdx14 ? 1 : 0) + (isCryptoChart && quantVolumeBars ? 1 : 0);
  const chartHeightPx = PRICE_CHART_HEIGHT_PX + subPaneStudyCount * STUDY_PANE_HEIGHT_PX;
  const candlePriceFormat = useMemo(() => {
    const _pp = chartPayload?.price_precision ?? engineBOverlay?.price_precision;
    const precision = typeof _pp?.precision === 'number' ? _pp.precision : 5;
    const minMove = typeof _pp?.min_move === 'number' ? _pp.min_move : 10 ** -precision;
    return { type: 'price' as const, precision, minMove };
  }, [chartPayload?.price_precision, engineBOverlay?.price_precision]);

  function captureChartCanvas(): { canvas: HTMLCanvasElement | null; error: string | null } {
    const captureEl = chartCaptureRef.current;
    if (!captureEl) {
      const error = 'Chart screenshot is unavailable until the chart renders';
      setChartError(error);
      return { canvas: null, error };
    }
    const canvases = Array.from(captureEl.querySelectorAll('canvas'));
    if (canvases.length === 0) {
      const error = 'Chart screenshot is unavailable until the chart renders';
      setChartError(error);
      return { canvas: null, error };
    }

    const captureRect = captureEl.getBoundingClientRect();
    const scale = window.devicePixelRatio || 1;
    const outputCanvas = document.createElement('canvas');
    outputCanvas.width = Math.max(1, Math.round(captureRect.width * scale));
    outputCanvas.height = Math.max(1, Math.round(captureRect.height * scale));
    const outputCtx = outputCanvas.getContext('2d');
    if (!outputCtx) {
      const error = 'Chart screenshot failed: canvas context unavailable';
      setChartError(error);
      return { canvas: null, error };
    }

    outputCtx.scale(scale, scale);
    outputCtx.fillStyle = `rgb(${CHART_CAPTURE_BACKGROUND.r}, ${CHART_CAPTURE_BACKGROUND.g}, ${CHART_CAPTURE_BACKGROUND.b})`;
    outputCtx.fillRect(0, 0, captureRect.width, captureRect.height);
    for (const canvas of canvases) {
      const rect = canvas.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      outputCtx.drawImage(
        canvas,
        rect.left - captureRect.left,
        rect.top - captureRect.top,
        rect.width,
        rect.height,
      );
    }
    drawCaptureLabels(outputCtx, captureEl, captureRect);
    return { canvas: outputCanvas, error: null };
  }

  function downloadChartScreenshot() {
    const { canvas: outputCanvas } = captureChartCanvas();
    if (!outputCanvas) return;
    outputCanvas.toBlob((blob) => {
      if (!blob) {
        setChartError('Chart screenshot failed: PNG export unavailable');
        return;
      }
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      const backendTf = TF_BACKEND_MAP[timeframe] || timeframe;
      const screenshotSymbol = pair.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').toUpperCase() || 'chart';
      link.href = url;
      link.download = `${screenshotSymbol}-${backendTf}-chart.png`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }, 'image/png');
  }

  async function captureReviewCanvas(
    allowRetry: boolean,
  ): Promise<{ canvas: HTMLCanvasElement | null; error: string | null }> {
    await waitForChartPaint();
    let captured = captureChartCanvas();
    if (!captured.canvas) return captured;
    if (canvasHasNonBackgroundContent(captured.canvas)) {
      setChartError(null);
      return captured;
    }
    if (!allowRetry) {
      const error = 'Chart screenshot failed: chart has not painted yet';
      setChartError(error);
      return { canvas: null, error };
    }
    setChartError('Chart not painted yet — retrying…');
    await waitForChartPaint();
    captured = captureChartCanvas();
    if (!captured.canvas) return captured;
    if (!canvasHasNonBackgroundContent(captured.canvas)) {
      const error = 'Chart screenshot failed: chart has not painted yet';
      setChartError(error);
      return { canvas: null, error };
    }
    setChartError(null);
    return captured;
  }

  async function runAIReview() {
    if (aiReviewLoading) return;
    if (chartReviewPendingForReview) {
      setChartError('Chart is still preparing indicators for review');
      return;
    }
    setAiReviewError(null);
    setAiReview(null);
    const captured = await captureReviewCanvas(true);
    if (!captured.canvas) {
      setAiReviewError(captured.error || 'Chart screenshot capture failed');
      return;
    }
    const sourceCanvas = captured.canvas;
    const downscaled = downscaleToCap(
      sourceCanvas,
      AI_CHART_REVIEW_DEFAULTS.MAX_WIDTH,
      AI_CHART_REVIEW_DEFAULTS.MAX_HEIGHT,
    );
    setAiReviewLoading(true);
    try {
      const dataUrl = await canvasToDataUrl(downscaled);
      const tfForBackend = TF_BACKEND_MAP[timeframe] || timeframe;
      const visibleRange = chartRef.current?.timeScale().getVisibleRange() ?? null;
      const overlays: string[] = ['candles'];
      if (showEngineBOverlays && engineBOverlay?.overlay_source === 'engine_b') overlays.push('engine_b');
      if (quantVolumeBars) overlays.push('volume');
      if (quantVwap) overlays.push('vwap');
      if (quantEma20) overlays.push('ema20');
      if (quantEma21) overlays.push('ema21');
      if (quantEma50) overlays.push('ema50');
      if (quantEma200) overlays.push('ema200');
      if (quantDema200) overlays.push('dema200');
      if (quantAtr14) overlays.push('atr14');
      if (quantRsi14) overlays.push('rsi14');
      if (quantAdx14) overlays.push('adx14');
      const priceValues = (candles ?? [])
        .flatMap((row) => [toNum(row.l, NaN), toNum(row.h, NaN)])
        .filter((value) => Number.isFinite(value));
      const chartSnapshot: AIChartReviewChartSnapshot = {
        renderedLayers: [...overlays],
        visibleRange: visibleRange
          ? {
              from: visibleRange.from != null ? String(visibleRange.from) : null,
              to: visibleRange.to != null ? String(visibleRange.to) : null,
            }
          : null,
        visibleCandleCount: candles?.length ?? 0,
        indicatorLayerStates: {
          engineB: showEngineBOverlays && engineBOverlay?.overlay_source === 'engine_b',
          volume: Boolean(quantVolumeBars),
          volumeMa: Boolean(quantVolumeBars && quantVolumeMa),
          vwap: Boolean(quantVwap),
          ema20: Boolean(quantEma20),
          ema21: Boolean(quantEma21),
          ema50: Boolean(quantEma50),
          ema200: Boolean(quantEma200),
          dema200: Boolean(quantDema200),
          atr14: Boolean(quantAtr14),
          rsi14: Boolean(quantRsi14),
          adx14: Boolean(quantAdx14),
        },
        engineBOverlayCount:
          showEngineBOverlays && engineBOverlay?.overlay_source === 'engine_b'
            ? engineBOverlayLines(engineBOverlay, true).length + buildEngineBZones(engineBOverlay, true).length
            : 0,
        engineBContext: showEngineBOverlays && engineBOverlay?.overlay_source === 'engine_b'
            ? (engineBOverlay as Record<string, unknown>)
            : null,
        engineBOverlayStatus,
        engineBOverlayError: engineBOverlayError ?? null,
        priceRange: priceValues.length
          ? {
              min: Math.min(...priceValues),
              max: Math.max(...priceValues),
            }
          : null,
        provider: chartPayload?.chart_provider || chartPayload?.candle_provider || null,
        // Diagnostics-only: the indicator values actually drawn on the chart, so
        // the backend can flag chart-vs-Engine-A divergence. Not trusted for scoring.
        chartIndicators: {
          timeframe: tfForBackend,
          emaTrend: studySnapshot.latest.ema21 ?? null,
          ema50: studySnapshot.latest.ema50 ?? null,
          ema200: studySnapshot.latest.ema200 ?? null,
          rsi14: studySnapshot.latest.rsi14 ?? null,
          atr14: studySnapshot.latest.atr14 ?? null,
          adx14: studySnapshot.latest.adx14 ?? null,
        },
      };
      const candidateStyle = firstString(chartCandidate?.style)?.toLowerCase();
      const scoringProfile = asRecord(chartCandidate?.scoringProfile);
      const meta = buildScreenshotMeta({
        width: downscaled.width,
        height: downscaled.height,
        chart_timeframe: tfForBackend,
        analyze_style:
          candidateStyle && ['scalp', 'intraday', 'swing'].includes(candidateStyle)
            ? candidateStyle
            : undefined,
        scoring_timeframes: Array.isArray(chartCandidate?.scoringTimeframes)
          ? chartCandidate.scoringTimeframes
          : undefined,
        momentum_timeframe:
          firstString(
            chartCandidate?.momentumTimeframe,
            scoringProfile.momentumTf as string | undefined,
          ) ?? undefined,
        regime_timeframe:
          firstString(
            chartCandidate?.regimeTimeframe,
            scoringProfile.regimeTf as string | undefined,
          ) ?? undefined,
        execution_timeframe:
          firstString(
            chartCandidate?.executionTimeframe,
            scoringProfile.executionTf as string | undefined,
          ) ?? undefined,
        signal_style: candidateStyle,
        overlays,
        visible_range_start: visibleRange?.from != null ? String(visibleRange.from) : undefined,
        visible_range_end: visibleRange?.to != null ? String(visibleRange.to) : undefined,
        chart_provider: chartPayload?.chart_provider || chartPayload?.candle_provider || undefined,
        chart_snapshot: chartSnapshot,
      });
      const symbol = (pair || '').toUpperCase();
      if (!symbol) {
        throw new Error('No symbol selected');
      }
      const response = await postChartReview({
        symbol,
        timeframe: tfForBackend,
        provider: aiReviewProvider,
        screenshot_base64: dataUrl,
        screenshot_meta: meta,
      });
      const responseSymbolKey = symbolKey(response.engine_a_context?.symbol) || symbolKey(symbol);
      if (responseSymbolKey && currentPairKeyRef.current && responseSymbolKey !== currentPairKeyRef.current) return;
      aiReviewSymbolKeyRef.current = responseSymbolKey;
      lastAppliedRouteKeyRef.current = null;
      setAiReview(response);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'AI review failed';
      setAiReviewError(msg);
    } finally {
      setAiReviewLoading(false);
    }
  }

  function runManualAIReview() {
    pendingAutoReviewRef.current = false;
    autoReviewEarliestRunAtRef.current = null;
    if (autoReviewStatus === 'pending') setAutoReviewStatus('idle');
    void runAIReview();
  }

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight || chartHeightPx,
      layout: {
        background: { color: 'transparent' },
        textColor: 'rgba(245, 240, 232, 0.65)',
        fontFamily: "'IBM Plex Mono', monospace",
      },
      grid: {
        vertLines: { color: 'rgba(212, 160, 23, 0.06)' },
        horzLines: { color: 'rgba(212, 160, 23, 0.06)' },
      },
      rightPriceScale: { borderColor: 'rgba(212, 160, 23, 0.18)', minimumWidth: 80 },
      timeScale: {
        borderColor: 'rgba(212, 160, 23, 0.18)',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 3,
      },
      crosshair: { mode: 1 },
    });
    chartRef.current = chart;

    // Pane 0 — price + overlay EMAs/DEMA. Stretch=3 so it stays dominant when sub-panes exist.
    const pricePane = chart.panes()[0] as IPaneApi<Time>;
    pricePane.setStretchFactor(3);

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: 'hsl(160, 84%, 39%)',
      downColor: 'hsl(343, 96%, 60%)',
      borderUpColor: 'hsl(160, 84%, 39%)',
      borderDownColor: 'hsl(343, 96%, 60%)',
      wickUpColor: 'hsl(160, 84%, 39%)',
      wickDownColor: 'hsl(343, 96%, 60%)',
      lastValueVisible: true,
      priceLineVisible: true,
      priceFormat: candlePriceFormat,
    }, 0);
    candleSeriesRef.current = candleSeries;
    engineBMarkersRef.current = createSeriesMarkers(candleSeries, [], { zOrder: 'top' });
    // Engine B zone primitive: paints translucent support/resistance/FVG/OB bands
    // under the candles. Lives for the chart instance's lifetime; data pushed via
    // setZones() from the data-push effect below.
    const zonePrimitive = new EngineBZonePrimitive();
    candleSeries.attachPrimitive(zonePrimitive);
    engineBZonePrimitiveRef.current = zonePrimitive;
    const labelPrimitive = new ChartRightEdgeLabelPrimitive();
    candleSeries.attachPrimitive(labelPrimitive);
    rightEdgeLabelPrimitiveRef.current = labelPrimitive;

    const overlayLineOpts = { lineWidth: 2 as const, lastValueVisible: true, priceLineVisible: false };
    ema20SeriesRef.current = chart.addSeries(LineSeries, { ...overlayLineOpts, color: PRICE_PANEL_INDICATORS.ema20.color }, 0);
    ema21SeriesRef.current = chart.addSeries(LineSeries, { ...overlayLineOpts, color: PRICE_PANEL_INDICATORS.ema21.color }, 0);
    ema50SeriesRef.current = chart.addSeries(LineSeries, { ...overlayLineOpts, color: PRICE_PANEL_INDICATORS.ema50.color }, 0);
    ema200SeriesRef.current = chart.addSeries(LineSeries, { ...overlayLineOpts, color: PRICE_PANEL_INDICATORS.ema200.color }, 0);
    dema200SeriesRef.current = chart.addSeries(LineSeries, { ...overlayLineOpts, color: PRICE_PANEL_INDICATORS.dema200.color }, 0);
    vwapSeriesRef.current = chart.addSeries(LineSeries, { ...overlayLineOpts, color: PRICE_PANEL_INDICATORS.vwap.color, lineStyle: LineStyle.Dashed }, 0);

    // Sub-panes — created only if their study is on, so an unused pane never sits empty.
    if (quantRsi14) {
      const pane = chart.addPane();
      pane.setStretchFactor(1);
      const paneIdx = pane.paneIndex();
      const series = chart.addSeries(LineSeries, {
        color: STUDY_PANEL_INDICATORS.rsi14.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
      }, paneIdx);
      series.createPriceLine({ price: 70, color: 'rgba(245,240,232,0.25)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '70' });
      series.createPriceLine({ price: 30, color: 'rgba(245,240,232,0.25)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '30' });
      rsiSeriesRef.current = series;
    }
    if (quantAdx14) {
      const pane = chart.addPane();
      pane.setStretchFactor(1);
      const paneIdx = pane.paneIndex();
      const series = chart.addSeries(LineSeries, {
        color: STUDY_PANEL_INDICATORS.adx14.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
      }, paneIdx);
      series.createPriceLine({ price: 25, color: 'rgba(245,240,232,0.25)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '25' });
      adxSeriesRef.current = series;
    }
    if (quantAtr14) {
      const atrPane = chart.addPane();
      atrPane.setStretchFactor(1);
      const atrPaneIdx = atrPane.paneIndex();
      atrSeriesRef.current = chart.addSeries(LineSeries, {
        color: STUDY_PANEL_INDICATORS.atr14.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
      }, atrPaneIdx);
    }
    if (isCryptoChart && quantVolumeBars) {
      const pane = chart.addPane();
      pane.setStretchFactor(1);
      const paneIdx = pane.paneIndex();
      volumeSeriesRef.current = chart.addSeries(HistogramSeries, {
        color: STUDY_PANEL_INDICATORS.volume.color,
        priceFormat: { type: 'volume' },
        priceLineVisible: false,
        lastValueVisible: true,
      }, paneIdx);
      volumeMaSeriesRef.current = chart.addSeries(LineSeries, {
        color: STUDY_PANEL_INDICATORS.volumeMa.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
      }, paneIdx);
    }

    const ro = new ResizeObserver(() => {
      const w = container.clientWidth;
      const h = container.clientHeight;
      if (w > 0 && h > 0) chart.applyOptions({ width: w, height: h });
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      engineBMarkersRef.current = null;
      engineBPriceLinesRef.current = [];
      engineBZonePrimitiveRef.current = null;
      rightEdgeLabelPrimitiveRef.current = null;
      ema20SeriesRef.current = null;
      ema21SeriesRef.current = null;
      ema50SeriesRef.current = null;
      ema200SeriesRef.current = null;
      dema200SeriesRef.current = null;
      vwapSeriesRef.current = null;
      rsiSeriesRef.current = null;
      adxSeriesRef.current = null;
      atrSeriesRef.current = null;
      volumeSeriesRef.current = null;
      volumeMaSeriesRef.current = null;
    };
    // chartHeightPx only seeds the first sizing call; the ResizeObserver takes over after.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quantRsi14, quantAtr14, quantAdx14, quantVolumeBars, isCryptoChart]);

  // --- Push data into the chart ------------------------------------
  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleSeriesRef.current;
    if (!chart || !candleSeries || !backendTf) return;

    candleSeries.applyOptions({ priceFormat: candlePriceFormat });

    for (const line of engineBPriceLinesRef.current) {
      candleSeries.removePriceLine(line);
    }
    engineBPriceLinesRef.current = [];

    if (!candles || candles.length === 0) {
      candleSeries.setData([]);
      engineBMarkersRef.current?.setMarkers([]);
      ema20SeriesRef.current?.setData([]);
      ema21SeriesRef.current?.setData([]);
      ema50SeriesRef.current?.setData([]);
      ema200SeriesRef.current?.setData([]);
      dema200SeriesRef.current?.setData([]);
      vwapSeriesRef.current?.setData([]);
      rsiSeriesRef.current?.setData([]);
      adxSeriesRef.current?.setData([]);
      atrSeriesRef.current?.setData([]);
      volumeSeriesRef.current?.setData([]);
      volumeMaSeriesRef.current?.setData([]);
      chartPaintReadyGenerationRef.current = null;
      return;
    }

    const rows = studySnapshot.rows;
    const times = rows.map((row) => row.time);
    const volumes = studySnapshot.volumes;
    const values = studySnapshot.seriesValues;
    candleSeries.setData(rows);
    engineBMarkersRef.current?.setMarkers(engineBMarkers(engineBOverlay, rows, showEngineBOverlays));
    if (chartMode === 'clean') {
      // Do not replace filled support/resistance/FVG zones with line-only overlays;
      // this filled-zone style is the preferred readable Engine B visual.
      // Clean mode: render zones as translucent bands via the primitive and keep
      // only single-level structural lines (BOS / CHOCH / breaker) as thin colored
      // lines with no right-axis labels. Identity is conveyed by the legend + label primitive.
      engineBZonePrimitiveRef.current?.setZones(buildEngineBZones(engineBOverlay, showEngineBOverlays));
      for (const level of engineBSingleLevelLines(engineBOverlay, showEngineBOverlays)) {
        engineBPriceLinesRef.current.push(
          candleSeries.createPriceLine({
            price: level.price,
            color: level.color,
            lineWidth: 1,
            lineStyle: level.style ?? LineStyle.Dotted,
            axisLabelVisible: false,
            title: '',
          }),
        );
      }
    } else {
      // Debug mode: filled zones hidden; structural levels use the label primitive
      // instead of native right-axis titles to avoid label collisions.
      engineBZonePrimitiveRef.current?.setZones([]);
      for (const level of engineBOverlayLines(engineBOverlay, showEngineBOverlays)) {
        engineBPriceLinesRef.current.push(
          candleSeries.createPriceLine({
            price: level.price,
            color: level.color,
            lineWidth: 1,
            lineStyle: level.style ?? LineStyle.Dashed,
            axisLabelVisible: false,
            title: '',
          }),
        );
      }
    }

    const paneHeightPx = containerRef.current?.clientHeight ?? chartHeightPx;
    const currentPrice = firstNumber(liveTick?.price, studySnapshot.latest.close);
    const labelInputs = buildEngineBRightEdgeLabelInputs(engineBOverlay, showEngineBOverlays, chartMode === 'clean');
    rightEdgeLabelPrimitiveRef.current?.setLabels(
      resolveRightEdgeLabels(candleSeries, labelInputs, currentPrice, paneHeightPx),
      paneHeightPx,
    );

    const pushLine = (
      series: ISeriesApi<'Line'> | null,
      enabled: boolean,
      seriesValues: (number | null)[],
    ) => {
      if (!series) return;
      if (!enabled) {
        series.setData([]);
        return;
      }
      const data: LineData[] = [];
      for (let i = 0; i < seriesValues.length; i += 1) {
        const v = seriesValues[i];
        if (v != null && Number.isFinite(v)) data.push({ time: times[i], value: v });
      }
      series.setData(data);
    };

    pushLine(ema20SeriesRef.current, quantEma20, values.ema20 || []);
    pushLine(ema21SeriesRef.current, isCryptoChart && quantEma21, values.ema21 || []);
    pushLine(ema50SeriesRef.current, quantEma50, values.ema50 || []);
    pushLine(ema200SeriesRef.current, quantEma200, values.ema200 || []);
    pushLine(dema200SeriesRef.current, quantDema200, values.dema200 || []);
    pushLine(vwapSeriesRef.current, showVwapOnChart && quantVwap, values.vwap || []);
    pushLine(rsiSeriesRef.current, quantRsi14, values.rsi14 || []);
    pushLine(adxSeriesRef.current, quantAdx14, values.adx14 || []);
    pushLine(atrSeriesRef.current, quantAtr14, values.atr14 || []);
    if (volumeSeriesRef.current) {
      if (isCryptoChart && quantVolumeBars) {
        const volumeData: HistogramData[] = rows.map((row, idx) => ({
          time: row.time,
          value: volumes[idx] ?? 0,
          color: row.close >= row.open ? 'rgba(16, 185, 129, 0.45)' : 'rgba(244, 63, 94, 0.45)',
        }));
        volumeSeriesRef.current.setData(volumeData);
      } else {
        volumeSeriesRef.current.setData([]);
      }
    }
    pushLine(volumeMaSeriesRef.current, isCryptoChart && quantVolumeBars && quantVolumeMa, values.volumeMa || []);

    if (rows.length > VISIBLE_BAR_COUNT) {
      chart.timeScale().setVisibleLogicalRange({
        from: rows.length - VISIBLE_BAR_COUNT,
        to: rows.length - 1,
      });
    } else {
      chart.timeScale().fitContent();
    }

    const generation = chartRenderGenerationKey(pair, backendTf, rows.length);
    chartRenderGenerationRef.current = generation;
    chartPaintReadyGenerationRef.current = null;
    let cancelled = false;
    void waitForChartPaint().then(() => {
      if (cancelled || chartRenderGenerationRef.current !== generation) return;
      chartPaintReadyGenerationRef.current = generation;
      setChartPaintReadyTick((tick) => tick + 1);
    });
    return () => {
      cancelled = true;
    };
  }, [pair, candles, liveTick, backendTf, isCryptoChart, showVwapOnChart, studySnapshot, chartPayload, candlePriceFormat, engineBOverlay, showEngineBOverlays, chartMode, quantEma20, quantEma21, quantEma50, quantEma200, quantDema200, quantVwap, quantRsi14, quantAdx14, quantAtr14, quantVolumeBars, quantVolumeMa]);

  return (
    <>
    <Card className="h-full">
      <CardHeader className="pb-2">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <BarChart3 className="h-5 w-5" />
            TV Chart
          </CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={pair}
              onChange={(event) => setPair(event.target.value)}
              className="h-8 w-32 text-xs"
              aria-label="Chart symbol"
            />
            <ProviderBadge payload={chartPayload} />
            {isCryptoChart && lastCandleConfirmed != null && (
              <Badge variant="outline" className="h-7 text-[10px]">
                {lastCandleConfirmed ? 'Confirmed candle' : 'Forming candle'}
              </Badge>
            )}
            <select
              value={displaySymbol(chartCandidate) || ''}
              onChange={(event) => {
                if (event.target.value) setPair(event.target.value);
              }}
              className="h-8 w-40 rounded-md border border-input bg-background px-2 text-xs"
              aria-label="Engine A candidate"
            >
              <option value="">No candidate selected</option>
              {candidateRows.map((candidate, index) => {
                const candidateSymbol = displaySymbol(candidate) || `Candidate ${index + 1}`;
                const direction = normalizeDirection(candidate.direction);
                return (
                  <option key={`${candidateSymbol}-${index}`} value={candidateSymbol}>
                    {candidateSymbol}{direction ? ` ${direction}` : ''}
                  </option>
                );
              })}
            </select>
            <div className="flex flex-wrap gap-1">
              {TIMEFRAMES.map((tf) => (
                <Button
                  key={tf}
                  size="sm"
                  variant={timeframe === tf ? 'default' : 'outline'}
                  className="h-8 px-2 text-xs"
                  onClick={() => handleManualTimeframeSelect(tf)}
                >
                  {tf}
                </Button>
              ))}
            </div>
            <IndicatorSwitch label="Engine B overlays" checked={showEngineBOverlays} onCheckedChange={setShowEngineBOverlays} />
            <div className="flex items-center gap-1 rounded-md border border-input bg-background p-0.5">
              {(['clean', 'debug'] as const).map((mode) => (
                <Button
                  key={mode}
                  size="sm"
                  variant={chartMode === mode ? 'default' : 'ghost'}
                  className="h-7 px-2 text-[11px]"
                  onClick={() => setChartMode(mode)}
                  aria-pressed={chartMode === mode}
                  aria-label={`${mode === 'clean' ? 'Full Stack Clean' : 'Debug Full Stack'} chart mode`}
                >
                  {mode === 'clean' ? 'Clean' : 'Debug'}
                </Button>
              ))}
            </div>
            <IndicatorSwitch label="Show Quant Debug" checked={showQuantDebug} onCheckedChange={setShowQuantDebug} />
            {showQuantDebug && (
              <Select value={activePreset} onValueChange={(v) => applyPreset(v as PresetValue)}>
                <SelectTrigger className="h-8 w-40 text-xs" aria-label="Indicator preset">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PRESET_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value} className="text-xs">
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <Button size="sm" variant="secondary" className="h-8 gap-2 text-xs" onClick={applyEngineAReviewLayout}>
              <SlidersHorizontal className="h-3.5 w-3.5" />
              Engine A Review Layout
            </Button>
            <AIReviewProviderToggle value={aiReviewProvider} onChange={setAiReviewProvider} />
            <Button
              size="sm"
              variant={engineAParityVisible ? 'default' : 'outline'}
              className="h-8 text-xs"
              onClick={() => setEngineAParityVisible((visible) => !visible)}
              aria-pressed={engineAParityVisible}
            >
              Engine A Parity
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-8 gap-2 text-xs"
              onClick={downloadChartScreenshot}
              disabled={loading || !candles?.length}
              aria-label="Download full chart screenshot"
            >
              <Camera className="h-3.5 w-3.5" />
              Screenshot
            </Button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 pt-2">
          {timeframeRouteLabel && (
            <Badge variant="outline" className="h-7 text-[10px]" title={timeframeRoute?.reason || undefined}>
              {timeframeRouteLabel}
            </Badge>
          )}
          {timeframeRoute?.autoSelectTf && (
            <Badge variant="outline" className="h-7 text-[10px]" title={timeframeRoute?.reason || undefined}>
              {timeframeAutoMode ? `Auto TF: ${timeframeRoute.autoSelectTf}` : 'Manual TF override'}
            </Badge>
          )}
          {timeframeRoute && !timeframeAutoMode && (
            <Button size="sm" variant="secondary" className="h-7 px-2 text-[10px]" onClick={applyRecommendedTimeframe}>
              Reset to recommended
            </Button>
          )}
          {intentSourceBadge && (
            <Badge variant="outline" className="h-7 text-[10px] border-primary/40 text-primary">
              {intentSourceBadge}
            </Badge>
          )}
          {autoReviewStatus !== 'idle' && (
            <Badge variant="outline" className="h-7 text-[10px]">
              Auto Review: {autoReviewStatus}
            </Badge>
          )}
          {suggestedTradeRunner && (
            <Badge variant="outline" className={`h-7 text-[10px] ${runnerBadgeClass(suggestedTradeRunner)}`}>
              Runner: {runnerBadgeLabel(suggestedTradeRunner)}
            </Badge>
          )}
        </div>
        {showQuantDebug && (
          <div className="flex flex-wrap items-center gap-3 border-t border-border/40 pt-2">
            {isCryptoChart ? (
              <IndicatorSwitch label={emaLabels.trend} checked={ema21} onCheckedChange={setEma21} />
            ) : (
              <IndicatorSwitch label={emaLabels.trend} checked={ema20} onCheckedChange={setEma20} />
            )}
            <IndicatorSwitch label={emaLabels.momentum} checked={ema50} onCheckedChange={setEma50} />
            <IndicatorSwitch label={emaLabels.long} checked={ema200} onCheckedChange={setEma200} />
            {!isCryptoChart && <IndicatorSwitch label={emaLabels.demaLong} checked={dema200} onCheckedChange={setDema200} />}
            {showVwapOnChart && <IndicatorSwitch label="VWAP" checked={vwapEnabled} onCheckedChange={setVwapEnabled} />}
            <IndicatorSwitch label={studyIndicatorDefs.atr14.label} checked={atr14} onCheckedChange={setAtr14} />
            <IndicatorSwitch label={studyIndicatorDefs.rsi14.label.split(' ')[0]} checked={rsi14} onCheckedChange={setRsi14} />
            {isCryptoChart && <IndicatorSwitch label={studyIndicatorDefs.adx14.label} checked={adx14} onCheckedChange={setAdx14} />}
            {isCryptoChart && <IndicatorSwitch label="Volume" checked={volumeBars} onCheckedChange={setVolumeBars} />}
            {isCryptoChart && <IndicatorSwitch label="Volume MA" checked={volumeMa} onCheckedChange={setVolumeMa} />}
          </div>
        )}
      </CardHeader>
      <CardContent className="pb-4">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_min(340px,36%)]">
          <div
            ref={chartCaptureRef}
            className="flex flex-col overflow-hidden rounded-md border bg-background"
          >
            <ChartMetadataStrip>
              <ChartFeedHeaderChips
                identityChips={chartFeedIdentityChips}
                feedChips={chartFeedDiagnosticsChips}
              />
              {(cleanLegendChips.length > 0
                || (showEngineBOverlays && engineBOverlay?.overlay_source === 'engine_b')
                || (showEngineBOverlays && (engineBOverlayLoading || engineBOverlayError || engineBOverlayStale))) && (
                <div className="flex flex-wrap items-center gap-1 border-t border-border/30 pt-2">
                  {cleanLegendChips.map((chip) => (
                    <LegendChip key={`legend-${chip.key}`} spec={chip} />
                  ))}
                  {showEngineBOverlays && engineBOverlay?.overlay_source === 'engine_b' && (
                    <CaptureLabel>{`Engine B ${engineBOverlay.overlay_version || 'overlay'}`}</CaptureLabel>
                  )}
                  {showEngineBOverlays && engineBOverlayLoading && (
                    <CaptureLabel>Engine B loading</CaptureLabel>
                  )}
                  {showEngineBOverlays && engineBOverlayError && (
                    <CaptureLabel>{`Engine B warning ${engineBOverlayError}`}</CaptureLabel>
                  )}
                  {showEngineBOverlays && engineBOverlayStale && (
                    <CaptureLabel>{`Engine B stale (${Math.round(engineBOverlayAgeSec ?? 0)}s)`}</CaptureLabel>
                  )}
                </div>
              )}
              {showQuantDebug && (pricePanelLegendItems.length > 0 || studyPanelLegendItems.length > 0) && (
                <div className="flex max-h-14 flex-wrap items-center gap-1 overflow-y-auto border-t border-border/30 pt-2">
                  {pricePanelLegendItems.map((item) => (
                    <IndicatorLegendItem key={`meta-${item.definition.key}`} item={item} />
                  ))}
                  {studyPanelLegendItems.map((item) => (
                    <IndicatorLegendItem key={`meta-study-${item.definition.key}`} item={item} />
                  ))}
                </div>
              )}
              {engineAParityVisible && engineAParityRows.length > 0 && (
                <div className="flex max-h-16 flex-wrap items-center gap-1 overflow-y-auto border-t border-border/30 pt-2">
                  <CaptureLabel>Engine A Parity</CaptureLabel>
                  {engineAParityRows.map((row) => (
                    <CaptureLabel key={row.label}>{`${row.label} ${row.value}`}</CaptureLabel>
                  ))}
                </div>
              )}
            </ChartMetadataStrip>
            <div
              className="relative min-h-0 w-full"
              style={{ height: `${chartHeightPx}px` }}
            >
              <div ref={containerRef} className="absolute inset-0" />
              {loading && (
                <div className="absolute inset-0 flex items-center justify-center bg-card/40 text-[11px] text-muted-foreground backdrop-blur-sm">
                  Loading candles…
                </div>
              )}
              {chartError && !loading && (
                <div className="absolute inset-x-0 top-0 z-10 bg-destructive/10 px-3 py-1 text-[11px] text-destructive">
                  {chartError}
                </div>
              )}
            </div>
          </div>
          <div
            className="min-w-0 space-y-3 overflow-y-auto lg:sticky lg:top-0"
            style={{ maxHeight: `${chartHeightPx}px` }}
            data-review-rail
          >
            <div className="flex flex-wrap items-center gap-2">
              <CompactSuggestedWatchStatus watches={symbolWatches} />
              {suggestedTradeRunner && (
                <Badge variant="outline" className={`h-7 text-[10px] ${runnerBadgeClass(suggestedTradeRunner)}`}>
                  Runner: {runnerBadgeLabel(suggestedTradeRunner)}
                </Badge>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2" data-review-action-strip>
              <Button
                size="sm"
                variant="outline"
                className="h-8 gap-2 text-xs"
                onClick={runManualAIReview}
                disabled={aiReviewLoading || chartReviewPendingForReview}
                aria-label="Run AI chart review"
                aria-busy={aiReviewLoading || chartReviewPendingForReview}
              >
                <Sparkles className="h-3.5 w-3.5" />
                {chartReviewPendingForReview ? 'Preparing...' : aiReviewLoading ? 'Reviewing...' : 'AI Review'}
              </Button>
              {chartCandidate && (
                <Button
                  size="sm"
                  variant="default"
                  className="h-8 gap-1 text-xs"
                  disabled={Boolean(executeBlockReason) || executing}
                  onClick={() => setConfirmExecuteOpen(true)}
                >
                  <Play className="h-3.5 w-3.5" />
                  Execute Now
                </Button>
              )}
              {showFlagWatchAction && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 text-xs"
                  disabled={!canFlagWatch || flagLoading}
                  onClick={() => void flagWatchSetup()}
                  title={flagWatchDisabledReason}
                  data-suggested-watch-action
                >
                  Flag / Watch Setup
                </Button>
              )}
              {showViewSuggestedTrades && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-8 text-xs"
                  onClick={() => setActivePanel('suggestedTrades')}
                >
                  View Suggested Trades
                </Button>
              )}
            </div>
            {chartReviewBlockReason && (
              <span className="block text-[10px] text-muted-foreground">{chartReviewBlockReason}</span>
            )}
            {executeBlockReason && (
              <span className="block text-[10px] text-muted-foreground">{executeBlockReason}</span>
            )}
            {!executeBlockReason && executeWarning && (
              <span className="block text-[10px] text-yellow-500">{executeWarning}</span>
            )}
            {flagStatus && <span className="block text-[10px] text-muted-foreground">{flagStatus}</span>}
            {aiReviewError && (
              <div className="text-[11px] text-warning border border-border/40 rounded-md p-2 break-words">
                AI review error: {aiReviewError}
              </div>
            )}
            {aiReview && <AIReviewCard response={aiReview} />}
            <details className="rounded-md border border-border/60 bg-card/70 p-2">
              <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
                Engine A diagnostics
              </summary>
              <div className="mt-2">
                <EngineASidePanel signal={chartCandidate} liveTick={liveTick} chartPayload={chartPayload} />
              </div>
            </details>
          </div>
        </div>
      </CardContent>
    </Card>
    <AlertDialog open={confirmExecuteOpen} onOpenChange={setConfirmExecuteOpen}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Confirm Execute Now</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2 text-sm">
              <p>
                Execute <b>{chartCandidate?.direction}</b>{' '}
                {chartCandidate?.display || chartCandidate?.pair || pair} at{' '}
                {fmtNum(chartCandidate?.entry ?? chartCandidate?.price, 5)}?
              </p>
              <VolumeModeField
                volumeMode={volumeMode}
                onVolumeModeChange={setVolumeMode}
                sizingOverride={sizingOverride}
                onSizingOverrideChange={setSizingOverride}
              />
              <ExitModeField
                compact
                exitMode={exitMode}
                onExitModeChange={setExitMode}
              />
              {riskPreviewLine && (
                <p className="font-mono text-[10px] text-muted-foreground">{riskPreviewLine}</p>
              )}
              <p className="text-xs text-muted-foreground">
                Backend will recompute SL/TP via /api/quick-execute. Manual user action only.
              </p>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={executing}>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={() => void onConfirmExecute()} disabled={executing || Boolean(executeBlockReason)}>
            {executing ? 'Executing…' : 'Confirm Execute'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    </>
  );
}
