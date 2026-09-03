import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CandlestickSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type UTCTimestamp,
} from 'lightweight-charts';
import { BookOpenText, Feather, RefreshCw, ScrollText, Stamp } from 'lucide-react';

import { useStore } from '@/hooks/useStore';
import apiClient from '@/lib/apiClient';
import { snapshotFromFableSignals } from '@/lib/engineScanCompile';
import {
  FABLE_ACT_TITLES,
  FABLE_DECISION_ORDER,
  fableActQuality,
  fableCanAttest,
  fableCanSeal,
  fableDecisionClass,
  fableMakeIdempotencyKey,
  fablePreferredMode,
  fablePrice,
  fableRelativeAge,
  fableReturnLabel,
  fableScanProgress,
  fableShortTime,
  fableStoryGlyphs,
  fableTierClass,
  type FableAccounts,
  type FableActName,
  type FableChart,
  type FableChronicle,
  type FableDecision,
  type FableExecutionMode,
  type FableExecutionRecord,
  type FableHealth,
  type FablePositions,
  type FablePreview,
  type FableScanState,
  type FableSignal,
} from '@/lib/fableEngine';
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
import '@/styles/fable.css';

const ASSET_TYPES: readonly [string, string][] = [
  ['forex', 'FX'],
  ['crypto', 'Crypto'],
  ['commodity', 'Metals & energy'],
  ['index', 'Indices'],
  ['stock', 'Stocks'],
  ['etf', 'ETFs'],
];

const ACT_ORDER: FableActName[] = ['draw', 'raid', 'shift', 'return', 'chorus'];

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error || 'Unknown error');
}

function pct(value: number | null | undefined, digits = 0): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

function ringColor(decision: FableDecision | string | undefined): string {
  if (decision === 'EXECUTE') return 'var(--fbl-verdigris)';
  if (decision === 'STAGE') return 'var(--fbl-ember)';
  if (decision === 'VOID') return 'var(--fbl-rose)';
  return 'rgba(239, 230, 211, 0.45)';
}

function evidenceValue(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(Math.abs(value) < 1 ? 3 : 5);
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return `${value.length} items`;
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    if ('source' in record && 'price' in record) return `${String(record.source)} @ ${evidenceValue(record.price)}`;
    if ('low' in record && 'high' in record) return `${evidenceValue(record.low)} – ${evidenceValue(record.high)}`;
    return Object.keys(record).length ? `${Object.keys(record).length} fields` : '—';
  }
  return String(value);
}

function CoherenceRing({ value, decision, large, caption }: { value: number; decision?: string; large?: boolean; caption?: string }) {
  const rounded = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div
      className={large ? 'fbl-ring fbl-ring--lg' : 'fbl-ring'}
      style={{ ['--v' as string]: rounded, ['--ring-color' as string]: ringColor(decision) }}
      title={`Coherence ${value.toFixed(1)} / 100`}
    >
      <span>{rounded}</span>
      {caption ? <small>{caption}</small> : null}
    </div>
  );
}

function Glyphs({ signal }: { signal: FableSignal }) {
  return (
    <span className="fbl-glyphs" aria-label="five acts">
      {fableStoryGlyphs(signal).map((glyph) => (
        <span
          key={glyph.act}
          className={`fbl-glyph${glyph.state === 'awaiting' || glyph.state === 'pending' ? ' fbl-glyph--awaiting' : ''}${glyph.state === 'absent' || glyph.state === 'through' ? ' fbl-glyph--absent' : ''}`}
          style={{ ['--q' as string]: glyph.quality ?? 0 }}
          title={`${FABLE_ACT_TITLES[glyph.act].title}: ${glyph.quality == null ? 'untold' : pct(glyph.quality)}`}
        >
          <i />
        </span>
      ))}
    </span>
  );
}

function SignalCard({ signal, selected, onSelect }: { signal: FableSignal; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      className={`fbl-card${selected ? ' fbl-card--selected' : ''}${signal.decision === 'VOID' ? ' fbl-card--void' : ''}`}
      onClick={onSelect}
      data-signal-id={signal.signalId}
    >
      <CoherenceRing value={signal.coherence} decision={signal.decision} caption={signal.tier} />
      <div style={{ minWidth: 0 }}>
        <div className="fbl-card-pair">
          <strong>{signal.pair}</strong>
          {signal.direction !== 'NONE' ? (
            <span className={`fbl-dir ${signal.direction === 'LONG' ? 'fbl-long' : 'fbl-short'}`}>{signal.direction}</span>
          ) : null}
          <Glyphs signal={signal} />
        </div>
        <div className="fbl-card-meta">
          <span className={`fbl-decision ${fableDecisionClass(signal.decision)}`}>{signal.decision}</span>
          <span className={`fbl-tier ${fableTierClass(signal.tier)}`}>{signal.tier}</span>
          <span>{signal.assetType}</span>
          <span>{signal.venue}</span>
          {signal.rr != null ? <span>RR {signal.rr.toFixed(2)}</span> : null}
        </div>
        <div className="fbl-card-line">{signal.narrative}</div>
      </div>
    </button>
  );
}

function ActCard({ act, signal, selected, onSelect }: { act: FableActName; signal: FableSignal; selected: boolean; onSelect: () => void }) {
  const detail = signal.acts.find((item) => item.name === act);
  const quality = detail?.quality ?? null;
  const weak = quality != null && quality < 0.45;
  const meta = FABLE_ACT_TITLES[act];
  return (
    <div className={`fbl-act${selected ? ' fbl-act--selected' : ''}${weak ? ' fbl-act--weak' : ''}`}>
      <button type="button" onClick={onSelect} title={meta.blurb}>
        <div className="fbl-act-numeral">ACT {meta.numeral}</div>
        <div className="fbl-act-title">{meta.title}</div>
        <div className="fbl-act-q fbl-mono">{quality == null ? '—' : pct(quality)}</div>
        <div className="fbl-act-bar"><i style={{ ['--q' as string]: quality ?? 0 }} /></div>
        <div className="fbl-act-state">{detail?.state ?? 'untold'} · w {detail?.weight ?? '—'}</div>
      </button>
    </div>
  );
}

function StoryChart({ signal }: { signal: FableSignal }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candlesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const overlayRef = useRef<ISeriesApi<'Line'>[]>([]);
  const [chart, setChart] = useState<FableChart | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setChart(null);
    setError(null);
    apiClient
      .get<FableChart>(`/api/fable/signals/${signal.signalId}/chart?bars=240`)
      .then((payload) => { if (!cancelled) setChart(payload); })
      .catch((err) => { if (!cancelled) setError(errorText(err)); });
    return () => { cancelled = true; };
  }, [signal.signalId]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    const instance = createChart(container, {
      width: container.clientWidth,
      height: 340,
      layout: {
        background: { color: 'transparent' },
        textColor: 'rgba(239, 230, 211, 0.55)',
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 10,
      },
      grid: {
        vertLines: { color: 'rgba(239, 230, 211, 0.045)' },
        horzLines: { color: 'rgba(239, 230, 211, 0.045)' },
      },
      rightPriceScale: { borderColor: 'rgba(239, 230, 211, 0.16)' },
      timeScale: { borderColor: 'rgba(239, 230, 211, 0.16)', timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 },
    });
    const candles = instance.addSeries(CandlestickSeries, {
      upColor: 'hsl(152, 64%, 46%)',
      downColor: 'hsl(358, 78%, 61%)',
      borderUpColor: 'hsl(152, 64%, 46%)',
      borderDownColor: 'hsl(358, 78%, 61%)',
      wickUpColor: 'hsl(152, 64%, 46%)',
      wickDownColor: 'hsl(358, 78%, 61%)',
      priceLineVisible: false,
    });
    chartRef.current = instance;
    candlesRef.current = candles;
    const observer = new ResizeObserver(() => {
      const width = container.clientWidth;
      if (width > 0) instance.applyOptions({ width });
    });
    observer.observe(container);
    return () => {
      observer.disconnect();
      instance.remove();
      chartRef.current = null;
      candlesRef.current = null;
      overlayRef.current = [];
    };
  }, []);

  useEffect(() => {
    const instance = chartRef.current;
    const candles = candlesRef.current;
    if (!instance || !candles || !chart) return;
    for (const series of overlayRef.current) {
      try { instance.removeSeries(series); } catch { /* already gone */ }
    }
    overlayRef.current = [];
    const rows = chart.candles
      .filter((row) => Number.isFinite(row.open) && Number.isFinite(row.close))
      .map((row) => ({ time: row.time as UTCTimestamp, open: row.open, high: row.high, low: row.low, close: row.close }));
    candles.setData(rows);
    if (!rows.length) return;
    const firstTime = rows[0].time;
    const lastTime = rows[rows.length - 1].time;
    const lastPrice = rows[rows.length - 1].close;
    const lines: { price: number | null | undefined; color: string; title: string; style: LineStyle; width: 1 | 2 }[] = [
      { price: chart.levels.entry, color: 'rgba(239, 230, 211, 0.9)', title: 'entry', style: LineStyle.Solid, width: 1 },
      { price: chart.levels.stop, color: 'hsl(358, 78%, 61%)', title: 'stop', style: LineStyle.Solid, width: 2 },
      { price: chart.levels.target, color: 'hsl(152, 64%, 46%)', title: 'target', style: LineStyle.Solid, width: 2 },
      { price: chart.levels.target2, color: 'rgba(94, 200, 176, 0.7)', title: 'draw', style: LineStyle.Dashed, width: 1 },
    ];
    for (const line of lines) {
      if (line.price == null || !Number.isFinite(line.price)) continue;
      candles.createPriceLine({ price: line.price, color: line.color, lineWidth: line.width, lineStyle: line.style, axisLabelVisible: true, title: line.title });
    }
    const span = Math.max(Math.abs(lastPrice) * 0.02, 1e-9);
    for (const pool of chart.annotations.pools || []) {
      if (Math.abs(pool.price - lastPrice) > span) continue;
      candles.createPriceLine({
        price: pool.price,
        color: pool.side === 'buyside' ? 'rgba(224, 164, 88, 0.55)' : 'rgba(224, 164, 88, 0.55)',
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: false,
        title: pool.source,
      });
    }
    const array = chart.annotations.array;
    if (array) {
      const start = Math.max(firstTime, array.time as UTCTimestamp) as UTCTimestamp;
      for (const level of [array.low, array.high]) {
        const series = instance.addSeries(LineSeries, {
          color: 'rgba(224, 164, 88, 0.85)',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        });
        series.setData([{ time: start, value: level }, { time: lastTime, value: level }]);
        overlayRef.current.push(series);
      }
    }
    const shift = chart.annotations.shift;
    if (shift) {
      const series = instance.addSeries(LineSeries, {
        color: 'rgba(94, 200, 176, 0.75)',
        lineWidth: 1,
        lineStyle: LineStyle.LargeDashed,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });
      const start = Math.max(firstTime, shift.brokenTime as UTCTimestamp) as UTCTimestamp;
      const end = Math.max(start, shift.time as UTCTimestamp) as UTCTimestamp;
      series.setData(start < end ? [{ time: start, value: shift.brokenLevel }, { time: end, value: shift.brokenLevel }] : [{ time: start, value: shift.brokenLevel }]);
      overlayRef.current.push(series);
    }
    const markers: SeriesMarker<UTCTimestamp>[] = [];
    const raid = chart.annotations.raid;
    const bullish = chart.direction === 'LONG';
    if (raid && raid.reclaimTime >= firstTime) {
      markers.push({
        time: raid.reclaimTime as UTCTimestamp,
        position: bullish ? 'belowBar' : 'aboveBar',
        color: 'rgb(224, 164, 88)',
        shape: bullish ? 'arrowUp' : 'arrowDown',
        text: `raid ${raid.pool.source}`,
      });
    }
    if (shift && shift.time >= firstTime) {
      markers.push({
        time: shift.time as UTCTimestamp,
        position: bullish ? 'belowBar' : 'aboveBar',
        color: 'rgb(94, 200, 176)',
        shape: 'circle',
        text: 'shift',
      });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    createSeriesMarkers(candles, markers);
    instance.timeScale().fitContent();
  }, [chart]);

  return (
    <div className="fbl-chart" data-fable-chart={signal.signalId}>
      <div className="fbl-chart-legend">
        <span><i style={{ background: 'rgba(239,230,211,0.9)' }} />entry</span>
        <span><i style={{ background: 'hsl(358,78%,61%)' }} />stop</span>
        <span><i style={{ background: 'hsl(152,64%,46%)' }} />target</span>
        <span><i style={{ background: 'rgba(224,164,88,0.85)' }} />imbalance</span>
        <span><i style={{ background: 'rgba(94,200,176,0.75)' }} />broken level</span>
        <span><i style={{ background: 'rgba(224,164,88,0.55)' }} />pools</span>
      </div>
      <div className="fbl-chart-note">{chart ? `${chart.timeframe} · ${chart.candles.length} closed bars` : error ? `chart: ${error}` : 'loading…'}</div>
      <div ref={containerRef} className="fbl-chart-surface" />
    </div>
  );
}

export default function FableEnginePanel() {
  const { showToast, setEngineScanSnapshot, markEngineCompilePending } = useStore();

  const [health, setHealth] = useState<FableHealth | null>(null);
  const [accounts, setAccounts] = useState<FableAccounts | null>(null);
  const [scan, setScan] = useState<FableScanState | null>(null);
  const [signals, setSignals] = useState<FableSignal[]>([]);
  const [executions, setExecutions] = useState<FableExecutionRecord[]>([]);
  const [positions, setPositions] = useState<FablePositions | null>(null);
  const [assetFilter, setAssetFilter] = useState<Set<string>>(new Set());
  const [decisionFilter, setDecisionFilter] = useState<Set<FableDecision>>(new Set(['EXECUTE', 'STAGE']));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedAct, setSelectedAct] = useState<FableActName>('raid');
  const [preview, setPreview] = useState<FablePreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [confirmSignal, setConfirmSignal] = useState<FableSignal | null>(null);
  const [mode, setMode] = useState<FableExecutionMode>('paper');
  const [modeTouched, setModeTouched] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const [chronicleSymbol, setChronicleSymbol] = useState('');
  const [chronicle, setChronicle] = useState<FableChronicle | null>(null);
  const [chronicling, setChronicling] = useState(false);
  const scanningRef = useRef(false);

  const capabilities = accounts?.brokerCapabilities ?? health?.brokerCapabilities ?? null;

  const loadHealth = useCallback(async () => {
    try {
      const payload = await apiClient.get<FableHealth>('/api/fable/health');
      setHealth(payload);
    } catch (err) {
      setLastError(errorText(err));
    }
  }, []);

  const loadAccounts = useCallback(async () => {
    try {
      setAccounts(await apiClient.get<FableAccounts>('/api/fable/accounts'));
    } catch (err) {
      setLastError(errorText(err));
    }
  }, []);

  const loadSignals = useCallback(async () => {
    try {
      const payload = await apiClient.get<{ signals: FableSignal[] }>('/api/fable/signals?decisions=EXECUTE,STAGE,OBSERVE,VOID&limit=500');
      setSignals(payload.signals || []);
    } catch (err) {
      setLastError(errorText(err));
    }
  }, []);

  const loadExecutions = useCallback(async () => {
    try {
      const payload = await apiClient.get<{ executions: FableExecutionRecord[] }>('/api/fable/executions?limit=60');
      setExecutions(payload.executions || []);
    } catch (err) {
      setLastError(errorText(err));
    }
  }, []);

  const loadPositions = useCallback(async () => {
    try {
      setPositions(await apiClient.get<FablePositions>('/api/fable/positions'));
    } catch (err) {
      setLastError(errorText(err));
    }
  }, []);

  const loadScan = useCallback(async () => {
    try {
      const payload = await apiClient.get<FableScanState>('/api/fable/scan/current');
      setScan(payload);
      return payload;
    } catch {
      setScan(null);
      return null;
    }
  }, []);

  useEffect(() => {
    void loadHealth();
    void loadAccounts();
    void loadScan();
    void loadSignals();
    void loadExecutions();
    void loadPositions();
  }, [loadHealth, loadAccounts, loadScan, loadSignals, loadExecutions, loadPositions]);

  useEffect(() => {
    if (!capabilities || modeTouched) return;
    setMode(fablePreferredMode(capabilities));
  }, [capabilities, modeTouched]);

  useEffect(() => {
    if (scan?.status !== 'RUNNING') return undefined;
    scanningRef.current = true;
    const timer = window.setInterval(async () => {
      const latest = await loadScan();
      if (latest && latest.status !== 'RUNNING') {
        scanningRef.current = false;
        window.clearInterval(timer);
        const payload = await apiClient.get<{ signals: FableSignal[] }>('/api/fable/signals?decisions=EXECUTE,STAGE,OBSERVE,VOID&limit=500').catch(() => null);
        if (payload) {
          setSignals(payload.signals || []);
          setEngineScanSnapshot(snapshotFromFableSignals(payload.signals || [], latest.completedAt));
        }
        void loadHealth();
        showToast(
          latest.status === 'COMPLETED'
            ? `FABLE read ${latest.processedPairs} markets · ${latest.executeCount} to execute, ${latest.stageCount} staged`
            : `FABLE scan ${latest.status.toLowerCase()}`,
          latest.status === 'COMPLETED' ? 'success' : 'error',
        );
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [scan?.status, loadScan, loadHealth, setEngineScanSnapshot, showToast]);

  const startScan = useCallback(async () => {
    setLastError(null);
    try {
      const state = await apiClient.post<FableScanState>('/api/fable/scan', {
        assetTypes: Array.from(assetFilter),
      });
      setScan(state);
      markEngineCompilePending('fable', true);
    } catch (err) {
      const message = errorText(err);
      setLastError(message);
      showToast(`FABLE scan: ${message}`, 'error');
    }
  }, [assetFilter, markEngineCompilePending, showToast]);

  const filtered = useMemo(() => {
    return signals.filter((signal) => {
      if (decisionFilter.size && !decisionFilter.has(signal.decision)) return false;
      if (assetFilter.size && !assetFilter.has(signal.assetType)) return false;
      return true;
    });
  }, [signals, decisionFilter, assetFilter]);

  const selected = useMemo(() => filtered.find((signal) => signal.signalId === selectedId) ?? filtered[0] ?? null, [filtered, selectedId]);

  useEffect(() => {
    setPreview(null);
  }, [selected?.signalId]);

  const attest = useCallback(async (signal: FableSignal) => {
    setPreviewing(true);
    setLastError(null);
    try {
      const result = await apiClient.post<FablePreview>(`/api/fable/signals/${signal.signalId}/preview`, {});
      setPreview(result);
    } catch (err) {
      const message = errorText(err);
      setPreview({ executable: false, error: message, gates: [] });
      setLastError(message);
    } finally {
      setPreviewing(false);
    }
  }, []);

  const seal = useCallback(async () => {
    if (!confirmSignal) return;
    setExecuting(true);
    setLastError(null);
    try {
      const result = await apiClient.post<FableExecutionRecord & { success?: boolean; error?: string }>(
        `/api/fable/signals/${confirmSignal.signalId}/execute`,
        { mode, idempotencyKey: fableMakeIdempotencyKey(confirmSignal.signalId), confirmLive: mode === 'live' },
      );
      const ticket = result.result?.ticket;
      showToast(`FABLE sealed ${confirmSignal.pair} ${confirmSignal.direction} in ${mode}${ticket ? ` · ticket ${ticket}` : ''}`, 'success');
      setConfirmSignal(null);
      void loadExecutions();
      void loadPositions();
    } catch (err) {
      const message = errorText(err);
      setLastError(message);
      showToast(`FABLE seal rejected: ${message}`, 'error');
      setConfirmSignal(null);
      void loadExecutions();
    } finally {
      setExecuting(false);
    }
  }, [confirmSignal, mode, showToast, loadExecutions, loadPositions]);

  const runChronicle = useCallback(async () => {
    const symbol = chronicleSymbol.trim() || selected?.pair || '';
    if (!symbol) return;
    setChronicling(true);
    setLastError(null);
    try {
      setChronicle(await apiClient.post<FableChronicle>('/api/fable/chronicle', { symbol }));
    } catch (err) {
      const message = errorText(err);
      setLastError(message);
      showToast(`FABLE chronicle: ${message}`, 'error');
    } finally {
      setChronicling(false);
    }
  }, [chronicleSymbol, selected?.pair, showToast]);

  const toggleAsset = (asset: string) => {
    setAssetFilter((current) => {
      const next = new Set(current);
      if (next.has(asset)) next.delete(asset);
      else next.add(asset);
      return next;
    });
  };
  const toggleDecision = (decision: FableDecision) => {
    setDecisionFilter((current) => {
      const next = new Set(current);
      if (next.has(decision)) next.delete(decision);
      else next.add(decision);
      return next;
    });
  };

  const modeEnabled = Boolean(capabilities?.modes?.[mode]?.enabled);
  const canSeal = Boolean(selected && fableCanSeal(selected) && preview?.executable && modeEnabled);
  const running = scan?.status === 'RUNNING';
  const session = health?.session;
  const mt5 = accounts?.venues?.mt5;
  const bybit = accounts?.venues?.bybit;
  const selectedActDetail = selected?.acts.find((act) => act.name === selectedAct) ?? null;
  const tally = useMemo(() => {
    const counts: Record<FableDecision, number> = { EXECUTE: 0, STAGE: 0, OBSERVE: 0, VOID: 0 };
    for (const signal of signals) counts[signal.decision] = (counts[signal.decision] ?? 0) + 1;
    return counts;
  }, [signals]);

  return (
    <div className="fbl-root" data-panel="fable-engine">
      <header className="fbl-frontispiece">
        <div>
          <div className="fbl-wordmark">
            <h1>F<em>able</em></h1>
            <span className="fbl-motto">every market tells a story; liquidity writes it</span>
          </div>
          <p className="fbl-subtitle">
            Narrative Liquidity Engine. Each pair is read as five acts — the higher-timeframe draw, the raid of a
            resting pool, the displacement that shifts structure, the return into the imbalance it left, and a chorus of
            quantitative voices. A weighted geometric mean fuses the acts into coherence; one weak act drags the whole
            story down instead of being averaged away.
          </p>
        </div>
        <div>
          <div className="fbl-status-row">
            <span className={`fbl-stamp ${health?.enabled ? 'fbl-stamp--verdigris' : 'fbl-stamp--rose'}`}><i className="fbl-dot" />{health ? (health.enabled ? 'engine awake' : 'engine disabled') : 'engine…'}</span>
            <span className="fbl-stamp">{health?.contractVersion ?? 'fable.v1'}</span>
            <span className="fbl-stamp fbl-stamp--ember">research {health?.researchStatus ?? '—'}</span>
            <span className="fbl-stamp">executor {capabilities?.globalExecutorMode ?? '—'} · default {capabilities?.defaultMode ?? '—'}</span>
            <span className={`fbl-stamp ${mt5?.connected ? 'fbl-stamp--verdigris' : ''}`} title={mt5?.error || `${mt5?.server ?? ''} ${mt5?.login ?? ''}`}>
              MT5 {mt5?.connected ? `${mt5.environment ?? 'demo'} ${mt5.equity != null ? Number(mt5.equity).toFixed(0) : ''} ${mt5.currency ?? ''}` : 'offline'}
            </span>
            <span className={`fbl-stamp ${bybit?.connected ? 'fbl-stamp--verdigris' : ''}`} title={bybit?.error || ''}>
              Bybit {bybit?.connected ? `${bybit.environment ?? 'demo'} ${bybit.equity != null ? Number(bybit.equity).toFixed(0) : ''} ${bybit.currency ?? ''}` : 'offline'}
            </span>
          </div>
          <div className="fbl-clock" style={{ justifyContent: 'flex-end', marginTop: '0.6rem' }}>
            <span>NY <strong>{session?.nyClock ?? '—'}</strong></span>
            <span>{session?.displayTimezone?.split('/').pop() ?? 'local'} <strong>{session?.displayClock ?? '—'}</strong></span>
            <span>window <strong className="fbl-ember">{session?.window ?? 'off-window'}</strong> {session ? pct(session.quality) : ''}</span>
          </div>
        </div>
      </header>

      {health?.windows?.length ? (
        <div className="fbl-ribbon" aria-label="institutional windows">
          {health.windows.map((window) => (
            <div key={window.name} className={`fbl-ribbon-cell${window.active ? ' fbl-ribbon-cell--active' : ''}`} style={{ ['--q' as string]: window.quality }}>
              <div className="fbl-ribbon-name">{window.name.replace(/_/g, ' ')}</div>
              <div className="fbl-ribbon-time">{window.startDisplay}–{window.endDisplay} <span className="fbl-faint">({window.startNy}–{window.endNy} NY)</span></div>
            </div>
          ))}
        </div>
      ) : null}

      <div className="fbl-toolbar">
        {ASSET_TYPES.map(([key, label]) => (
          <button key={key} type="button" className={`fbl-chip${assetFilter.has(key) ? ' fbl-chip--on' : ''}`} onClick={() => toggleAsset(key)} disabled={running}>
            {label}
          </button>
        ))}
        <span className="fbl-spacer" />
        {FABLE_DECISION_ORDER.map((decision) => (
          <button key={decision} type="button" className={`fbl-chip${decisionFilter.has(decision) ? ' fbl-chip--on' : ''}`} onClick={() => toggleDecision(decision)}>
            {decision} {tally[decision] ? `· ${tally[decision]}` : ''}
          </button>
        ))}
        <button type="button" className="fbl-btn fbl-btn--ghost fbl-btn--sm" onClick={() => { void loadSignals(); void loadHealth(); void loadAccounts(); void loadPositions(); }} title="Refresh">
          <RefreshCw size={14} />
        </button>
        <button type="button" className="fbl-btn fbl-btn--ember" onClick={() => void startScan()} disabled={running || health?.enabled === false} data-testid="fable-scan">
          <Feather size={15} className={running ? 'fbl-spin' : undefined} /> {running ? 'Reading the market…' : 'Read the market'}
        </button>
      </div>
      {scan ? (
        <>
          {running ? <div className="fbl-progress"><i style={{ width: `${fableScanProgress(scan)}%` }} /></div> : null}
          <div className="fbl-tally">
            <span>scan <b>{scan.status.toLowerCase()}</b></span>
            <span><b>{scan.processedPairs}</b>/{scan.totalPairs} markets</span>
            <span className="fbl-verdigris">execute <b>{scan.executeCount}</b></span>
            <span className="fbl-ember">stage <b>{scan.stageCount}</b></span>
            <span>observe <b>{scan.observeCount}</b></span>
            <span>void <b>{scan.voidCount}</b></span>
            {scan.errorCount ? <span className="fbl-short">errors <b>{scan.errorCount}</b></span> : null}
            <span>{scan.completedAt ? `finished ${fableRelativeAge(scan.completedAt)}` : `started ${fableRelativeAge(scan.startedAt)}`}</span>
          </div>
        </>
      ) : null}
      {lastError ? <div className="fbl-alert">{lastError}</div> : null}

      <div className="fbl-body">
        <aside>
          <div className="fbl-codex-head">
            <h2 className="fbl-h2">Codex<small>{filtered.length} of {signals.length}</small></h2>
          </div>
          <div className="fbl-codex" data-testid="fable-codex">
            {filtered.length === 0 ? (
              <div className="fbl-empty">{signals.length ? 'No stories match the current filters.' : 'No stories yet — read the market to begin.'}</div>
            ) : (
              filtered.map((signal) => (
                <SignalCard key={signal.signalId} signal={signal} selected={selected?.signalId === signal.signalId} onSelect={() => setSelectedId(signal.signalId)} />
              ))
            )}
          </div>
        </aside>

        <section className="fbl-manuscript" data-testid="fable-manuscript">
          {!selected ? (
            <div className="fbl-manuscript-empty">
              <div>
                <BookOpenText size={28} style={{ opacity: 0.5 }} />
                <div style={{ marginTop: '0.5rem' }}>Select a story from the codex to open its manuscript.</div>
              </div>
            </div>
          ) : (
            <>
              <div className="fbl-ms-head">
                <div style={{ minWidth: 0 }}>
                  <div className="fbl-ms-title">
                    <h3>{selected.pair}</h3>
                    {selected.direction !== 'NONE' ? <span className={`fbl-dir ${selected.direction === 'LONG' ? 'fbl-long' : 'fbl-short'}`}>{selected.direction}</span> : null}
                    <span className={`fbl-decision ${fableDecisionClass(selected.decision)}`}>{selected.decision}</span>
                    <span className={`fbl-tier ${fableTierClass(selected.tier)}`}>{selected.tier}</span>
                  </div>
                  <div className="fbl-ms-sub">
                    <span>{selected.assetType} · {selected.venue}</span>
                    <span>{selected.decisionReason}</span>
                    <span>{fableReturnLabel(selected.returnState)}</span>
                    <span>bar {fableShortTime(selected.barClosedAt)}</span>
                    <span>read {fableRelativeAge(selected.generatedAt)}</span>
                    <span>D1 draw · H4 bias · H1 pools · M15 narrative</span>
                  </div>
                </div>
                <CoherenceRing value={selected.coherence} decision={selected.decision} large caption={`potential ${Math.round(selected.coherencePotential)}`} />
              </div>

              <p className="fbl-narrative">{selected.narrative}</p>

              <div className="fbl-acts">
                {ACT_ORDER.map((act) => (
                  <ActCard key={act} act={act} signal={selected} selected={selectedAct === act} onSelect={() => setSelectedAct(act)} />
                ))}
              </div>
              {selectedActDetail ? (
                <div className="fbl-evidence">
                  <div className="fbl-evidence-title">Act {FABLE_ACT_TITLES[selectedAct].numeral} · {FABLE_ACT_TITLES[selectedAct].title} — {FABLE_ACT_TITLES[selectedAct].blurb}</div>
                  {selectedAct === 'chorus' && Array.isArray(selectedActDetail.evidence.voices)
                    ? (selectedActDetail.evidence.voices as { name: string; quality: number | null; weight: number; raw: unknown }[]).map((voice) => (
                      <span key={voice.name}>{voice.name}: <b>{voice.quality == null ? 'silent' : pct(voice.quality)}</b> <span className="fbl-faint">w{voice.weight} · {evidenceValue(voice.raw)}</span></span>
                    ))
                    : Object.entries(selectedActDetail.evidence)
                      .filter(([key]) => !['pool', 'imbalances', 'session', 'eventRisk'].includes(key))
                      .slice(0, 24)
                      .map(([key, value]) => (
                        <span key={key}>{key}: <b>{evidenceValue(value)}</b></span>
                      ))}
                </div>
              ) : null}

              <div className="fbl-levels">
                <div className="fbl-level"><div className="fbl-level-k">Entry</div><div className="fbl-level-v">{fablePrice(preview?.executableEntry ?? selected.entry, selected.assetType)}</div><div className="fbl-level-n">{preview?.quote ? `${preview.quote.source} · ${preview.quote.ageSec.toFixed(1)}s` : 'scan close'}</div></div>
                <div className="fbl-level"><div className="fbl-level-k">Stop</div><div className="fbl-level-v fbl-short">{fablePrice(selected.stop, selected.assetType)}</div><div className="fbl-level-n">{selected.stopAtr != null ? `${selected.stopAtr.toFixed(2)} ATR beyond the raid` : '—'}</div></div>
                <div className="fbl-level"><div className="fbl-level-k">Target</div><div className="fbl-level-v fbl-long">{fablePrice(preview?.liveTarget ?? selected.target, selected.assetType)}</div><div className="fbl-level-n">RR {(preview?.liveRr ?? selected.rr)?.toFixed(2) ?? '—'} · {preview?.liveTargetSource ?? selected.targetSource ?? '—'}</div></div>
                <div className="fbl-level"><div className="fbl-level-k">Draw</div><div className="fbl-level-v">{fablePrice(selected.target2, selected.assetType)}</div><div className="fbl-level-n">{selected.rr2 != null ? `RR ${selected.rr2.toFixed(2)} · ${selected.target2Source ?? ''}` : 'no external draw beyond target'}</div></div>
                <div className="fbl-level"><div className="fbl-level-k">ATR M15</div><div className="fbl-level-v">{fablePrice(selected.atr, selected.assetType)}</div><div className="fbl-level-n">{selected.atrPct != null ? `${(selected.atrPct * 100).toFixed(3)}% of price` : '—'}</div></div>
              </div>

              <StoryChart signal={selected} />

              <div className="fbl-seal" data-testid="fable-seal">
                <div className="fbl-seal-row">
                  <span className="fbl-h2" style={{ fontSize: '0.95rem' }}>Seal the story</span>
                  <span className="fbl-spacer" />
                  <select className="fbl-select" value={mode} onChange={(event) => { setMode(event.target.value as FableExecutionMode); setModeTouched(true); }} aria-label="execution mode">
                    {(['paper', 'demo', 'live'] as FableExecutionMode[]).map((option) => (
                      <option key={option} value={option} disabled={!capabilities?.modes?.[option]?.enabled}>
                        {option}{capabilities?.modes?.[option]?.enabled ? '' : ' (locked)'}
                      </option>
                    ))}
                  </select>
                  <button type="button" className="fbl-btn fbl-btn--sm" onClick={() => void attest(selected)} disabled={previewing || !fableCanAttest(selected.decision)}>
                    <ScrollText size={14} className={previewing ? 'fbl-spin' : undefined} /> Attest live quote
                  </button>
                  <button type="button" className="fbl-btn fbl-btn--ember fbl-btn--sm" onClick={() => setConfirmSignal(selected)} disabled={!canSeal} data-testid="fable-seal-button">
                    <Stamp size={14} /> Seal {mode}
                  </button>
                </div>
                {preview ? (
                  <div className={`fbl-attest ${preview.executable ? 'fbl-attest--ok' : 'fbl-attest--bad'}`}>
                    {preview.executable ? 'Quote attested — every gate passed.' : `Rejected: ${preview.error || 'unknown gate'}${preview.detail ? ` · ${preview.detail}` : ''}`}
                    {preview.quote ? (
                      <div className="fbl-dim">bid {fablePrice(preview.quote.bid, selected.assetType)} · ask {fablePrice(preview.quote.ask, selected.assetType)} · spread {preview.quote.spreadBps.toFixed(2)} bps · age {preview.quote.ageSec.toFixed(1)}s</div>
                    ) : null}
                    <div className="fbl-gates">
                      {preview.gates.map((gate) => (
                        <span key={gate.name} className={`fbl-gate ${gate.passed ? 'fbl-gate--pass' : 'fbl-gate--fail'}`} title={gate.reason || ''}>{gate.name}</span>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="fbl-note">
                    {selected.decision === 'EXECUTE'
                      ? 'Attest the live quote first: spread, age, drift against the scan close and live geometry are re-checked before the seal button unlocks.'
                      : selected.decision === 'STAGE'
                        ? 'Staged stories can be attested for a quote read-out but cannot be sealed until price returns into the imbalance on a fresh scan.'
                        : 'Only EXECUTE stories can be sealed. Observe and void stories are read-only.'}
                  </div>
                )}
                <div className="fbl-gates">
                  {selected.gates.map((gate) => (
                    <span key={gate.name} className={`fbl-gate ${gate.passed ? 'fbl-gate--pass' : 'fbl-gate--fail'}`} title={gate.reason || ''}>{gate.name}</span>
                  ))}
                </div>
                <div className="fbl-note">
                  Data: {Object.entries(selected.dataFreshness || {}).map(([tf, diag]) => `${tf} ${diag.status.toLowerCase()}${diag.ageBuckets != null ? ` (${diag.ageBuckets.toFixed(1)} buckets)` : ''}`).join(' · ')}
                  {selected.chorusContext && Object.keys(selected.chorusContext).length ? ` · chorus ${Object.entries(selected.chorusContext).filter(([, value]) => value != null).map(([key, value]) => `${key} ${evidenceValue(value)}`).join(', ')}` : ''}
                </div>
              </div>
            </>
          )}
        </section>
      </div>

      <div className="fbl-lower">
        <section className="fbl-section">
          <div className="fbl-section-head">
            <h2 className="fbl-h2">Ledger<small>{executions.length} seals</small></h2>
            <button type="button" className="fbl-btn fbl-btn--ghost fbl-btn--sm" onClick={() => void loadExecutions()}><RefreshCw size={13} /></button>
          </div>
          {executions.length === 0 ? (
            <div className="fbl-empty">No seals yet.</div>
          ) : (
            <div className="fbl-table-wrap">
              <table className="fbl-table">
                <thead><tr><th>Pair</th><th>Mode</th><th>Status</th><th>Ticket</th><th>Entry</th><th>Detail</th><th>When</th></tr></thead>
                <tbody>
                  {executions.map((row) => (
                    <tr key={row.execution_id}>
                      <td>{row.request?.signal?.pair ?? row.signal_id.slice(0, 12)} <span className={row.request?.signal?.direction === 'LONG' ? 'fbl-long' : 'fbl-short'}>{row.request?.signal?.direction ?? ''}</span></td>
                      <td>{row.mode}</td>
                      <td className={row.status === 'SUCCESS' ? 'fbl-verdigris' : row.status === 'PENDING' ? 'fbl-ember' : 'fbl-short'}>{row.status}</td>
                      <td>{row.result?.ticket != null ? String(row.result.ticket) : '—'}</td>
                      <td>{typeof row.result?.entryPrice === 'number' ? fablePrice(row.result.entryPrice, row.request?.signal?.assetType) : '—'}</td>
                      <td title={String(row.result?.detail ?? '')}>{row.result?.error ?? (row.result?.mode === 'paper' ? 'paper fill' : 'broker fill')}</td>
                      <td>{fableRelativeAge(row.completed_at || row.requested_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="fbl-section">
          <div className="fbl-section-head">
            <h2 className="fbl-h2">Open stories<small>{positions?.count ?? 0} positions</small></h2>
            <button type="button" className="fbl-btn fbl-btn--ghost fbl-btn--sm" onClick={() => void loadPositions()}><RefreshCw size={13} /></button>
          </div>
          {!positions || positions.positions.length === 0 ? (
            <div className="fbl-empty">
              No open broker positions claimed by FABLE.
              {positions ? ` MT5 ${positions.venues.mt5?.connected ? 'connected' : 'offline'} · Bybit ${positions.venues.bybit?.connected ? 'connected' : 'offline'}.` : ''}
            </div>
          ) : (
            <div className="fbl-table-wrap">
              <table className="fbl-table">
                <thead><tr><th>Pair</th><th>Side</th><th>Size</th><th>Entry</th><th>Now</th><th>SL</th><th>TP</th><th>P&L</th><th>Tier</th></tr></thead>
                <tbody>
                  {positions.positions.map((position) => (
                    <tr key={`${position.venue}-${position.ticket}`}>
                      <td>{position.pair} <span className="fbl-faint">{position.venue}</span></td>
                      <td className={position.direction === 'LONG' ? 'fbl-long' : 'fbl-short'}>{position.direction}</td>
                      <td>{position.volume}</td>
                      <td>{fablePrice(position.entry)}</td>
                      <td>{fablePrice(position.currentPrice ?? null)}</td>
                      <td>{fablePrice(position.sl ?? null)}</td>
                      <td>{fablePrice(position.tp ?? null)}</td>
                      <td className={(position.profit ?? 0) >= 0 ? 'fbl-long' : 'fbl-short'}>{position.profit != null ? Number(position.profit).toFixed(2) : '—'}</td>
                      <td>{position.tier ?? '—'} {position.coherence != null ? `· ${Math.round(position.coherence)}` : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="fbl-section" data-testid="fable-chronicle">
          <div className="fbl-section-head">
            <h2 className="fbl-h2">Chronicle<small>causal replay</small></h2>
          </div>
          <div className="fbl-inline-form">
            <input className="fbl-input" placeholder={selected?.pair ? `symbol · ${selected.pair}` : 'symbol e.g. EUR/USD'} value={chronicleSymbol} onChange={(event) => setChronicleSymbol(event.target.value)} />
            <button type="button" className="fbl-btn fbl-btn--sm" onClick={() => void runChronicle()} disabled={chronicling || (!chronicleSymbol.trim() && !selected)}>
              <BookOpenText size={14} className={chronicling ? 'fbl-spin' : undefined} /> Replay the story
            </button>
          </div>
          {chronicle ? (
            <>
              <div className="fbl-kpis" style={{ marginTop: '0.7rem' }}>
                <div className="fbl-kpi"><div className="fbl-kpi-k">Evidence</div><div className="fbl-kpi-v" style={{ fontSize: '0.8rem' }}>{chronicle.evidenceStatus.replace(/_/g, ' ').toLowerCase()}</div></div>
                <div className="fbl-kpi"><div className="fbl-kpi-k">Trades</div><div className="fbl-kpi-v">{chronicle.summary.trades}</div></div>
                <div className="fbl-kpi"><div className="fbl-kpi-k">Win rate</div><div className="fbl-kpi-v">{chronicle.summary.winRate != null ? pct(chronicle.summary.winRate) : '—'}</div></div>
                <div className="fbl-kpi"><div className="fbl-kpi-k">Expectancy</div><div className={`fbl-kpi-v ${(chronicle.summary.expectancyR ?? 0) >= 0 ? 'fbl-long' : 'fbl-short'}`}>{chronicle.summary.expectancyR != null ? `${chronicle.summary.expectancyR.toFixed(2)}R` : '—'}</div></div>
                <div className="fbl-kpi"><div className="fbl-kpi-k">Total</div><div className={`fbl-kpi-v ${(chronicle.summary.totalR ?? 0) >= 0 ? 'fbl-long' : 'fbl-short'}`}>{chronicle.summary.totalR != null ? `${chronicle.summary.totalR.toFixed(2)}R` : '—'}</div></div>
                <div className="fbl-kpi"><div className="fbl-kpi-k">Bars</div><div className="fbl-kpi-v">{chronicle.barsEvaluated ?? chronicle.bars}</div></div>
              </div>
              <div className="fbl-note">{chronicle.note} Decisions: {Object.entries(chronicle.decisions).map(([key, value]) => `${key.toLowerCase()} ${value}`).join(' · ')}.</div>
              {chronicle.chapters.length ? (
                <div className="fbl-table-wrap" style={{ marginTop: '0.6rem', maxHeight: 260, overflowY: 'auto' }}>
                  <table className="fbl-table">
                    <thead><tr><th>When</th><th>Side</th><th>Tier</th><th>Coh</th><th>Entry</th><th>Stop</th><th>Target</th><th>Outcome</th><th>R</th><th>Bars</th></tr></thead>
                    <tbody>
                      {chronicle.chapters.map((chapter) => (
                        <tr key={`${chapter.signalId}-${chapter.decisionAt}`}>
                          <td>{fableShortTime(chapter.decisionAt)}</td>
                          <td className={chapter.direction === 'LONG' ? 'fbl-long' : 'fbl-short'}>{chapter.direction}</td>
                          <td>{chapter.tier}</td>
                          <td>{Math.round(chapter.coherence)}</td>
                          <td>{fablePrice(chapter.entry, chronicle.assetType)}</td>
                          <td>{fablePrice(chapter.stop, chronicle.assetType)}</td>
                          <td>{fablePrice(chapter.target, chronicle.assetType)}</td>
                          <td>{chapter.outcome}</td>
                          <td className={chapter.rMultiple >= 0 ? 'fbl-long' : 'fbl-short'}>{chapter.rMultiple.toFixed(2)}</td>
                          <td>{chapter.barsHeld}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <div className="fbl-empty">No EXECUTE chapters inside the replay window.</div>}
            </>
          ) : (
            <div className="fbl-note">Replays the same scorer over closed-bar prefixes with next-bar-open fills. It checks the implementation; it does not prove an edge.</div>
          )}
        </section>
      </div>

      <AlertDialog open={!!confirmSignal} onOpenChange={(open) => { if (!open && !executing) setConfirmSignal(null); }}>
        <AlertDialogContent className="fbl-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Seal {confirmSignal?.pair} {confirmSignal?.direction} in {mode}</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div>
                <div className="fbl-mono" style={{ fontSize: '0.74rem', lineHeight: 1.5 }}>
                  {mode === 'paper'
                    ? 'A paper fill is recorded inside FABLE; no broker order is sent.'
                    : mode === 'demo'
                      ? 'A market order is sent to the attested demo account through guardian and risk_check. The stop is immutable; the target may fall back to the external draw if live RR no longer clears the minimum.'
                      : 'Real-money execution. Requires the server confirmation token and a validated research status.'}
                </div>
                <div className="fbl-dialog-levels">
                  <div><small>entry</small>{fablePrice(preview?.executableEntry ?? confirmSignal?.entry, confirmSignal?.assetType)}</div>
                  <div><small>stop</small>{fablePrice(confirmSignal?.stop, confirmSignal?.assetType)}</div>
                  <div><small>target</small>{fablePrice(preview?.liveTarget ?? confirmSignal?.target, confirmSignal?.assetType)}</div>
                </div>
                <div className="fbl-mono" style={{ fontSize: '0.7rem' }}>
                  coherence {confirmSignal ? Math.round(confirmSignal.coherence) : '—'} · {confirmSignal?.tier} · risk {capabilities?.riskFraction != null ? `${(capabilities.riskFraction * 100).toFixed(2)}% of equity` : 'per config'}
                </div>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={executing}>Not yet</AlertDialogCancel>
            <AlertDialogAction onClick={(event) => { event.preventDefault(); void seal(); }} disabled={executing}>
              {executing ? 'Sealing…' : `Seal in ${mode}`}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
