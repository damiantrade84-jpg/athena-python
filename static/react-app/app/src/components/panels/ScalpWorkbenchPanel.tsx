import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  createChart,
  CandlestickSeries,
  LineStyle,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import { AlertTriangle, BarChart3, Camera, CheckCircle2, Copy, Play, RefreshCw, ShieldCheck, Sparkles } from 'lucide-react';
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
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { useApiPost } from '@/hooks/useApiData';
import { useStore } from '@/hooks/useStore';
import apiClient from '@/lib/apiClient';
import {
  buildScalpScreenshotMeta,
  canvasToDataUrl,
  downscaleToCap,
  postScalpChartReview,
} from '@/lib/aiScalpChartReview';
import {
  buildScalpExecutePayload,
  evaluateScalpExecuteBlock,
} from '@/lib/manualExecuteHelpers';
import ScalpAIReviewCard from '@/components/athena/ScalpAIReviewCard';
import type { ScalpAIChartReviewResponse, SuggestedTradePlan, SuggestedTradeWatch } from '@/types/athena';
import { cn, fmtNum, toNum } from '@/lib/utils';

const TIMEFRAMES = ['M1', 'M5', 'M15'] as const;
const CANDLE_LIMIT = 500;
const EMPTY_SYMBOL_SELECT_VALUE = '__no_scalp_candidate__';

interface CandleApiRow {
  t?: string | number;
  time?: string | number;
  timestamp?: string | number;
  o?: number | string;
  open?: number | string;
  h?: number | string;
  high?: number | string;
  l?: number | string;
  low?: number | string;
  c?: number | string;
  close?: number | string;
}

interface CandleApiResponse {
  candles?: CandleApiRow[];
  error?: string;
  symbol?: string;
  display?: string;
  tf?: string;
  candle_provider?: string;
  chart_provider?: string;
  execution_provider?: string;
  provider_mismatch?: boolean;
  provider_mismatch_reason?: string | null;
  fallback_reason?: string | null;
  chart_status?: string;
}

interface DataFidelity {
  vp_source?: string;
  vp_fidelity?: string;
  vp_is_proxy?: boolean;
  cvd_source?: string;
  cvd_fidelity?: string;
  cvd_is_proxy?: boolean;
  absorption_source?: string;
  absorption_fidelity?: string;
  absorption_is_proxy?: boolean;
  aggression_uses_real_order_flow?: boolean;
  notes?: string[];
}

interface ScalpWorkbenchSignal {
  symbol?: string | null;
  pair?: string;
  display?: string;
  direction?: string;
  type?: string;
  entry?: number;
  price?: number;
  sl?: number;
  tp1?: number;
  tp2?: number;
  rr?: number;
  rr1?: number;
  ai_grade?: string;
  ai_score?: number;
  market_state?: string;
  zone_type?: string;
  zone_desc?: string;
  trigger_desc?: string;
  vp_poc?: number;
  vp_vah?: number;
  vp_val?: number;
  vp_lvn_count?: number;
  vp_volume_source?: string;
  vp_fidelity?: string;
  vp_is_proxy?: boolean;
  vp_uses_real_trade_buckets?: boolean;
  cvd_source?: string;
  cvd_fidelity?: string;
  cvd_is_proxy?: boolean;
  cvd_uses_real_trade_buckets?: boolean;
  cvd_direction?: string;
  absorption_source?: string;
  absorption_fidelity?: string;
  absorption_is_proxy?: boolean;
  aggression_source?: string;
  aggression_source_is_proxy?: boolean;
  aggression_confirmed?: boolean;
  aggression_uses_real_order_flow?: boolean;
  data_fidelity?: DataFidelity;
  strict_fabio_pass?: boolean;
  strict_fabio_reason?: string;
  strict_fabio_missing_pillars?: string[];
  strict_fabio_pillars?: Record<string, boolean>;
  current_vs_strict_status?: string;
  profile_anchor_mode?: string;
  profile_anchor_bars?: number;
  profile_anchor_start?: string | null;
  profile_anchor_end?: string | null;
  execution_tf?: string;
  context_tf?: string;
  structure_tf?: string;
  gate_result?: string;
  executable?: boolean;
  candidate_status?: string;
  fail_reasons?: string[];
  soft_warnings?: string[];
  fee_guard?: Record<string, unknown>;
  advisory_summary?: string;
  engine?: string;
  timestamp?: string;
  rr_ok?: boolean;
  [k: string]: unknown;
}

interface ScalpScanResponse {
  signals?: ScalpWorkbenchSignal[];
  skipped?: Array<ScalpWorkbenchSignal & {
    pair?: string;
    display?: string;
    symbol?: string | null;
    reason?: string;
    gate_result?: string;
    fail_reasons?: string[];
  }>;
  scanned?: number;
  pair_count?: number;
  candidate_count?: number;
  pass_count?: number;
  skip_count?: number;
  session?: string;
  diagnostic?: boolean;
  reason?: string;
  error?: string;
}

interface ScalpExecuteResponse {
  success?: boolean;
  ticket?: string;
  error?: string;
  fresh_scan?: ScalpScanResponse;
}

type BadgeTone = 'real' | 'proxy' | 'danger' | 'ok' | 'muted' | 'warn';

interface NormalizedSourceContract {
  symbol: string | null;
  timeframe: string | null;
  venue: string | null;
  expectedVenue: string | null;
  executionVenue: string | null;
  dataVenue: string | null;
  venueMismatch: boolean | null;
  venueMismatchReason: string | null;
  candleSource: string | null;
  candleSourceIsReal: boolean | null;
  volumeSource: string | null;
  volumeSourceIsReal: boolean | null;
  orderflowSource: string | null;
  orderflowSourceIsReal: boolean | null;
  cvdSource: string | null;
  cvdSourceIsReal: boolean | null;
  absorptionSource: string | null;
  absorptionSourceIsReal: boolean | null;
  vpSource: string | null;
  vpSourceIsReal: boolean | null;
  strictOrderflowSourcePass: boolean | null;
  strictVolumeSourcePass: boolean | null;
  strictTimestampAlignmentPass: boolean | null;
  strictVenuePass: boolean | null;
  latestCandleTs: string | null;
  orderflowTs: string | null;
  cvdTs: string | null;
  vpTs: string | null;
  maxTimestampSkewSeconds: number | null;
  timestampAlignmentReason: string | null;
  unavailableReasons: string[];
  warnings: string[];
}

interface NormalizedMarketLocation {
  locationLabel: string | null;
  locationScore: number | null;
  nearPOC: boolean | null;
  nearVAH: boolean | null;
  nearVAL: boolean | null;
  nearLVN: boolean | null;
  nearHVN: boolean | null;
  aboveValueArea: boolean | null;
  belowValueArea: boolean | null;
  insideValueArea: boolean | null;
  poc: number | null;
  vah: number | null;
  val: number | null;
  lvnLevels: number[];
  hvnLevels: number[];
  nearestLVN: number | null;
  nearestHVN: number | null;
  distanceToPOC: number | null;
  distanceToVAH: number | null;
  distanceToVAL: number | null;
  distanceToNearestLVN: number | null;
  distanceToNearestHVN: number | null;
}

interface NormalizedAggressionContext {
  aggressionLabel: string | null;
  aggressionScore: number | null;
  buyAggression: number | null;
  sellAggression: number | null;
  delta: number | null;
  cvd: number | null;
  cvdSlope: number | null;
  absorptionDetected: boolean | null;
  absorptionSide: string | null;
  sweepDetected: boolean | null;
  sweepSide: string | null;
  imbalanceDetected: boolean | null;
  imbalanceSide: string | null;
}

interface NormalizedScalpSetup {
  setupType: string | null;
  direction: string | null;
  entry: number | null;
  stopLoss: number | null;
  tp1: number | null;
  tp2: number | null;
  rr1: number | null;
  rr2: number | null;
  riskDistance: number | null;
  rewardDistance1: number | null;
  rewardDistance2: number | null;
  invalidationReason: string | null;
  strictFabioPass: boolean | null;
  strictGateReasons: string[];
  skipped: boolean;
  skippedReason: string | null;
}

interface NormalizedScalpRow {
  raw: ScalpWorkbenchSignal | null;
  symbol: string | null;
  timeframe: string | null;
  engineStatus: string | null;
  executable: boolean | null;
  marketState: string | null;
  sourceContract: NormalizedSourceContract;
  marketLocation: NormalizedMarketLocation;
  aggressionContext: NormalizedAggressionContext;
  scalpSetup: NormalizedScalpSetup;
}

interface ScalpChartSnapshot {
  symbol: string | null;
  timeframe: string | null;
  chartCapturedAt: string;
  visibleRange: {
    from: string | null;
    to: string | null;
  };
  selectedSignal: {
    direction: string | null;
    setupType: string | null;
    entry: number | null;
    stopLoss: number | null;
    tp1: number | null;
    tp2: number | null;
    rr1: number | null;
    rr2: number | null;
  };
  marketLocation: {
    locationLabel: string | null;
    locationScore: number | null;
    poc: number | null;
    vah: number | null;
    val: number | null;
    nearestLVN: number | null;
    nearestHVN: number | null;
    lvnLevels: number[];
    hvnLevels: number[];
  };
  aggressionContext: {
    aggressionLabel: string | null;
    aggressionScore: number | null;
    delta: number | null;
    cvd: number | null;
    cvdSlope: number | null;
    absorptionDetected: boolean | null;
    absorptionSide: string | null;
    sweepDetected: boolean | null;
    sweepSide: string | null;
    imbalanceDetected: boolean | null;
    imbalanceSide: string | null;
  };
  sourceContract: {
    candleSource: string | null;
    candleSourceIsReal: boolean | null;
    volumeSource: string | null;
    volumeSourceIsReal: boolean | null;
    orderflowSource: string | null;
    orderflowSourceIsReal: boolean | null;
    cvdSource: string | null;
    cvdSourceIsReal: boolean | null;
    vpSource: string | null;
    vpSourceIsReal: boolean | null;
    strictOrderflowSourcePass: boolean | null;
    strictVolumeSourcePass: boolean | null;
    strictTimestampAlignmentPass: boolean | null;
    strictVenuePass: boolean | null;
    unavailableReasons: string[];
  };
}

interface ScalpAIReview {
  aiReviewSummary: {
    provider: string | null;
    model: string | null;
    providerStatus: string | null;
    fallbackUsed: boolean;
    humanAction: 'trade' | 'wait' | 'reject' | 'watch' | null;
    setupType: string | null;
    overallScore: number | null;
    tradeabilityScore: number | null;
    visualConfirmationScore: number | null;
    entryQualityScore: number | null;
    riskScore: number | null;
    sourceQualityScore: number | null;
    confidence: number | null;
    finalReason: string | null;
  };
  scalpVerdictComparison: {
    setupProvided: boolean;
    setupDirection: string | null;
    chartConfirmsDirection: boolean | null;
    chartContradictsDirection: boolean | null;
    chartConfirmsEntryTiming: boolean | null;
    chartContradictsEntryTiming: boolean | null;
    sourceQualitySupportsReview: boolean | null;
    aiDowngradedSetup: boolean;
    comparisonVerdict:
      | 'setup_confirmed'
      | 'direction_confirmed_entry_rejected'
      | 'setup_contradicted'
      | 'source_quality_insufficient'
      | 'setup_missing'
      | 'mixed'
      | 'unknown';
    downgradeReasons: string[];
    finalDecision: string | null;
    finalReason: string | null;
  };
  contextCompleteness: {
    score: number;
    status: 'complete' | 'partial' | 'insufficient';
    missingRequired: string[];
    missingOptional: string[];
    notApplicable: string[];
    metadata: {
      chartCapturedAt: string | null;
      latestCandleTs: string | null;
      candleSource: string | null;
      orderflowSource: string | null;
      vpSource: string | null;
    };
  };
}

interface ScalpAIReviewPreview {
  scalpChartSnapshot: ScalpChartSnapshot;
  screenshotCaptured: boolean;
  screenshotBytes: number | null;
  screenshotSize: { width: number; height: number } | null;
  screenshotBase64: string | null;
  sourceQualitySummary: string;
  expectedResultShape: ScalpAIReview | null;
  stagePlan: {
    visionInput: string[];
    compilerInput: string[];
    outputContract: string;
  };
}

interface PriceLevel {
  label: string;
  price: number;
  color: string;
  style?: LineStyle;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function numberOrNull(value: unknown): number | null {
  const n = toNum(value, NaN);
  return Number.isFinite(n) ? n : null;
}

function toTimestamp(raw: unknown): UTCTimestamp | null {
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    return (raw > 1e12 ? Math.floor(raw / 1000) : Math.floor(raw)) as UTCTimestamp;
  }
  if (typeof raw === 'string' && raw.trim().length > 0) {
    const ms = Date.parse(raw);
    if (Number.isFinite(ms)) return Math.floor(ms / 1000) as UTCTimestamp;
  }
  return null;
}

function toText(value: unknown, fallback = 'unknown'): string {
  if (value == null) return fallback;
  if (typeof value === 'string') return value.trim() || fallback;
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (Array.isArray(value)) {
    const text = value.map((item) => toText(item, '')).filter(Boolean).join(', ');
    return text || fallback;
  }
  return fallback;
}

function fieldText(
  signal: ScalpWorkbenchSignal | null,
  keys: string[],
  fallback = 'pending backend source-contract patch',
): string {
  const record = asRecord(signal);
  for (const key of keys) {
    const value = record[key];
    if (value != null && toText(value, '') !== '') return toText(value);
  }
  return fallback;
}

function boolField(signal: ScalpWorkbenchSignal | null, keys: string[]): boolean | null {
  const record = asRecord(signal);
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'boolean') return value;
    if (typeof value === 'string') {
      const normalized = value.trim().toLowerCase();
      if (normalized === 'true') return true;
      if (normalized === 'false') return false;
    }
  }
  return null;
}

function signalSymbol(signal: ScalpWorkbenchSignal | null | undefined): string {
  return String(signal?.display || signal?.pair || signal?.symbol || '').trim();
}

function signalKey(signal: ScalpWorkbenchSignal | null | undefined): string {
  return signalSymbol(signal).toUpperCase();
}

function preferredScalpDisplayTf(signal: ScalpWorkbenchSignal | null | undefined): (typeof TIMEFRAMES)[number] {
  const ctx = String(signal?.context_tf || '').toUpperCase();
  if (ctx === 'M5' || ctx === 'M15') {
    return ctx as (typeof TIMEFRAMES)[number];
  }
  return 'M5';
}

function toCandleData(rows: CandleApiRow[] | undefined): CandlestickData[] {
  const byTime = new Map<number, CandlestickData>();
  for (const row of rows || []) {
    const time = toTimestamp(row.t ?? row.time ?? row.timestamp);
    const open = numberOrNull(row.o ?? row.open);
    const high = numberOrNull(row.h ?? row.high);
    const low = numberOrNull(row.l ?? row.low);
    const close = numberOrNull(row.c ?? row.close);
    if (time == null || open == null || high == null || low == null || close == null) continue;
    byTime.set(Number(time), { time, open, high, low, close });
  }
  return Array.from(byTime.values()).sort((a, b) => Number(a.time) - Number(b.time));
}

function badgeClass(tone: BadgeTone): string {
  switch (tone) {
    case 'real':
    case 'ok':
      return 'border-long/40 bg-long/15 text-long';
    case 'proxy':
    case 'warn':
      return 'border-warning/45 bg-warning/15 text-warning';
    case 'danger':
      return 'border-short/45 bg-short/15 text-short';
    default:
      return 'border-border/60 bg-muted/30 text-muted-foreground';
  }
}

function passTone(value: boolean | null): BadgeTone {
  if (value === true) return 'ok';
  if (value === false) return 'danger';
  return 'muted';
}

function passLabel(value: boolean | null, unknown = 'pending backend source-contract patch'): string {
  if (value === true) return 'pass';
  if (value === false) return 'fail';
  return unknown;
}

function recordText(record: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    const text = toText(value, '');
    if (text) return text;
  }
  return null;
}

function recordBool(record: Record<string, unknown>, keys: string[]): boolean | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'boolean') return value;
    if (typeof value === 'string') {
      const normalized = value.trim().toLowerCase();
      if (normalized === 'true') return true;
      if (normalized === 'false') return false;
    }
  }
  return null;
}

function recordNumber(record: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = numberOrNull(record[key]);
    if (value != null) return value;
  }
  return null;
}

function recordArray(record: Record<string, unknown>, keys: string[]): string[] {
  for (const key of keys) {
    const value = record[key];
    if (!Array.isArray(value)) continue;
    return value.map((item) => toText(item, '')).filter(Boolean);
  }
  return [];
}

function recordNumberArray(record: Record<string, unknown>, keys: string[]): number[] {
  for (const key of keys) {
    const value = record[key];
    if (!Array.isArray(value)) continue;
    const levels = value.map(numberOrNull).filter((level): level is number => level != null);
    if (levels.length > 0) return levels;
  }
  return [];
}

function normalizeScalpWorkbenchRow(row: ScalpWorkbenchSignal | null): NormalizedScalpRow {
  const raw = asRecord(row);
  const source = asRecord(raw.sourceContract);
  const location = asRecord(raw.marketLocation);
  const aggression = asRecord(raw.aggressionContext);
  const setup = asRecord(raw.scalpSetup);
  const dataFidelity = asRecord(raw.data_fidelity);

  const sourceContract: NormalizedSourceContract = {
    symbol: recordText(source, ['symbol']) ?? recordText(raw, ['display', 'pair', 'symbol']),
    timeframe: recordText(source, ['timeframe']) ?? recordText(raw, ['execution_tf', 'timeframe', 'tf']),
    venue: recordText(source, ['venue']) ?? recordText(raw, ['venue']),
    expectedVenue: recordText(source, ['expectedVenue']) ?? recordText(raw, ['expected_venue', 'expectedVenue']),
    executionVenue: recordText(source, ['executionVenue']) ?? recordText(raw, ['execution_venue', 'execution_provider']),
    dataVenue: recordText(source, ['dataVenue']) ?? recordText(raw, ['analysis_venue', 'analysis_provider', 'data_venue']),
    venueMismatch: recordBool(source, ['venueMismatch']) ?? recordBool(raw, ['venue_mismatch']),
    venueMismatchReason: recordText(source, ['venueMismatchReason']) ?? recordText(raw, ['venue_mismatch_reason']),
    candleSource: recordText(source, ['candleSource']) ?? recordText(raw, ['candle_source', 'candleSource']),
    candleSourceIsReal: recordBool(source, ['candleSourceIsReal']) ?? recordBool(raw, ['candle_source_is_real']),
    volumeSource: recordText(source, ['volumeSource']) ?? recordText(raw, ['volume_source', 'vp_volume_source']),
    volumeSourceIsReal: recordBool(source, ['volumeSourceIsReal']) ?? recordBool(raw, ['volume_source_is_real']),
    orderflowSource: recordText(source, ['orderflowSource']) ?? recordText(raw, ['orderflow_source', 'aaa_source', 'aggression_source']),
    orderflowSourceIsReal: recordBool(source, ['orderflowSourceIsReal']) ?? recordBool(raw, ['orderflow_source_is_real', 'aaa_source_is_real', 'aggression_uses_real_order_flow']),
    cvdSource: recordText(source, ['cvdSource']) ?? recordText(raw, ['cvd_source']) ?? recordText(dataFidelity, ['cvd_source']),
    cvdSourceIsReal: recordBool(source, ['cvdSourceIsReal']) ?? recordBool(raw, ['cvd_source_is_real', 'cvd_uses_real_trade_buckets']),
    absorptionSource: recordText(source, ['absorptionSource']) ?? recordText(raw, ['absorption_source']) ?? recordText(dataFidelity, ['absorption_source']),
    absorptionSourceIsReal: recordBool(source, ['absorptionSourceIsReal']) ?? recordBool(raw, ['absorption_source_is_real']),
    vpSource: recordText(source, ['vpSource']) ?? recordText(raw, ['vp_source', 'vp_volume_source']) ?? recordText(dataFidelity, ['vp_source']),
    vpSourceIsReal: recordBool(source, ['vpSourceIsReal']) ?? recordBool(raw, ['vp_source_is_real', 'vp_uses_real_trade_buckets']),
    strictOrderflowSourcePass: recordBool(source, ['strictOrderflowSourcePass']) ?? recordBool(raw, ['strict_orderflow_source_pass']),
    strictVolumeSourcePass: recordBool(source, ['strictVolumeSourcePass']) ?? recordBool(raw, ['strict_volume_source_pass']),
    strictTimestampAlignmentPass: recordBool(source, ['strictTimestampAlignmentPass']) ?? recordBool(raw, ['strict_timestamp_alignment_pass']),
    strictVenuePass: recordBool(source, ['strictVenuePass']) ?? recordBool(raw, ['strict_venue_pass']),
    latestCandleTs: recordText(source, ['latestCandleTs']) ?? recordText(raw, ['latest_candle_ts']),
    orderflowTs: recordText(source, ['orderflowTs']) ?? recordText(raw, ['orderflow_ts']),
    cvdTs: recordText(source, ['cvdTs']) ?? recordText(raw, ['cvd_ts']),
    vpTs: recordText(source, ['vpTs']) ?? recordText(raw, ['vp_ts']),
    maxTimestampSkewSeconds: recordNumber(source, ['maxTimestampSkewSeconds']) ?? recordNumber(raw, ['max_timestamp_skew_seconds']),
    timestampAlignmentReason: recordText(source, ['timestampAlignmentReason']) ?? recordText(raw, ['timestamp_alignment_reason', 'timestamp_alignment_warning']),
    unavailableReasons: recordArray(source, ['unavailableReasons']).concat(recordArray(raw, ['source_unavailable_reasons'])),
    warnings: recordArray(source, ['warnings']).concat(recordArray(raw, ['soft_warnings'])),
  };

  const marketLocation: NormalizedMarketLocation = {
    locationLabel: recordText(location, ['locationLabel']) ?? recordText(raw, ['location', 'price_location', 'vp_location', 'zone_type']),
    locationScore: recordNumber(location, ['locationScore']) ?? recordNumber(raw, ['location_score']),
    nearPOC: recordBool(location, ['nearPOC']) ?? recordBool(raw, ['near_poc']),
    nearVAH: recordBool(location, ['nearVAH']) ?? recordBool(raw, ['near_vah']),
    nearVAL: recordBool(location, ['nearVAL']) ?? recordBool(raw, ['near_val']),
    nearLVN: recordBool(location, ['nearLVN']) ?? recordBool(raw, ['near_lvn']),
    nearHVN: recordBool(location, ['nearHVN']) ?? recordBool(raw, ['near_hvn']),
    aboveValueArea: recordBool(location, ['aboveValueArea']) ?? recordBool(raw, ['above_value_area']),
    belowValueArea: recordBool(location, ['belowValueArea']) ?? recordBool(raw, ['below_value_area']),
    insideValueArea: recordBool(location, ['insideValueArea']) ?? recordBool(raw, ['inside_value_area']),
    poc: recordNumber(location, ['poc']) ?? recordNumber(raw, ['vp_poc', 'poc']),
    vah: recordNumber(location, ['vah']) ?? recordNumber(raw, ['vp_vah', 'vah']),
    val: recordNumber(location, ['val']) ?? recordNumber(raw, ['vp_val', 'val']),
    lvnLevels: recordNumberArray(location, ['lvnLevels']).length > 0
      ? recordNumberArray(location, ['lvnLevels'])
      : recordNumberArray(raw, ['lvn_levels', 'vp_lvn_levels', 'lvns']),
    hvnLevels: recordNumberArray(location, ['hvnLevels']).length > 0
      ? recordNumberArray(location, ['hvnLevels'])
      : recordNumberArray(raw, ['hvn_levels', 'vp_hvn_levels', 'hvns']),
    nearestLVN: recordNumber(location, ['nearestLVN']) ?? recordNumber(raw, ['nearest_lvn']),
    nearestHVN: recordNumber(location, ['nearestHVN']) ?? recordNumber(raw, ['nearest_hvn']),
    distanceToPOC: recordNumber(location, ['distanceToPOC']) ?? recordNumber(raw, ['distance_to_poc']),
    distanceToVAH: recordNumber(location, ['distanceToVAH']) ?? recordNumber(raw, ['distance_to_vah']),
    distanceToVAL: recordNumber(location, ['distanceToVAL']) ?? recordNumber(raw, ['distance_to_val']),
    distanceToNearestLVN: recordNumber(location, ['distanceToNearestLVN']) ?? recordNumber(raw, ['distance_to_nearest_lvn']),
    distanceToNearestHVN: recordNumber(location, ['distanceToNearestHVN']) ?? recordNumber(raw, ['distance_to_nearest_hvn']),
  };

  const aggressionContext: NormalizedAggressionContext = {
    aggressionLabel: recordText(aggression, ['aggressionLabel']) ?? recordText(raw, ['aggression_label', 'aggression_source']),
    aggressionScore: recordNumber(aggression, ['aggressionScore']) ?? recordNumber(raw, ['aggression_score']),
    buyAggression: recordNumber(aggression, ['buyAggression']) ?? recordNumber(raw, ['buy_aggression']),
    sellAggression: recordNumber(aggression, ['sellAggression']) ?? recordNumber(raw, ['sell_aggression']),
    delta: recordNumber(aggression, ['delta']) ?? recordNumber(raw, ['delta']),
    cvd: recordNumber(aggression, ['cvd']) ?? recordNumber(raw, ['cvd']),
    cvdSlope: recordNumber(aggression, ['cvdSlope']) ?? recordNumber(raw, ['cvd_slope']),
    absorptionDetected: recordBool(aggression, ['absorptionDetected']) ?? (recordNumber(raw, ['absorption_count']) != null ? (recordNumber(raw, ['absorption_count']) ?? 0) > 0 : null),
    absorptionSide: recordText(aggression, ['absorptionSide']) ?? recordText(raw, ['absorption_side']),
    sweepDetected: recordBool(aggression, ['sweepDetected']) ?? recordBool(raw, ['sweep_detected']),
    sweepSide: recordText(aggression, ['sweepSide']) ?? recordText(raw, ['sweep_side']),
    imbalanceDetected: recordBool(aggression, ['imbalanceDetected']) ?? recordBool(raw, ['imbalance_detected']),
    imbalanceSide: recordText(aggression, ['imbalanceSide']) ?? recordText(raw, ['imbalance_side']),
  };

  const scalpSetup: NormalizedScalpSetup = {
    setupType: recordText(setup, ['setupType']) ?? recordText(raw, ['setup_type', 'zone_type', 'trigger_type']),
    direction: recordText(setup, ['direction']) ?? recordText(raw, ['direction']),
    entry: recordNumber(setup, ['entry']) ?? recordNumber(raw, ['entry', 'price']),
    stopLoss: recordNumber(setup, ['stopLoss']) ?? recordNumber(raw, ['sl']),
    tp1: recordNumber(setup, ['tp1']) ?? recordNumber(raw, ['tp1']),
    tp2: recordNumber(setup, ['tp2']) ?? recordNumber(raw, ['tp2']),
    rr1: recordNumber(setup, ['rr1']) ?? recordNumber(raw, ['rr1', 'rr']),
    rr2: recordNumber(setup, ['rr2']) ?? recordNumber(raw, ['rr2', 'structural_rr']),
    riskDistance: recordNumber(setup, ['riskDistance']) ?? recordNumber(raw, ['sl_distance']),
    rewardDistance1: recordNumber(setup, ['rewardDistance1']),
    rewardDistance2: recordNumber(setup, ['rewardDistance2']),
    invalidationReason: recordText(setup, ['invalidationReason']) ?? recordText(raw, ['sl_method', 'invalidation_reason']),
    strictFabioPass: recordBool(setup, ['strictFabioPass']) ?? recordBool(raw, ['strict_fabio_method_pass', 'strict_fabio_pass']),
    strictGateReasons: recordArray(setup, ['strictGateReasons']).concat(recordArray(raw, ['strict_fabio_fail_reasons'])),
    skipped: recordBool(setup, ['skipped']) ?? false,
    skippedReason: recordText(setup, ['skippedReason']) ?? recordText(raw, ['reason']),
  };

  return {
    raw: row,
    symbol: sourceContract.symbol,
    timeframe: sourceContract.timeframe,
    engineStatus: recordText(raw, ['candidate_status', 'gate_result']),
    executable: recordBool(raw, ['executable']),
    marketState: recordText(raw, ['market_state']),
    sourceContract,
    marketLocation,
    aggressionContext,
    scalpSetup,
  };
}

function isoFromLogicalTime(value: unknown): string | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return new Date(value * 1000).toISOString();
  }
  if (typeof value === 'string' && value.trim()) return value;
  return null;
}

function buildScalpChartSnapshot(
  ui: NormalizedScalpRow,
  args: {
    symbol: string | null;
    timeframe: string;
    visibleRange: { from?: unknown; to?: unknown } | null;
  },
): ScalpChartSnapshot {
  return {
    symbol: args.symbol,
    timeframe: ui.timeframe ?? args.timeframe,
    chartCapturedAt: new Date().toISOString(),
    visibleRange: {
      from: isoFromLogicalTime(args.visibleRange?.from),
      to: isoFromLogicalTime(args.visibleRange?.to),
    },
    selectedSignal: {
      direction: ui.scalpSetup.direction,
      setupType: ui.scalpSetup.setupType,
      entry: ui.scalpSetup.entry,
      stopLoss: ui.scalpSetup.stopLoss,
      tp1: ui.scalpSetup.tp1,
      tp2: ui.scalpSetup.tp2,
      rr1: ui.scalpSetup.rr1,
      rr2: ui.scalpSetup.rr2,
    },
    marketLocation: {
      locationLabel: ui.marketLocation.locationLabel,
      locationScore: ui.marketLocation.locationScore,
      poc: ui.marketLocation.poc,
      vah: ui.marketLocation.vah,
      val: ui.marketLocation.val,
      nearestLVN: ui.marketLocation.nearestLVN,
      nearestHVN: ui.marketLocation.nearestHVN,
      lvnLevels: [...ui.marketLocation.lvnLevels],
      hvnLevels: [...ui.marketLocation.hvnLevels],
    },
    aggressionContext: {
      aggressionLabel: ui.aggressionContext.aggressionLabel,
      aggressionScore: ui.aggressionContext.aggressionScore,
      delta: ui.aggressionContext.delta,
      cvd: ui.aggressionContext.cvd,
      cvdSlope: ui.aggressionContext.cvdSlope,
      absorptionDetected: ui.aggressionContext.absorptionDetected,
      absorptionSide: ui.aggressionContext.absorptionSide,
      sweepDetected: ui.aggressionContext.sweepDetected,
      sweepSide: ui.aggressionContext.sweepSide,
      imbalanceDetected: ui.aggressionContext.imbalanceDetected,
      imbalanceSide: ui.aggressionContext.imbalanceSide,
    },
    sourceContract: {
      candleSource: ui.sourceContract.candleSource,
      candleSourceIsReal: ui.sourceContract.candleSourceIsReal,
      volumeSource: ui.sourceContract.volumeSource,
      volumeSourceIsReal: ui.sourceContract.volumeSourceIsReal,
      orderflowSource: ui.sourceContract.orderflowSource,
      orderflowSourceIsReal: ui.sourceContract.orderflowSourceIsReal,
      cvdSource: ui.sourceContract.cvdSource,
      cvdSourceIsReal: ui.sourceContract.cvdSourceIsReal,
      vpSource: ui.sourceContract.vpSource,
      vpSourceIsReal: ui.sourceContract.vpSourceIsReal,
      strictOrderflowSourcePass: ui.sourceContract.strictOrderflowSourcePass,
      strictVolumeSourcePass: ui.sourceContract.strictVolumeSourcePass,
      strictTimestampAlignmentPass: ui.sourceContract.strictTimestampAlignmentPass,
      strictVenuePass: ui.sourceContract.strictVenuePass,
      unavailableReasons: [...ui.sourceContract.unavailableReasons],
    },
  };
}

function sourceQualitySummaryFor(source: NormalizedSourceContract): string {
  const realCount = [
    source.candleSourceIsReal,
    source.volumeSourceIsReal,
    source.orderflowSourceIsReal,
    source.cvdSourceIsReal,
    source.vpSourceIsReal,
  ].filter((value) => value === true).length;
  const unavailableCount = source.unavailableReasons.length;
  if (source.strictOrderflowSourcePass === false) return `order-flow source blocked (${unavailableCount} unavailable)`;
  if (realCount >= 4 && unavailableCount === 0) return 'mostly real source context';
  if (realCount > 0) return `mixed source context (${realCount}/5 real)`;
  return unavailableCount > 0 ? `source context unavailable (${unavailableCount} reasons)` : 'source context unknown';
}

function captureNativeChartCanvas(captureEl: HTMLElement | null): HTMLCanvasElement | null {
  if (!captureEl) return null;
  const canvases = Array.from(captureEl.querySelectorAll('canvas'));
  if (canvases.length === 0) return null;

  const captureRect = captureEl.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  const outputCanvas = document.createElement('canvas');
  outputCanvas.width = Math.max(1, Math.round(captureRect.width * scale));
  outputCanvas.height = Math.max(1, Math.round(captureRect.height * scale));
  const outputCtx = outputCanvas.getContext('2d');
  if (!outputCtx) return null;

  outputCtx.scale(scale, scale);
  outputCtx.fillStyle = '#080c10';
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
  return outputCanvas;
}

function estimateDataUrlBytes(dataUrl: string | null): number | null {
  if (!dataUrl) return null;
  const b64 = dataUrl.split(',', 2)[1] || '';
  return Math.round((b64.length * 3) / 4);
}

function textValue(value: string | null, fallback = 'unknown'): string {
  return value == null || value === '' ? fallback : value;
}

function numberValue(value: number | null): string {
  return value == null ? '—' : fmtNum(value, Math.abs(value) >= 100 ? 2 : 5);
}

function boolLabel(value: boolean | null, unknown = 'unknown'): string {
  if (value === true) return 'yes';
  if (value === false) return 'no';
  return unknown;
}

function sourceStatusTone(real: boolean | null, source: string | null): BadgeTone {
  if (real === true) return 'real';
  if (real === false && source) return 'proxy';
  return 'muted';
}

function sourceStatusLabel(real: boolean | null, source: string | null): string {
  if (real === true) return 'real';
  if (real === false && source) return 'unavailable';
  return 'unknown';
}

function FieldRow({ label, value, tone = 'muted' }: { label: string; value: string; tone?: BadgeTone }) {
  return (
    <div className="flex items-center justify-between gap-3 py-0.5">
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <Badge variant="outline" className={cn('max-w-[190px] justify-end truncate text-[10px]', badgeClass(tone))}>
        {value}
      </Badge>
    </div>
  );
}

function SourceRow({
  label,
  source,
  real,
  proxy,
}: {
  label: string;
  source: string;
  real: boolean | null;
  proxy: boolean | null;
}) {
  return (
    <div className="grid grid-cols-[72px_1fr_auto] items-center gap-2 py-0.5">
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="truncate text-xs text-foreground/80">{source}</span>
      <Badge variant="outline" className={cn('text-[10px]', badgeClass(proxy === true ? 'proxy' : sourceStatusTone(real, source)))}>
        {proxy === true ? 'proxy' : sourceStatusLabel(real, source)}
      </Badge>
    </div>
  );
}

function WarningPill({ children, tone = 'warn' }: { children: string; tone?: BadgeTone }) {
  return (
    <Badge variant="outline" className={cn('max-w-full truncate text-[10px]', badgeClass(tone))}>
      {children}
    </Badge>
  );
}

export default function ScalpWorkbenchPanel() {
  const {
    scalpLabScanCache,
    scalpLabSelectedCache,
    setScalpLabScanCache,
    setScalpLabSelectedCache,
    scalpWorkbenchIntent,
    clearScalpWorkbenchIntent,
    showToast,
    isTestMode,
  } = useStore();
  const { post: postScan, loading: scanLoading, error: scanError } = useApiPost<ScalpScanResponse>();
  const { post: postExecute, loading: executingScalp } = useApiPost<ScalpExecuteResponse>();
  const [symbolOverride, setSymbolOverride] = useState('');
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>('M5');
  const [candlePayload, setCandlePayload] = useState<CandleApiResponse | null>(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);
  const [aiCaptureError, setAiCaptureError] = useState<string | null>(null);
  const [aiReviewLoading, setAiReviewLoading] = useState(false);
  const [scalpAiReviewResponse, setScalpAiReviewResponse] = useState<ScalpAIChartReviewResponse | null>(null);
  const [scalpAiReviewPreview, setScalpAiReviewPreview] = useState<ScalpAIReviewPreview | null>(null);
  const [tfDisplayOverride, setTfDisplayOverride] = useState(false);
  const [intentSourceBadge, setIntentSourceBadge] = useState<string | null>(null);
  const [autoReviewStatus, setAutoReviewStatus] = useState<'idle' | 'pending' | 'running' | 'done'>('idle');
  const [activeWatches, setActiveWatches] = useState<SuggestedTradeWatch[]>([]);
  const [flagStatus, setFlagStatus] = useState<string | null>(null);
  const [flagLoading, setFlagLoading] = useState(false);
  const [confirmExecOpen, setConfirmExecOpen] = useState(false);
  const [sizingOverride] = useState(1.0);
  const appliedIntentIdRef = useRef<string | null>(null);
  const pendingAutoReviewRef = useRef(false);
  const autoReviewRanForIntentRef = useRef<string | null>(null);
  const [showTradeLevels, setShowTradeLevels] = useState(true);
  const [showProfileLevels, setShowProfileLevels] = useState(true);
  const [showLvnLevels, setShowLvnLevels] = useState(true);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartCaptureRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);

  const scanResult = scalpLabScanCache as ScalpScanResponse | null;
  const selectedCache = scalpLabSelectedCache as ScalpWorkbenchSignal | null;
  const scanSignals = useMemo(
    () => (Array.isArray(scanResult?.signals) ? scanResult.signals : []),
    [scanResult],
  );

  const symbolOptions = useMemo(() => {
    const map = new Map<string, string>();
    const selected = signalSymbol(selectedCache);
    if (selected) map.set(selected.toUpperCase(), selected);
    for (const signal of scanSignals) {
      const symbol = signalSymbol(signal);
      if (symbol) map.set(symbol.toUpperCase(), symbol);
    }
    return Array.from(map.entries()).map(([key, label]) => ({ key, label }));
  }, [scanSignals, selectedCache]);

  const activeSymbolKey = symbolOverride || signalKey(selectedCache) || signalKey(scanSignals[0]);
  const activeSignal = useMemo(() => {
    if (!activeSymbolKey) return selectedCache || scanSignals[0] || null;
    return scanSignals.find((signal) => signalKey(signal) === activeSymbolKey)
      || (signalKey(selectedCache) === activeSymbolKey ? selectedCache : null)
      || scanSignals[0]
      || null;
  }, [activeSymbolKey, scanSignals, selectedCache]);
  const chartSymbol = signalSymbol(activeSignal);
  const activeUi = useMemo(() => normalizeScalpWorkbenchRow(activeSignal), [activeSignal]);
  const executionTf = useMemo(() => {
    const raw = activeUi.timeframe || activeSignal?.execution_tf || activeSignal?.timeframe || 'M1';
    const normalized = String(raw).toUpperCase();
    return TIMEFRAMES.includes(normalized as (typeof TIMEFRAMES)[number])
      ? (normalized as (typeof TIMEFRAMES)[number])
      : 'M1';
  }, [activeSignal, activeUi.timeframe]);

  useEffect(() => {
    if (!tfDisplayOverride) {
      setTimeframe(preferredScalpDisplayTf(activeSignal));
    }
  }, [activeSignal, activeSymbolKey, tfDisplayOverride]);

  useEffect(() => {
    if (!scalpWorkbenchIntent?.id || scalpWorkbenchIntent.id === appliedIntentIdRef.current) return;
    appliedIntentIdRef.current = scalpWorkbenchIntent.id;
    const intentSymbol = String(scalpWorkbenchIntent.symbol || '').toUpperCase().trim();
    if (intentSymbol) {
      setSymbolOverride(intentSymbol);
      if (scalpWorkbenchIntent.signal) {
        setScalpLabSelectedCache(scalpWorkbenchIntent.signal);
      }
    }
    if (!tfDisplayOverride) {
      const pref = scalpWorkbenchIntent.preferredTf?.toUpperCase();
      if (pref === 'M1' || pref === 'M5' || pref === 'M15') {
        setTimeframe(pref);
      } else {
        setTimeframe(preferredScalpDisplayTf(scalpWorkbenchIntent.signal as ScalpWorkbenchSignal));
      }
    }
    setScalpAiReviewResponse(null);
    pendingAutoReviewRef.current = scalpWorkbenchIntent.autoReview === true;
    autoReviewRanForIntentRef.current = null;
    setIntentSourceBadge(
      scalpWorkbenchIntent.source === 'scalp_lab' ? 'Opened from Scalp Lab' : 'Opened from manual',
    );
    setAutoReviewStatus(scalpWorkbenchIntent.autoReview ? 'pending' : 'idle');
    clearScalpWorkbenchIntent();
  }, [scalpWorkbenchIntent, clearScalpWorkbenchIntent, setScalpLabSelectedCache, tfDisplayOverride]);

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
        /* alert-only poll */
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
    const top = scalpAiReviewResponse?.suggestedTradePlan ?? scalpAiReviewResponse?.suggested_trade_plan;
    if (top && typeof top === 'object') return top;
    const nested = scalpAiReviewResponse?.ai_review as { suggestedTradePlan?: SuggestedTradePlan } | undefined;
    return nested?.suggestedTradePlan ?? null;
  }, [scalpAiReviewResponse]);

  const canFlagWatch = Boolean(
    suggestedPlan?.armable
    && ['WAIT_FOR_LEVEL', 'WAIT_FOR_ZONE'].includes(String(suggestedPlan?.action || '').toUpperCase()),
  );

  const executeBlockReason = useMemo(
    () => evaluateScalpExecuteBlock({
      signal: activeSignal
        ? {
            ...activeSignal,
            strictOrderflowSourcePass: activeUi.sourceContract.strictOrderflowSourcePass,
          }
        : null,
      aiReview: scalpAiReviewResponse,
      isTestMode,
    }),
    [activeSignal, activeUi.sourceContract.strictOrderflowSourcePass, scalpAiReviewResponse, isTestMode],
  );

  const onConfirmScalpExecute = useCallback(async () => {
    if (!activeSignal || !chartSymbol || executeBlockReason) return;
    const payload = buildScalpExecutePayload({
      symbol: chartSymbol,
      signal: activeSignal,
      sizingOverride,
    });
    const result = await postExecute('/api/scalp-execute', payload);
    if (!result) {
      showToast('Execution failed (no response)', 'error');
    } else if (result.success) {
      showToast(`Scalp executed — ticket ${result.ticket || '?'}`, 'success');
    } else {
      showToast(`Scalp rejected: ${result.error || 'unknown'}`, 'error');
    }
    setConfirmExecOpen(false);
  }, [activeSignal, chartSymbol, executeBlockReason, postExecute, showToast, sizingOverride]);

  const flagWatchSetup = useCallback(async () => {
    if (!canFlagWatch || !suggestedPlan || flagLoading) return;
    setFlagLoading(true);
    setFlagStatus(null);
    try {
      const res = await apiClient.postJson('/api/suggested-trades/flag', {
        symbol: chartSymbol,
        display: activeSignal?.display || activeSignal?.pair || chartSymbol,
        signal: activeSignal,
        suggestedTradePlan: suggestedPlan,
        aiReviewSummary: scalpAiReviewResponse?.aiReviewSummary ?? scalpAiReviewResponse?.ai_review_summary,
        source: 'ai_scalp_chart_review',
        createdAt: new Date().toISOString(),
      }) as { success?: boolean; error?: string };
      if (res?.success) {
        setFlagStatus('Watching setup flagged (alert-only)');
        showToast('Scalp setup flagged for watch — alert only', 'success');
      } else {
        setFlagStatus(res?.error || 'Flag failed');
      }
    } catch (err) {
      setFlagStatus(err instanceof Error ? err.message : 'Flag failed');
    } finally {
      setFlagLoading(false);
    }
  }, [activeSignal, canFlagWatch, chartSymbol, flagLoading, scalpAiReviewResponse, showToast, suggestedPlan]);

  function watchLabel(watch: SuggestedTradeWatch): string {
    const dir = watch.direction || '';
    if (watch.zone_low != null && watch.zone_high != null) {
      return `Watching: ${watch.zone_low}-${watch.zone_high} ${dir} zone`;
    }
    if (watch.level != null) {
      return `Watching: ${watch.level} ${dir}`;
    }
    return `Watching: ${watch.symbol || ''} ${dir}`.trim();
  }

  const candleRows = useMemo(() => toCandleData(candlePayload?.candles), [candlePayload]);
  const dataFidelity = asRecord(activeSignal?.data_fidelity);
  const strictPillars = asRecord(activeSignal?.strict_fabio_pillars);
  const marketState = textValue(activeUi.marketState);
  const location = textValue(activeUi.marketLocation.locationLabel);
  const aggressionProxy = boolField(activeSignal, ['aggression_source_is_proxy']);
  const aggressionConfirmed = activeUi.aggressionContext.aggressionLabel === 'confirmed'
    ? true
    : activeUi.aggressionContext.aggressionLabel === 'missing'
      ? false
      : boolField(activeSignal, ['aggression_confirmed']);
  const aggressionStatus = aggressionConfirmed === true
    ? (aggressionProxy === true ? 'proxy' : 'confirmed')
    : aggressionConfirmed === false ? 'missing' : 'unknown';
  const aggressionTone: BadgeTone = aggressionStatus === 'confirmed' ? 'ok' : aggressionStatus === 'proxy' ? 'proxy' : aggressionStatus === 'missing' ? 'danger' : 'muted';
  const venueMismatch = activeUi.sourceContract.venueMismatch;
  const strictFabioMethod = activeUi.scalpSetup.strictFabioPass;
  const strictOrderflowSource = activeUi.sourceContract.strictOrderflowSourcePass;

  const sourceSummary = useMemo(() => {
    const explicit = fieldText(activeUi.raw, ['source_fidelity_summary'], '');
    if (explicit) return explicit;
    if (activeUi.sourceContract.orderflowSourceIsReal === true) return 'real order flow';
    if (
      activeUi.sourceContract.orderflowSourceIsReal === false
      || activeUi.sourceContract.vpSourceIsReal === false
      || activeUi.sourceContract.cvdSourceIsReal === false
      || activeUi.sourceContract.absorptionSourceIsReal === false
      || aggressionProxy === true
      || activeUi.raw?.vp_is_proxy === true
      || activeUi.raw?.cvd_is_proxy === true
      || activeUi.raw?.absorption_is_proxy === true
      || dataFidelity.vp_is_proxy === true
      || dataFidelity.cvd_is_proxy === true
      || dataFidelity.absorption_is_proxy === true
    ) return 'proxy / mixed';
    return 'unknown';
  }, [activeUi, aggressionProxy, dataFidelity]);

  const sourceSummaryTone: BadgeTone = sourceSummary.includes('real') ? 'real' : sourceSummary.includes('proxy') || sourceSummary.includes('mixed') ? 'proxy' : 'muted';

  const priceLevels = useMemo<PriceLevel[]>(() => {
    const levels: PriceLevel[] = [];
    if (showTradeLevels) {
      const entry = activeUi.scalpSetup.entry;
      const sl = activeUi.scalpSetup.stopLoss;
      const tp1 = activeUi.scalpSetup.tp1;
      const tp2 = activeUi.scalpSetup.tp2;
      if (entry != null) levels.push({ label: 'ENTRY', price: entry, color: 'hsl(45, 95%, 58%)' });
      if (sl != null) levels.push({ label: 'SL', price: sl, color: 'hsl(343, 96%, 60%)' });
      if (tp1 != null) levels.push({ label: 'TP1', price: tp1, color: 'hsl(160, 84%, 39%)' });
      if (tp2 != null) levels.push({ label: 'TP2', price: tp2, color: 'hsl(160, 84%, 48%)', style: LineStyle.Dotted });
    }
    if (showProfileLevels) {
      const poc = activeUi.marketLocation.poc;
      const vah = activeUi.marketLocation.vah;
      const val = activeUi.marketLocation.val;
      if (poc != null) levels.push({ label: 'POC', price: poc, color: 'hsl(200, 95%, 55%)' });
      if (vah != null) levels.push({ label: 'VAH', price: vah, color: 'hsl(265, 80%, 68%)', style: LineStyle.Dashed });
      if (val != null) levels.push({ label: 'VAL', price: val, color: 'hsl(265, 80%, 68%)', style: LineStyle.Dashed });
    }
    if (showLvnLevels) {
      for (const [index, price] of activeUi.marketLocation.lvnLevels.entries()) {
        levels.push({ label: `LVN ${index + 1}`, price, color: 'hsl(30, 92%, 58%)', style: LineStyle.Dotted });
      }
      for (const [index, price] of activeUi.marketLocation.hvnLevels.entries()) {
        levels.push({ label: `HVN ${index + 1}`, price, color: 'hsl(190, 82%, 52%)', style: LineStyle.Dotted });
      }
    }
    return levels;
  }, [activeUi, showLvnLevels, showProfileLevels, showTradeLevels]);
  const refreshScan = useCallback(async () => {
    const result = await postScan('/api/scalp-scan', { diagnostic: true });
    if (!result) {
      showToast('Engine D scan refresh failed', 'error');
      return;
    }
    setScalpLabScanCache(result);
    const first = Array.isArray(result.signals) ? result.signals[0] : null;
    setScalpLabSelectedCache(first || null);
    setSymbolOverride(first ? signalKey(first) : '');
    showToast('Engine D scan refreshed', result.error ? 'error' : 'success');
  }, [postScan, setScalpLabScanCache, setScalpLabSelectedCache, showToast]);

  const selectSymbol = useCallback((key: string) => {
    if (key === EMPTY_SYMBOL_SELECT_VALUE) return;
    setSymbolOverride(key);
    const next = scanSignals.find((signal) => signalKey(signal) === key) || null;
    if (next) setScalpLabSelectedCache(next);
  }, [scanSignals, setScalpLabSelectedCache]);

  const copyPayload = useCallback(async () => {
    if (!activeSignal) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(activeSignal, null, 2));
      showToast('Engine D candidate JSON copied', 'success');
    } catch {
      showToast('Copy failed: clipboard unavailable', 'error');
    }
  }, [activeSignal, showToast]);

  const captureScalpChartForAIReview = useCallback(async () => {
    setAiCaptureError(null);
    setScalpAiReviewResponse(null);
    if (!activeSignal || !chartSymbol) {
      setAiCaptureError('Select an Engine D candidate before capturing the chart');
      return;
    }

    const visibleRange = chartRef.current?.timeScale().getVisibleRange() ?? null;
    const scalpChartSnapshot = buildScalpChartSnapshot(activeUi, {
      symbol: chartSymbol,
      timeframe,
      visibleRange: visibleRange as { from?: unknown; to?: unknown } | null,
    });
    const sourceCanvas = captureNativeChartCanvas(chartCaptureRef.current);
    if (!sourceCanvas) {
      setAiCaptureError('Chart capture failed: native chart canvas is not rendered yet');
      return;
    }

    setAiReviewLoading(true);
    try {
      const outputCanvas = downscaleToCap(sourceCanvas);
      const screenshotBase64 = await canvasToDataUrl(outputCanvas);
      const overlays: string[] = [];
      if (showTradeLevels) overlays.push('entry_sl_tp');
      if (showProfileLevels) overlays.push('poc_vah_val');
      if (showLvnLevels) overlays.push('lvn_hvn');
      const screenshotMeta = buildScalpScreenshotMeta({
        width: outputCanvas.width,
        height: outputCanvas.height,
        chart_timeframe: timeframe,
        execution_tf: executionTf,
        overlays,
        visible_range_start: visibleRange?.from != null ? String(visibleRange.from) : undefined,
        visible_range_end: visibleRange?.to != null ? String(visibleRange.to) : undefined,
        chart_provider: candlePayload?.chart_provider || candlePayload?.candle_provider,
      });
      setScalpAiReviewPreview({
        scalpChartSnapshot,
        screenshotCaptured: true,
        screenshotBytes: estimateDataUrlBytes(screenshotBase64),
        screenshotSize: { width: outputCanvas.width, height: outputCanvas.height },
        screenshotBase64,
        sourceQualitySummary: sourceQualitySummaryFor(activeUi.sourceContract),
        expectedResultShape: null,
        stagePlan: {
          visionInput: ['chart screenshot', 'symbol/timeframe', 'selected direction/setup'],
          compilerInput: ['server engine_d_context', 'vision facts JSON', 'source/location/aggression/setup context'],
          outputContract: 'scalpAIReview',
        },
      });
      const response = await postScalpChartReview({
        symbol: chartSymbol,
        timeframe,
        provider: 'default',
        screenshot_base64: screenshotBase64,
        screenshot_meta: screenshotMeta,
      });
      setScalpAiReviewResponse(response);
      showToast('Engine D AI chart review complete', 'success');
    } catch (err) {
      setAiCaptureError(err instanceof Error ? err.message : 'AI chart review failed');
      showToast('Engine D AI chart review failed', 'error');
    } finally {
      setAiReviewLoading(false);
    }
  }, [
    activeSignal,
    activeUi,
    candlePayload,
    chartSymbol,
    executionTf,
    timeframe,
    showLvnLevels,
    showProfileLevels,
    showTradeLevels,
    showToast,
  ]);

  useEffect(() => {
    if (!pendingAutoReviewRef.current || chartLoading || aiReviewLoading || !activeSignal || !chartSymbol) return;
    const intentId = appliedIntentIdRef.current;
    if (!intentId || autoReviewRanForIntentRef.current === intentId) return;
    autoReviewRanForIntentRef.current = intentId;
    pendingAutoReviewRef.current = false;
    setAutoReviewStatus('running');
    void captureScalpChartForAIReview().finally(() => setAutoReviewStatus('done'));
  }, [chartLoading, aiReviewLoading, activeSignal, chartSymbol, candlePayload, captureScalpChartForAIReview]);

  useEffect(() => {
    if (!chartSymbol) {
      const id = window.setTimeout(() => {
        setCandlePayload(null);
        setChartError(null);
      }, 0);
      return () => window.clearTimeout(id);
    }
    let cancelled = false;
    const id = window.setTimeout(() => {
      if (cancelled) return;
      setChartLoading(true);
      setChartError(null);
      apiClient
        .getJson(`/api/candles?symbol=${encodeURIComponent(chartSymbol)}&tf=${encodeURIComponent(timeframe)}&limit=${CANDLE_LIMIT}`)
        .then((response) => {
          if (cancelled) return;
          const payload = response as CandleApiResponse;
          setCandlePayload(payload);
          if (payload.error) {
            setChartError(payload.error);
            return;
          }
          if (!Array.isArray(payload.candles) || payload.candles.length === 0) {
            setChartError(`No candle data for ${chartSymbol} ${timeframe}`);
          }
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setCandlePayload(null);
          setChartError(err instanceof Error ? err.message : 'Failed to load candles');
        })
        .finally(() => {
          if (!cancelled) setChartLoading(false);
        });
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(id);
    };
  }, [chartSymbol, timeframe]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight || 540,
      layout: {
        background: { color: 'transparent' },
        textColor: 'rgba(245, 240, 232, 0.68)',
        fontFamily: "'IBM Plex Mono', monospace",
      },
      grid: {
        vertLines: { color: 'rgba(212, 160, 23, 0.04)' },
        horzLines: { color: 'rgba(212, 160, 23, 0.04)' },
      },
      rightPriceScale: { borderColor: 'rgba(212, 160, 23, 0.16)' },
      timeScale: {
        borderColor: 'rgba(212, 160, 23, 0.16)',
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: { mode: 1 },
    });
    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: 'hsl(160, 84%, 39%)',
      downColor: 'hsl(343, 96%, 60%)',
      borderUpColor: 'hsl(160, 84%, 39%)',
      borderDownColor: 'hsl(343, 96%, 60%)',
      wickUpColor: 'hsl(160, 84%, 39%)',
      wickDownColor: 'hsl(343, 96%, 60%)',
      lastValueVisible: true,
      priceLineVisible: true,
    });
    candleSeriesRef.current = candleSeries;
    candleSeries.setData(candleRows);

    for (const level of priceLevels) {
      candleSeries.createPriceLine({
        price: level.price,
        color: level.color,
        lineWidth: 1,
        lineStyle: level.style ?? LineStyle.Dashed,
        axisLabelVisible: true,
        title: level.label,
      });
    }

    if (candleRows.length > 0) chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(() => {
      chart.applyOptions({
        width: container.clientWidth,
        height: container.clientHeight || 540,
      });
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
    };
  }, [candleRows, priceLevels]);

  const warnings = useMemo(() => {
    const items: Array<{ text: string; tone: BadgeTone }> = [];
    const push = (value: unknown, tone: BadgeTone = 'warn') => {
      const text = toText(value, '');
      if (text) items.push({ text, tone });
    };

    push(activeSignal?.advisory_summary, 'muted');
    for (const reason of activeSignal?.fail_reasons || []) push(reason, 'danger');
    for (const reason of activeSignal?.soft_warnings || []) push(reason, 'warn');
    for (const reason of activeUi.scalpSetup.strictGateReasons) push(reason, 'danger');
    for (const reason of activeUi.sourceContract.unavailableReasons) push(reason, 'proxy');
    for (const reason of activeUi.sourceContract.warnings) push(reason, 'warn');
    push(activeSignal?.strict_fabio_reason, strictFabioMethod === false ? 'danger' : 'muted');
    if (venueMismatch === true) push(activeUi.sourceContract.venueMismatchReason || 'venue mismatch reported', 'danger');
    if (candlePayload?.provider_mismatch) push(candlePayload.provider_mismatch_reason || 'chart provider mismatch', 'danger');
    push(candlePayload?.fallback_reason, 'warn');
    if (chartError) push(chartError, 'warn');
    if (items.length === 0) push('No candidate warnings in current payload', 'muted');
    return items;
  }, [activeSignal, activeUi, candlePayload, chartError, strictFabioMethod, venueMismatch]);

  const skippedRows = useMemo(
    () => (Array.isArray(scanResult?.skipped) ? scanResult.skipped.slice(0, 6).map((row) => normalizeScalpWorkbenchRow(row)) : []),
    [scanResult],
  );
  const noLvnLevels = showLvnLevels && activeSignal && activeUi.marketLocation.lvnLevels.length === 0;
  const chartProviderRows = [
    ['Chart provider', candlePayload?.chart_provider || candlePayload?.candle_provider || 'unknown'],
    ['Chart status', candlePayload?.chart_status || 'unknown'],
  ];
  const scalpAiReviewDebugPayload = useMemo(() => {
    if (!scalpAiReviewPreview) return null;
    return {
      ...scalpAiReviewPreview,
      screenshotBase64: 'base64 hidden',
    };
  }, [scalpAiReviewPreview]);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h2 className="text-xl font-semibold tracking-tight text-foreground">Engine D Naked Scalp Workbench</h2>
          <p className="mt-1 max-w-3xl text-xs text-muted-foreground">
            Read-only native chart shell for Market State, Location, and Aggression review.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={refreshScan} disabled={scanLoading}>
            <RefreshCw className={cn('mr-2 h-4 w-4', scanLoading && 'animate-spin')} />
            Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={captureScalpChartForAIReview} disabled={!activeSignal || chartLoading || aiReviewLoading}>
            <Camera className={cn('mr-2 h-4 w-4', aiReviewLoading && 'animate-pulse')} />
            {aiReviewLoading ? 'Reviewing…' : 'Capture for AI review'}
          </Button>
          <Button
            variant="default"
            size="sm"
            disabled={Boolean(executeBlockReason) || executingScalp || !activeSignal}
            onClick={() => setConfirmExecOpen(true)}
          >
            <Play className={cn('mr-2 h-4 w-4', executingScalp && 'animate-pulse')} />
            {executeBlockReason || 'Execute Scalp'}
          </Button>
          <Button variant="outline" size="sm" onClick={copyPayload} disabled={!activeSignal}>
            <Copy className="mr-2 h-4 w-4" />
            Copy JSON
          </Button>
        </div>
      </div>

      {scanError && (
        <div className="flex items-center gap-2 rounded-md border border-short/35 bg-short/10 px-3 py-2 text-sm text-short">
          <AlertTriangle className="h-4 w-4" />
          {scanError}
        </div>
      )}

      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card className="border-border/60 bg-card/70">
          <CardHeader className="space-y-3 pb-3">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <CardTitle className="flex items-center gap-2 text-base">
                <BarChart3 className="h-4 w-4 text-primary" />
                M5 Context Chart
                {executionTf === 'M1' && (
                  <Badge variant="outline" className="text-[10px] font-normal">
                    Execution TF: M1
                  </Badge>
                )}
              </CardTitle>
              <div className="flex flex-wrap items-center gap-2">
                {intentSourceBadge && (
                  <Badge variant="outline" className="text-[10px] border-primary/40 text-primary">
                    {intentSourceBadge}
                  </Badge>
                )}
                {autoReviewStatus !== 'idle' && (
                  <Badge variant="outline" className="text-[10px]">
                    Auto Review: {autoReviewStatus}
                  </Badge>
                )}
                {activeWatches.map((watch) => (
                  <Badge
                    key={watch.watch_id || `${watch.symbol}-${watch.status}`}
                    variant="outline"
                    className={`text-[10px] ${watch.status === 'READY_FOR_REVIEW' ? 'border-long/50 text-long' : ''}`}
                  >
                    {watch.status === 'READY_FOR_REVIEW' ? 'Ready for review' : watchLabel(watch)}
                  </Badge>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Select value={activeSymbolKey || EMPTY_SYMBOL_SELECT_VALUE} onValueChange={selectSymbol}>
                  <SelectTrigger className="h-8 w-[190px]">
                    <SelectValue placeholder="No candidate" />
                  </SelectTrigger>
                  <SelectContent>
                    {symbolOptions.length === 0 && (
                      <SelectItem value={EMPTY_SYMBOL_SELECT_VALUE} disabled>
                        No candidate
                      </SelectItem>
                    )}
                    {symbolOptions.map((option) => (
                      <SelectItem key={option.key} value={option.key}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={timeframe}
                  onValueChange={(value) => setTimeframe(value as (typeof TIMEFRAMES)[number])}
                  disabled={!tfDisplayOverride}
                >
                  <SelectTrigger className="h-8 w-[92px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TIMEFRAMES.map((tf) => (
                      <SelectItem key={tf} value={tf}>
                        {tf}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <label className="flex items-center gap-2 text-[11px] text-muted-foreground">
                  <Switch checked={tfDisplayOverride} onCheckedChange={setTfDisplayOverride} />
                  Display override
                </label>
              </div>
            </div>
            <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
              <label className="flex items-center gap-2">
                <Switch checked={showTradeLevels} onCheckedChange={setShowTradeLevels} />
                Entry / SL / TP
              </label>
              <label className="flex items-center gap-2">
                <Switch checked={showProfileLevels} onCheckedChange={setShowProfileLevels} />
                POC / VAH / VAL
              </label>
              <label className="flex items-center gap-2">
                <Switch checked={showLvnLevels} onCheckedChange={setShowLvnLevels} />
                LVN zones
              </label>
            </div>
          </CardHeader>
          <CardContent className="pt-0">
            <div ref={chartCaptureRef} className="relative h-[540px] min-h-[420px] overflow-hidden rounded-md border border-border/50 bg-[#080c10]">
              <div ref={containerRef} className="absolute inset-0" />
              {(chartLoading || chartError || !chartSymbol || candleRows.length === 0) && (
                <div className="pointer-events-none absolute left-3 top-3 max-w-[75%] rounded-md border border-border/60 bg-background/85 px-3 py-2 text-xs text-muted-foreground shadow-lg">
                  {chartLoading
                    ? 'Loading candles...'
                    : chartError || (!chartSymbol ? 'No Engine D candidate selected' : 'No candles loaded')}
                </div>
              )}
              {noLvnLevels && (
                <div className="pointer-events-none absolute bottom-3 left-3 rounded-md border border-warning/35 bg-warning/10 px-3 py-2 text-[11px] text-warning">
                  LVN overlay levels pending backend source-contract patch
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card className="border-border/60 bg-card/70">
          <CardHeader className="px-4 py-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <ShieldCheck className="h-4 w-4 text-primary" />
                Candidate
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 px-4 pb-3 pt-0">
              <FieldRow label="Symbol" value={chartSymbol || '—'} />
              <FieldRow label="Timeframe" value={activeUi.timeframe || executionTf} />
              <FieldRow label="AI grade" value={textValue(activeUi.raw?.ai_grade ?? activeSignal?.ai_grade ?? null, '—')} />
              <FieldRow label="AI score" value={numberValue(activeUi.raw?.ai_score ?? activeSignal?.ai_score ?? null)} />
              <FieldRow label="Engine D" value={textValue(activeUi.engineStatus)} tone={activeUi.executable ? 'ok' : 'muted'} />
              <FieldRow label="Market state" value={marketState} tone={marketState === 'unknown' ? 'muted' : 'ok'} />
              <FieldRow label="Location" value={location} tone={location === 'unknown' ? 'muted' : 'ok'} />
              <FieldRow label="Aggression" value={aggressionStatus} tone={aggressionTone} />
              <FieldRow label="Fidelity" value={sourceSummary} tone={sourceSummaryTone} />
              <FieldRow label="Data venue" value={textValue(activeUi.sourceContract.dataVenue)} />
              <FieldRow label="Execution venue" value={textValue(activeUi.sourceContract.executionVenue)} />
              <FieldRow
                label="Venue mismatch"
                value={venueMismatch === true ? 'mismatch' : venueMismatch === false ? 'aligned' : 'unknown'}
                tone={venueMismatch === true ? 'danger' : venueMismatch === false ? 'ok' : 'muted'}
              />
            </CardContent>
          </Card>

          <Card className="border-border/60 bg-card/70">
            <CardHeader className="px-4 py-3">
              <CardTitle className="text-base">Source Contract</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 px-4 pb-3 pt-0">
              <FieldRow label="Venue" value={textValue(activeUi.sourceContract.venue)} />
              <FieldRow label="Expected" value={textValue(activeUi.sourceContract.expectedVenue, '—')} />
              <FieldRow label="Execution" value={textValue(activeUi.sourceContract.executionVenue, '—')} />
              <FieldRow label="Data" value={textValue(activeUi.sourceContract.dataVenue, '—')} />
              <FieldRow label="Mismatch" value={boolLabel(activeUi.sourceContract.venueMismatch)} tone={activeUi.sourceContract.venueMismatch ? 'danger' : 'muted'} />
              <FieldRow label="Mismatch reason" value={textValue(activeUi.sourceContract.venueMismatchReason, '—')} />
              <SourceRow label="Candle" source={textValue(activeUi.sourceContract.candleSource)} real={activeUi.sourceContract.candleSourceIsReal} proxy={null} />
              <SourceRow label="Volume" source={textValue(activeUi.sourceContract.volumeSource)} real={activeUi.sourceContract.volumeSourceIsReal} proxy={boolField(activeSignal, ['vp_is_proxy'])} />
              <SourceRow label="Flow" source={textValue(activeUi.sourceContract.orderflowSource)} real={activeUi.sourceContract.orderflowSourceIsReal} proxy={aggressionProxy} />
              <SourceRow label="CVD" source={textValue(activeUi.sourceContract.cvdSource)} real={activeUi.sourceContract.cvdSourceIsReal} proxy={boolField(activeSignal, ['cvd_is_proxy'])} />
              <SourceRow label="Absorb" source={textValue(activeUi.sourceContract.absorptionSource)} real={activeUi.sourceContract.absorptionSourceIsReal} proxy={boolField(activeSignal, ['absorption_is_proxy'])} />
              <SourceRow label="VP" source={textValue(activeUi.sourceContract.vpSource)} real={activeUi.sourceContract.vpSourceIsReal} proxy={boolField(activeSignal, ['vp_is_proxy'])} />
              <FieldRow label="Flow pass" value={passLabel(activeUi.sourceContract.strictOrderflowSourcePass, 'unknown')} tone={passTone(activeUi.sourceContract.strictOrderflowSourcePass)} />
              <FieldRow label="Volume pass" value={passLabel(activeUi.sourceContract.strictVolumeSourcePass, 'unknown')} tone={passTone(activeUi.sourceContract.strictVolumeSourcePass)} />
              <FieldRow label="Time pass" value={passLabel(activeUi.sourceContract.strictTimestampAlignmentPass, 'unknown')} tone={passTone(activeUi.sourceContract.strictTimestampAlignmentPass)} />
              <FieldRow label="Venue pass" value={passLabel(activeUi.sourceContract.strictVenuePass, 'unknown')} tone={passTone(activeUi.sourceContract.strictVenuePass)} />
              <FieldRow label="Candle ts" value={textValue(activeUi.sourceContract.latestCandleTs, '—')} />
              <FieldRow label="Flow ts" value={textValue(activeUi.sourceContract.orderflowTs, '—')} />
              <FieldRow label="CVD ts" value={textValue(activeUi.sourceContract.cvdTs, '—')} />
              <FieldRow label="VP ts" value={textValue(activeUi.sourceContract.vpTs, '—')} />
              <FieldRow label="Max skew" value={numberValue(activeUi.sourceContract.maxTimestampSkewSeconds)} />
              <FieldRow label="Time reason" value={textValue(activeUi.sourceContract.timestampAlignmentReason, '—')} />
              <FieldRow
                label="Unavailable"
                value={activeUi.sourceContract.unavailableReasons.length ? activeUi.sourceContract.unavailableReasons.join(', ') : '—'}
                tone={activeUi.sourceContract.unavailableReasons.length ? 'proxy' : 'muted'}
              />
              <FieldRow
                label="Warnings"
                value={activeUi.sourceContract.warnings.length ? activeUi.sourceContract.warnings.join(', ') : '—'}
                tone={activeUi.sourceContract.warnings.length ? 'warn' : 'muted'}
              />
            </CardContent>
          </Card>

          <Card className="border-border/60 bg-card/70">
            <CardHeader className="px-4 py-3">
              <CardTitle className="text-base">Market Location</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 px-4 pb-3 pt-0">
              <FieldRow label="Label" value={textValue(activeUi.marketLocation.locationLabel)} />
              <FieldRow label="Score" value={numberValue(activeUi.marketLocation.locationScore)} />
              <FieldRow label="POC" value={numberValue(activeUi.marketLocation.poc)} />
              <FieldRow label="VAH" value={numberValue(activeUi.marketLocation.vah)} />
              <FieldRow label="VAL" value={numberValue(activeUi.marketLocation.val)} />
              <FieldRow label="Near POC" value={boolLabel(activeUi.marketLocation.nearPOC)} />
              <FieldRow label="Near VAH" value={boolLabel(activeUi.marketLocation.nearVAH)} />
              <FieldRow label="Near VAL" value={boolLabel(activeUi.marketLocation.nearVAL)} />
              <FieldRow label="Near LVN" value={boolLabel(activeUi.marketLocation.nearLVN)} />
              <FieldRow label="Near HVN" value={boolLabel(activeUi.marketLocation.nearHVN)} />
              <FieldRow label="Inside value" value={boolLabel(activeUi.marketLocation.insideValueArea)} />
              <FieldRow label="Above value" value={boolLabel(activeUi.marketLocation.aboveValueArea)} />
              <FieldRow label="Below value" value={boolLabel(activeUi.marketLocation.belowValueArea)} />
              <FieldRow label="LVN levels" value={activeUi.marketLocation.lvnLevels.length > 0 ? `${activeUi.marketLocation.lvnLevels.length}` : '0 / unavailable'} />
              <FieldRow label="HVN levels" value={activeUi.marketLocation.hvnLevels.length > 0 ? `${activeUi.marketLocation.hvnLevels.length}` : '0 / unavailable'} />
              <FieldRow label="Nearest LVN" value={numberValue(activeUi.marketLocation.nearestLVN)} />
              <FieldRow label="Nearest HVN" value={numberValue(activeUi.marketLocation.nearestHVN)} />
              <FieldRow label="Dist POC" value={numberValue(activeUi.marketLocation.distanceToPOC)} />
              <FieldRow label="Dist VAH" value={numberValue(activeUi.marketLocation.distanceToVAH)} />
              <FieldRow label="Dist VAL" value={numberValue(activeUi.marketLocation.distanceToVAL)} />
              <FieldRow label="Dist LVN" value={numberValue(activeUi.marketLocation.distanceToNearestLVN)} />
              <FieldRow label="Dist HVN" value={numberValue(activeUi.marketLocation.distanceToNearestHVN)} />
            </CardContent>
          </Card>

          <Card className="border-border/60 bg-card/70">
            <CardHeader className="px-4 py-3">
              <CardTitle className="text-base">Aggression / Order Flow</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 px-4 pb-3 pt-0">
              <FieldRow label="Source" value={sourceStatusLabel(activeUi.sourceContract.orderflowSourceIsReal, activeUi.sourceContract.orderflowSource)} tone={sourceStatusTone(activeUi.sourceContract.orderflowSourceIsReal, activeUi.sourceContract.orderflowSource)} />
              <FieldRow label="Label" value={textValue(activeUi.aggressionContext.aggressionLabel)} />
              <FieldRow label="Score" value={numberValue(activeUi.aggressionContext.aggressionScore)} />
              <FieldRow label="Buy" value={numberValue(activeUi.aggressionContext.buyAggression)} />
              <FieldRow label="Sell" value={numberValue(activeUi.aggressionContext.sellAggression)} />
              <FieldRow label="Delta" value={numberValue(activeUi.aggressionContext.delta)} />
              <FieldRow label="CVD" value={numberValue(activeUi.aggressionContext.cvd)} />
              <FieldRow label="CVD slope" value={numberValue(activeUi.aggressionContext.cvdSlope)} />
              <FieldRow label="Absorption" value={boolLabel(activeUi.aggressionContext.absorptionDetected)} />
              <FieldRow label="Abs side" value={textValue(activeUi.aggressionContext.absorptionSide, '—')} />
              <FieldRow label="Sweep" value={boolLabel(activeUi.aggressionContext.sweepDetected)} />
              <FieldRow label="Sweep side" value={textValue(activeUi.aggressionContext.sweepSide, '—')} />
              <FieldRow label="Imbalance" value={boolLabel(activeUi.aggressionContext.imbalanceDetected)} />
              <FieldRow label="Imb side" value={textValue(activeUi.aggressionContext.imbalanceSide, '—')} />
            </CardContent>
          </Card>

          <Card className="border-border/60 bg-card/70">
            <CardHeader className="px-4 py-3">
              <CardTitle className="text-base">Setup / Risk</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 px-4 pb-3 pt-0">
              <FieldRow label="Setup" value={textValue(activeUi.scalpSetup.setupType, '—')} />
              <FieldRow label="Direction" value={textValue(activeUi.scalpSetup.direction, '—')} />
              <FieldRow label="Entry" value={numberValue(activeUi.scalpSetup.entry)} />
              <FieldRow label="SL" value={numberValue(activeUi.scalpSetup.stopLoss)} tone={activeUi.scalpSetup.stopLoss == null ? 'muted' : 'danger'} />
              <FieldRow label="TP1" value={numberValue(activeUi.scalpSetup.tp1)} tone={activeUi.scalpSetup.tp1 == null ? 'muted' : 'ok'} />
              <FieldRow label="TP2" value={numberValue(activeUi.scalpSetup.tp2)} tone={activeUi.scalpSetup.tp2 == null ? 'muted' : 'ok'} />
              <FieldRow label="RR1" value={numberValue(activeUi.scalpSetup.rr1)} />
              <FieldRow label="RR2" value={numberValue(activeUi.scalpSetup.rr2)} />
              <FieldRow label="Risk dist" value={numberValue(activeUi.scalpSetup.riskDistance)} />
              <FieldRow label="Reward 1" value={numberValue(activeUi.scalpSetup.rewardDistance1)} />
              <FieldRow label="Reward 2" value={numberValue(activeUi.scalpSetup.rewardDistance2)} />
              <FieldRow label="Fabio pass" value={passLabel(activeUi.scalpSetup.strictFabioPass, 'unknown')} tone={passTone(activeUi.scalpSetup.strictFabioPass)} />
              <FieldRow label="Invalidation" value={textValue(activeUi.scalpSetup.invalidationReason, '—')} />
              <FieldRow label="Skipped" value={boolLabel(activeUi.scalpSetup.skipped)} tone={activeUi.scalpSetup.skipped ? 'warn' : 'muted'} />
              <FieldRow label="Skip reason" value={textValue(activeUi.scalpSetup.skippedReason, '—')} />
              <FieldRow label="Source pass" value={passLabel(strictOrderflowSource, 'unknown')} tone={passTone(strictOrderflowSource)} />
              <FieldRow label="Gate reasons" value={activeUi.scalpSetup.strictGateReasons.length ? activeUi.scalpSetup.strictGateReasons.join(', ') : '—'} tone={activeUi.scalpSetup.strictGateReasons.length ? 'danger' : 'muted'} />
              <FieldRow label="State" value={passLabel(typeof strictPillars.market_state === 'boolean' ? strictPillars.market_state : null, 'unknown')} tone={passTone(typeof strictPillars.market_state === 'boolean' ? strictPillars.market_state : null)} />
              <FieldRow label="Location" value={passLabel(typeof strictPillars.location === 'boolean' ? strictPillars.location : null, 'unknown')} tone={passTone(typeof strictPillars.location === 'boolean' ? strictPillars.location : null)} />
              <FieldRow label="Aggression" value={passLabel(typeof strictPillars.aggression === 'boolean' ? strictPillars.aggression : null, 'unknown')} tone={passTone(typeof strictPillars.aggression === 'boolean' ? strictPillars.aggression : null)} />
            </CardContent>
          </Card>
        </div>
      </div>

      {(aiCaptureError || scalpAiReviewResponse || scalpAiReviewPreview) && (
        <div className="space-y-3">
          {aiCaptureError && (
            <div className="rounded-md border border-warning/35 bg-warning/10 px-3 py-2 text-xs text-warning">
              {aiCaptureError}
            </div>
          )}
          {scalpAiReviewResponse && <ScalpAIReviewCard response={scalpAiReviewResponse} />}
          {canFlagWatch && (
            <div className="flex flex-wrap items-center gap-2 pt-2">
              <Button
                size="sm"
                variant="outline"
                className="h-8 text-xs"
                disabled={flagLoading}
                onClick={() => void flagWatchSetup()}
              >
                Flag / Watch Setup
              </Button>
              {flagStatus && <span className="text-[10px] text-muted-foreground">{flagStatus}</span>}
            </div>
          )}
          {scalpAiReviewPreview && (
            <Card className="border-border/60 bg-card/70">
              <CardHeader className="px-4 py-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Sparkles className="h-4 w-4 text-primary" />
                  AI review debug
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 px-4 pb-4 pt-0">
                {scalpAiReviewDebugPayload && (
                  <details className="rounded-md border border-border/50 bg-background/40 px-3 py-2 text-xs" open={!scalpAiReviewResponse}>
                    <summary className="cursor-pointer font-medium text-muted-foreground">Snapshot payload</summary>
                    <pre className="mt-2 max-h-[260px] overflow-auto whitespace-pre-wrap break-words text-[11px] text-foreground/80">
                      {JSON.stringify(scalpAiReviewDebugPayload, null, 2)}
                    </pre>
                  </details>
                )}
                {scalpAiReviewResponse && (
                  <details className="rounded-md border border-border/50 bg-background/40 px-3 py-2 text-xs">
                    <summary className="cursor-pointer font-medium text-muted-foreground">Copy JSON</summary>
                    <pre className="mt-2 max-h-[260px] overflow-auto whitespace-pre-wrap break-words text-[11px] text-foreground/80">
                      {JSON.stringify(scalpAiReviewResponse, null, 2)}
                    </pre>
                  </details>
                )}
              </CardContent>
            </Card>
          )}
        </div>
      )}

      <Card className="border-border/60 bg-card/70">
        <CardHeader className="px-4 py-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <CheckCircle2 className="h-4 w-4 text-primary" />
            Candidate Context
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 px-4 pb-4 pt-0 xl:grid-cols-[minmax(0,1fr)_420px]">
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {warnings.map((item, index) => (
                <WarningPill key={`${item.text}-${index}`} tone={item.tone}>
                  {item.text}
                </WarningPill>
              ))}
            </div>
            <div className="grid gap-2 text-xs text-muted-foreground md:grid-cols-3">
              <div>
                <span className="uppercase tracking-wide">Reason</span>
                <div className="mt-1 text-foreground/80">{fieldText(activeSignal, ['trigger_desc', 'zone_desc', 'strict_fabio_reason'], '—')}</div>
              </div>
              <div>
                <span className="uppercase tracking-wide">Profile anchor</span>
                <div className="mt-1 text-foreground/80">
                  {fieldText(activeSignal, ['profile_anchor_mode'], 'unknown')}
                  {activeSignal?.profile_anchor_bars ? ` / ${activeSignal.profile_anchor_bars} bars` : ''}
                </div>
              </div>
              <div>
                <span className="uppercase tracking-wide">Timestamp alignment</span>
                <div className="mt-1 text-foreground/80">
                  {textValue(activeUi.sourceContract.timestampAlignmentReason, '—')}
                </div>
              </div>
            </div>
            <div className="grid gap-2 text-xs text-muted-foreground md:grid-cols-2">
              <div>
                <span className="uppercase tracking-wide">Source unavailable</span>
                <div className="mt-1 text-foreground/80">
                  {activeUi.sourceContract.unavailableReasons.length ? activeUi.sourceContract.unavailableReasons.join(', ') : '—'}
                </div>
              </div>
              <div>
                <span className="uppercase tracking-wide">Strategy gate reasons</span>
                <div className="mt-1 text-foreground/80">
                  {activeUi.scalpSetup.strictGateReasons.length ? activeUi.scalpSetup.strictGateReasons.join(', ') : '—'}
                </div>
              </div>
            </div>
            <div className="grid gap-2 text-xs text-muted-foreground md:grid-cols-2">
              {chartProviderRows.map(([label, value]) => (
                <div key={label}>
                  <span className="uppercase tracking-wide">{label}</span>
                  <div className="mt-1 text-foreground/80">{value}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-md border border-border/50 bg-background/40">
            <div className="border-b border-border/50 px-3 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Skipped / Blocked
            </div>
            <ScrollArea className="h-[150px]">
              {skippedRows.length > 0 ? (
                <div className="divide-y divide-border/40">
                  {skippedRows.map((row, index) => (
                    <div key={`${row.symbol || index}`} className="px-3 py-2 text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-foreground/85">{row.symbol || 'unknown'}</span>
                        <Badge variant="outline" className={cn('text-[10px]', badgeClass(row.scalpSetup.skipped ? 'warn' : 'muted'))}>
                          {row.scalpSetup.skipped ? 'skipped' : 'blocked'}
                        </Badge>
                      </div>
                      <div className="mt-1 truncate text-muted-foreground">
                        {row.scalpSetup.skippedReason || row.scalpSetup.strictGateReasons.join(', ') || 'no reason in payload'}
                      </div>
                      {row.sourceContract.unavailableReasons.length > 0 && (
                        <div className="mt-1 truncate text-muted-foreground/75">
                          source: {row.sourceContract.unavailableReasons.join(', ')}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                  No skipped rows in current payload.
                </div>
              )}
            </ScrollArea>
          </div>
        </CardContent>
      </Card>

      <AlertDialog open={confirmExecOpen} onOpenChange={setConfirmExecOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm Scalp Execution · Engine D</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm">
                <p>
                  Execute scalp setup? Backend will refresh/revalidate before order.
                </p>
                <p>
                  Execute <b>{activeSignal?.direction}</b>{' '}
                  {activeSignal?.display || activeSignal?.pair || chartSymbol} · grade{' '}
                  <span className="font-mono">{activeSignal?.ai_grade || '—'}</span>
                </p>
                <p className="text-xs text-muted-foreground">
                  Entry {fmtNum(activeSignal?.entry ?? activeSignal?.price, 6)} · SL {fmtNum(activeSignal?.sl, 6)} · TP1{' '}
                  {fmtNum(activeSignal?.tp1, 6)}
                </p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={executingScalp}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void onConfirmScalpExecute()}
              disabled={executingScalp || Boolean(executeBlockReason)}
            >
              {executingScalp ? 'Executing…' : 'Confirm Execute Scalp'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
