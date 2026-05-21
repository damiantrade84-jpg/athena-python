import { useEffect, useMemo, useRef, useState } from 'react';
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPaneApi,
  type ISeriesApi,
  type CandlestickData,
  type LineData,
  type UTCTimestamp,
  type Time,
} from 'lightweight-charts';
import { BarChart3, Layers, SlidersHorizontal } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
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
import type { EngineASignal } from '@/types/athena';

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

// Indicator colors — chosen to be distinct and high-contrast on the dark chart background.
const INDICATOR_COLORS = {
  ema20: 'hsl(200, 95%, 55%)',
  ema50: 'hsl(45, 95%, 58%)',
  ema200: 'hsl(280, 80%, 65%)',
  dema200: 'hsl(15, 90%, 60%)',
  rsi14: 'hsl(45, 95%, 58%)',
  atr14: 'hsl(200, 95%, 55%)',
} as const;

const PRESET_OPTIONS = [
  { value: 'custom', label: 'Custom' },
  { value: 'all', label: 'All indicators' },
] as const;
type PresetValue = (typeof PRESET_OPTIONS)[number]['value'];

interface CandleApiRow {
  t?: string | number;
  o?: number | string;
  h?: number | string;
  l?: number | string;
  c?: number | string;
}
interface CandleApiResponse {
  candles?: CandleApiRow[];
  error?: string;
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

function EngineASidePanel({ signal }: { signal: EngineASignal | null }) {
  const factorScores = asRecord(signal?.factorScores);
  const diagnostics = asRecord(signal?.factorDiagnostics);
  const trendCoherence = asRecord(diagnostics.trendCoherence);
  const feedStatus = asRecord(diagnostics.feedStatus);
  const engineAAssetDiagnostics = asRecord(diagnostics.engineAAssetDiagnostics);
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

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
      <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}

// --- Main panel ------------------------------------------------------

export default function TVChartPanel() {
  const { scanCacheA } = useStore();
  const [pair, setPair] = useState('EURUSD');
  const [timeframe, setTimeframe] = useState('60');
  const [ema20, setEma20] = useState(true);
  const [ema50, setEma50] = useState(true);
  const [ema200, setEma200] = useState(false);
  const [dema200, setDema200] = useState(false);
  const [atr14, setAtr14] = useState(false);
  const [rsi14, setRsi14] = useState(false);

  const [candles, setCandles] = useState<CandleApiRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);

  const candidateRows = useMemo(
    () => (Array.isArray(scanCacheA) ? scanCacheA.filter((row): row is EngineASignal => Boolean(row && typeof row === 'object')) : []),
    [scanCacheA],
  );
  const defaultCandidate = useMemo(() => pickEngineACandidate(candidateRows), [candidateRows]);
  const chartCandidate = useMemo(() => findEngineACandidateForSymbol(candidateRows, pair), [candidateRows, pair]);

  // Derived preset label: "all" only when every indicator is on, otherwise "custom".
  const activePreset: PresetValue = ema20 && ema50 && ema200 && dema200 && rsi14 && atr14 ? 'all' : 'custom';

  const applyPreset = (value: PresetValue) => {
    if (value === 'all') {
      setEma20(true);
      setEma50(true);
      setEma200(true);
      setDema200(true);
      setAtr14(true);
      setRsi14(true);
    }
    // 'custom' is passive — manual switch flips revert the label naturally.
  };

  const applyEngineAReviewLayout = () => {
    const candidate = chartCandidate || defaultCandidate;
    const candidateSymbol = displaySymbol(candidate);
    if (candidateSymbol) setPair(candidateSymbol);
    setTimeframe(reviewTimeframeFor(candidate));
    setEma20(true);
    setEma50(true);
    setEma200(true);
    setDema200(false);
    setAtr14(true);
    setRsi14(true);
  };

  // --- Fetch candles whenever pair/timeframe changes ---------------
  useEffect(() => {
    const backendTf = TF_BACKEND_MAP[timeframe];
    if (!pair || !backendTf) {
      setCandles(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setChartError(null);
    const url = `/api/candles?symbol=${encodeURIComponent(pair)}&tf=${encodeURIComponent(backendTf)}&limit=300`;
    apiClient
      .getJson(url)
      .then((res) => {
        if (cancelled) return;
        const data = res as CandleApiResponse;
        if (data?.error) {
          setChartError(data.error);
          setCandles(null);
          return;
        }
        const list = Array.isArray(data?.candles) ? data.candles : [];
        setCandles(list);
        if (list.length === 0) setChartError(`No candle data for ${pair} ${backendTf}`);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setChartError(err instanceof Error ? err.message : 'Failed to load candles');
        setCandles(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [pair, timeframe]);

  // --- Chart lifecycle ---------------------------------------------
  // Pane structure depends on which sub-pane studies are on; recreate the chart
  // whenever rsi14 / atr14 flip so panes appear and disappear cleanly.
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const ema20SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ema50SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ema200SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const dema200SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const rsiSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const atrSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  const subPaneStudyCount = (rsi14 ? 1 : 0) + (atr14 ? 1 : 0);
  const chartMinHeightPx = 480 + subPaneStudyCount * 180;
  const cardMinHeightPx = chartMinHeightPx + 60;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight || chartMinHeightPx,
      layout: {
        background: { color: 'transparent' },
        textColor: 'rgba(245, 240, 232, 0.65)',
        fontFamily: "'IBM Plex Mono', monospace",
      },
      grid: {
        vertLines: { color: 'rgba(212, 160, 23, 0.06)' },
        horzLines: { color: 'rgba(212, 160, 23, 0.06)' },
      },
      rightPriceScale: { borderColor: 'rgba(212, 160, 23, 0.18)' },
      timeScale: {
        borderColor: 'rgba(212, 160, 23, 0.18)',
        timeVisible: true,
        secondsVisible: false,
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
      priceLineVisible: false,
    }, 0);
    candleSeriesRef.current = candleSeries;

    const overlayLineOpts = { lineWidth: 2 as const, lastValueVisible: true, priceLineVisible: false };
    ema20SeriesRef.current = chart.addSeries(LineSeries, { ...overlayLineOpts, color: INDICATOR_COLORS.ema20 }, 0);
    ema50SeriesRef.current = chart.addSeries(LineSeries, { ...overlayLineOpts, color: INDICATOR_COLORS.ema50 }, 0);
    ema200SeriesRef.current = chart.addSeries(LineSeries, { ...overlayLineOpts, color: INDICATOR_COLORS.ema200 }, 0);
    dema200SeriesRef.current = chart.addSeries(LineSeries, { ...overlayLineOpts, color: INDICATOR_COLORS.dema200 }, 0);

    // Sub-panes — created only if their study is on, so an unused pane never sits empty.
    if (rsi14) {
      const pane = chart.addPane();
      pane.setStretchFactor(1);
      const paneIdx = pane.paneIndex();
      const series = chart.addSeries(LineSeries, {
        color: INDICATOR_COLORS.rsi14,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
      }, paneIdx);
      series.createPriceLine({ price: 70, color: 'rgba(245,240,232,0.25)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '70' });
      series.createPriceLine({ price: 30, color: 'rgba(245,240,232,0.25)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '30' });
      rsiSeriesRef.current = series;
    }
    if (atr14) {
      const pane = chart.addPane();
      pane.setStretchFactor(1);
      const paneIdx = pane.paneIndex();
      atrSeriesRef.current = chart.addSeries(LineSeries, {
        color: INDICATOR_COLORS.atr14,
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
      ema20SeriesRef.current = null;
      ema50SeriesRef.current = null;
      ema200SeriesRef.current = null;
      dema200SeriesRef.current = null;
      rsiSeriesRef.current = null;
      atrSeriesRef.current = null;
    };
    // chartMinHeightPx only seeds the first sizing call; the ResizeObserver takes over after.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rsi14, atr14]);

  // --- Push data into the chart ------------------------------------
  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleSeriesRef.current;
    if (!chart || !candleSeries) return;

    if (!candles || candles.length === 0) {
      candleSeries.setData([]);
      ema20SeriesRef.current?.setData([]);
      ema50SeriesRef.current?.setData([]);
      ema200SeriesRef.current?.setData([]);
      dema200SeriesRef.current?.setData([]);
      rsiSeriesRef.current?.setData([]);
      atrSeriesRef.current?.setData([]);
      return;
    }

    const rows: CandlestickData[] = [];
    const times: Time[] = [];
    const highs: number[] = [];
    const lows: number[] = [];
    const closes: number[] = [];
    for (const c of candles) {
      const t = toTimestamp(c.t);
      const o = toNum(c.o, NaN);
      const h = toNum(c.h, NaN);
      const l = toNum(c.l, NaN);
      const cl = toNum(c.c, NaN);
      if (t == null) continue;
      if (![o, h, l, cl].every(Number.isFinite)) continue;
      rows.push({ time: t, open: o, high: h, low: l, close: cl });
      times.push(t);
      highs.push(h);
      lows.push(l);
      closes.push(cl);
    }
    candleSeries.setData(rows);

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

    pushLine(ema20SeriesRef.current, ema20, ema(closes, 20));
    pushLine(ema50SeriesRef.current, ema50, ema(closes, 50));
    pushLine(ema200SeriesRef.current, ema200, ema(closes, 200));
    pushLine(dema200SeriesRef.current, dema200, dema(closes, 200));
    pushLine(rsiSeriesRef.current, rsi14, rsi(closes, 14));
    pushLine(atrSeriesRef.current, atr14, atr(highs, lows, closes, 14));

    chart.timeScale().fitContent();
  }, [candles, ema20, ema50, ema200, dema200, rsi14, atr14]);

  return (
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
                  onClick={() => setTimeframe(tf)}
                >
                  {tf}
                </Button>
              ))}
            </div>
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
            <Button size="sm" variant="secondary" className="h-8 gap-2 text-xs" onClick={applyEngineAReviewLayout}>
              <SlidersHorizontal className="h-3.5 w-3.5" />
              Engine A Review Layout
            </Button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3 pt-2">
          <IndicatorSwitch label="EMA20" checked={ema20} onCheckedChange={setEma20} />
          <IndicatorSwitch label="EMA50" checked={ema50} onCheckedChange={setEma50} />
          <IndicatorSwitch label="EMA200" checked={ema200} onCheckedChange={setEma200} />
          <IndicatorSwitch label="DEMA200" checked={dema200} onCheckedChange={setDema200} />
          <IndicatorSwitch label="ATR14" checked={atr14} onCheckedChange={setAtr14} />
          <IndicatorSwitch label="RSI14" checked={rsi14} onCheckedChange={setRsi14} />
        </div>
        <div className="flex flex-wrap items-center gap-3 pt-2">
          {ema20 && <LegendDot color={INDICATOR_COLORS.ema20} label="EMA20" />}
          {ema50 && <LegendDot color={INDICATOR_COLORS.ema50} label="EMA50" />}
          {ema200 && <LegendDot color={INDICATOR_COLORS.ema200} label="EMA200" />}
          {dema200 && <LegendDot color={INDICATOR_COLORS.dema200} label="DEMA200" />}
          {rsi14 && <LegendDot color={INDICATOR_COLORS.rsi14} label="RSI14 (pane)" />}
          {atr14 && <LegendDot color={INDICATOR_COLORS.atr14} label="ATR14 (pane)" />}
        </div>
      </CardHeader>
      <CardContent className="h-[calc(100%-160px)]" style={{ minHeight: `${cardMinHeightPx}px` }}>
        <div className="grid h-full gap-3 xl:grid-cols-[minmax(0,1fr)_320px]">
          <div
            className="relative overflow-hidden rounded-md border bg-background"
            style={{ minHeight: `${chartMinHeightPx}px` }}
          >
            <div ref={containerRef} className="absolute inset-0" />
            {loading && (
              <div className="absolute inset-0 flex items-center justify-center bg-card/40 text-[11px] text-muted-foreground backdrop-blur-sm">
                Loading candles…
              </div>
            )}
            {chartError && !loading && (
              <div className="absolute inset-x-0 top-0 bg-destructive/10 px-3 py-1 text-[11px] text-destructive">
                {chartError}
              </div>
            )}
          </div>
          <EngineASidePanel signal={chartCandidate} />
        </div>
      </CardContent>
    </Card>
  );
}
