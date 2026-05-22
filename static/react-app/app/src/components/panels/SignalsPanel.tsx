import { useCallback, useMemo, useState } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { useLivePrices } from '@/hooks/useLivePrices';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ErrorBanner, RefreshButton } from '@/components/shared';
import {
  Search, Zap, Filter, Layers, Activity, Eye, Clock, Sun, Moon, FileText, Bot,
  BarChart2, Shield, Gauge, Sparkles,
} from 'lucide-react';
import { fmtNum, toNum, cn } from '@/lib/utils';
import { fmtAtrMeta, fmtLiveQuoteMeta, fmtPrice } from '@/lib/athenaFormat';
import { fetchVisionCandlePayload } from '@/lib/visionReview';
import apiClient from '@/lib/apiClient';
import { buildQuickExecutePayload } from '@/lib/manualExecuteHelpers';
import {
  EngineASignalCard,
  EngineBChecklistCard,
  VisionReviewCard,
  H4ChartCard,
} from '@/components/athena';
import AITradingAgentPanel from '@/components/ai/AITradingAgentPanel';
import type {
  EngineASignal,
  ScanResponse,
  NakedScanResponse,
  ChartAnalysisResponse,
  AiTextReviewResponse,
} from '@/types/athena';

type EngineSource = 'A' | 'B';

type ExecuteResponse = {
  success?: boolean;
  ticket?: string;
  error?: string;
  approval?: { approved: boolean; reason: string };
};

/** Stable row key for a unified signal (pair + direction). Used for engine dedupe. */
function rowKey(signal: EngineASignal): string {
  const pair = String(signal.pair || signal.display || signal.symbol || '').toUpperCase().trim();
  const dir = String(signal.direction || '').toUpperCase().trim();
  return `${pair}::${dir}`;
}

/** Group key: backend scoreGroup (e.g. forex_majors) or asset type fallback. */
function signalGroupKey(s: EngineASignal): string {
  const g = (s.scoreGroup || '').trim();
  if (g) return g.toLowerCase();
  const t = (s.type || '').trim().toLowerCase();
  return t || 'other';
}

function formatGroupLabel(key: string): string {
  if (!key || key === 'other') return 'Other';
  return key
    .split('_')
    .map((w) => (w.length ? w[0].toUpperCase() + w.slice(1) : ''))
    .join(' ');
}

/** Sort comparator. Watchlist tier always falls to the bottom of its group. */
function compareUnified(a: UnifiedRow, b: UnifiedRow, sortBy: string): number {
  const aWatch = isWatchlist(a.signal);
  const bWatch = isWatchlist(b.signal);
  if (aWatch !== bWatch) return aWatch ? 1 : -1;
  if (sortBy === 'conviction') return toNum(b.signal.conviction) - toNum(a.signal.conviction);
  if (sortBy === 'rr') return toNum(b.signal.rr ?? b.signal.rr1) - toNum(a.signal.rr ?? a.signal.rr1);
  if (sortBy === 'time') {
    return new Date(b.signal.timestamp || 0).getTime() - new Date(a.signal.timestamp || 0).getTime();
  }
  return toNum(b.signal.confluenceScore ?? b.signal.score) - toNum(a.signal.confluenceScore ?? a.signal.score);
}

function isWatchlist(s: EngineASignal): boolean {
  const tier = String(s.signalTier || s.scan_tier || s.signalClass || '').toLowerCase();
  return tier.includes('watch') || tier === 'skip' || tier === 'blocked';
}

/** Labels for Engine B scanFunnel keys returned by /api/scan-naked. */
const ENGINE_B_FUNNEL_LABELS: Record<string, string> = {
  passed: 'Passed',
  insufficient_candles: 'No data (candles)',
  bybit_atr_unavailable: 'Bybit ATR missing',
  invalid_atr: 'Invalid ATR',
  volatility_gate: 'Volatility gate',
  pair_exception: 'Exception',
  no_clear_structure: 'No CLEAR structure',
  gate_fail_struct: 'Gate: structure',
  gate_fail_loc: 'Gate: location',
  gate_fail_trigger: 'Gate: trigger',
  gate_fail_rr: 'Gate: RR',
  gate_fail_other: 'Gate: other',
  unknown_reject: 'Unknown',
  total: 'Total',
};

function formatEngineBScanFunnel(funnel: Record<string, number> | undefined): string | null {
  if (!funnel || typeof funnel !== 'object') return null;
  const entries = Object.entries(funnel)
    .filter(([k, v]) => k !== 'total' && k !== 'passed' && typeof v === 'number' && v > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);
  if (!entries.length) return null;
  return entries.map(([k, v]) => `${ENGINE_B_FUNNEL_LABELS[k] ?? k} ${v}`).join(' · ');
}

interface UnifiedRow {
  /** Stable id (pair::direction) for selection + React key */
  id: string;
  /** Primary signal payload used for display + execution. Engine A preferred when both exist. */
  signal: EngineASignal;
  /** Which engines flagged this setup. Display-only — never blended into scoring. */
  engines: Set<'A' | 'B'>;
  /** Optional secondary payload (Engine B) when both engines flagged this row */
  engineBSignal?: EngineASignal;
}

function unifyScanResults(
  scanA: EngineASignal[] | null,
  scanB: EngineASignal[] | null,
  lastScan: ScanResponse | null,
): UnifiedRow[] {
  const map = new Map<string, UnifiedRow>();

  // Engine A: combine trade list + watchlist from the scan/lastScan cache.
  const sourceA: EngineASignal[] = scanA != null
    ? scanA
    : Array.isArray(lastScan?.signals) ? (lastScan!.signals as EngineASignal[]) : [];
  for (const raw of sourceA) {
    if (!raw) continue;
    const sig: EngineASignal = {
      ...raw,
      signalClass: raw.signalClass || (raw.trade === false ? 'WATCHLIST' : 'TRADE'),
      signalTier: raw.signalTier || (raw.trade === false ? 'watchlist' : 'trade'),
    };
    const k = rowKey(sig);
    if (!k.includes('::')) continue;
    const existing = map.get(k);
    if (existing) {
      existing.engines.add('A');
    } else {
      map.set(k, { id: k, signal: sig, engines: new Set(['A']) });
    }
  }

  // Engine B: each row already carries naked_data. Attach as secondary
  // when an A row exists; otherwise insert as Engine B–only watchlist.
  if (Array.isArray(scanB)) {
    for (const raw of scanB) {
      if (!raw) continue;
      const sig: EngineASignal = { ...raw };
      const k = rowKey(sig);
      if (!k.includes('::')) continue;
      const existing = map.get(k);
      if (existing) {
        existing.engines.add('B');
        existing.engineBSignal = sig;
        // Make sure the engine_b/naked_data payload is exposed on the primary
        // signal so the detail panel can render Engine B without juggling rows.
        if (!existing.signal.naked_data && !existing.signal.engine_b) {
          existing.signal = {
            ...existing.signal,
            naked_data: sig.naked_data ?? sig.engine_b ?? existing.signal.naked_data,
            engine_b: sig.engine_b ?? sig.naked_data ?? existing.signal.engine_b,
          };
        }
      } else {
        map.set(k, {
          id: k,
          signal: sig,
          engines: new Set(['B']),
        });
      }
    }
  }

  return [...map.values()];
}

function unifiedEngineLabel(engines: Set<'A' | 'B'>): EngineSource {
  if (engines.has('A')) return 'A';
  return 'B';
}

function canExecuteRow(row: UnifiedRow | null): boolean {
  if (!row) return false;
  // Engine B alone is always executable (it has its own gates). Engine A
  // watchlist tier is display-only.
  if (row.engines.has('B') && !row.engines.has('A')) return true;
  const tier = String(
    row.signal.signalTier || row.signal.scan_tier || row.signal.signalClass || '',
  ).toLowerCase();
  if (tier.includes('watch') || tier === 'skip' || tier === 'blocked') return false;
  return tier === 'trade' || tier === 'criteria' || row.signal.trade === true;
}

function signalTraceId(signal: EngineASignal | null): string | null {
  if (!signal) return null;
  const raw = signal.trace_id ?? signal.traceId;
  return typeof raw === 'string' && raw.trim() ? raw.trim() : null;
}

function signalSymbol(signal: EngineASignal | null): string | null {
  if (!signal) return null;
  return signal.symbol || signal.pair || signal.display || null;
}

function preferredExecutionSignal(row: UnifiedRow): EngineASignal {
  // Engine A payload is canonical for execution when present; Engine B alone
  // falls back to the B signal. Mirrors the legacy execute-payload logic.
  if (row.engines.has('A') && !row.engines.has('B')) {
    const sig = row.signal;
    const payload = { ...sig } as Record<string, unknown>;
    delete payload.engine_b;
    delete payload.naked_data;
    delete payload.is_naked;
    return payload as EngineASignal;
  }
  return row.signal;
}

function resolveSignalSymbol(signal: EngineASignal): string {
  return String(signal.symbol || signal.pair || signal.display || '').toUpperCase().trim();
}

function preferredTvChartTf(signal: EngineASignal): string {
  const route = (signal as { timeframe_route?: { autoSelectTf?: string } }).timeframe_route?.autoSelectTf;
  const direct = signal.timeframe || route;
  if (direct && typeof direct === 'string') {
    return direct.toUpperCase();
  }
  const style = String(signal.style || signal.requestedStyle || '').toLowerCase();
  if (style === 'scalp' || style === 'intraday') return 'H1';
  return 'H4';
}

function intentSourceForRow(row: UnifiedRow): 'signals' | 'engine_a' | 'engine_b' {
  if (row.engines.has('A') && row.engines.has('B')) return 'signals';
  if (row.engines.has('B')) return 'engine_b';
  return 'engine_a';
}

export default function SignalsPanel() {
  const {
    showToast, isTestMode,
    scanCacheA, scanCacheB,
    scanCacheAMeta, scanCacheBMeta,
    setScanCacheA, setScanCacheB,
    setActivePanel, setTvChartIntent,
  } = useStore();
  const [filter, setFilter] = useState('');
  const [directionFilter, setDirectionFilter] = useState<string>('all');
  const [assetClass, setAssetClass] = useState<string>('all');
  const [style, setStyle] = useState<string>('auto');
  const [sortBy, setSortBy] = useState<string>('score');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<string>('overview');
  const [confirmRow, setConfirmRow] = useState<UnifiedRow | null>(null);
  const [pendingStyle, setPendingStyle] = useState<'scalp' | 'intraday' | 'swing' | 'auto'>('auto');
  const [sizingOverride, setSizingOverride] = useState<number>(1.0);

  // Hot-cached snapshot from server-side last scan (Engine A only).
  const { data: lastScan, loading: lastLoading, error: lastError, refresh: refreshLast } =
    useApiPoll<ScanResponse>('/api/last-scan', 60_000);

  const { data: autoTrade } = useApiPoll<{ enabled: boolean }>('/api/auto-trade', 60_000);
  const { post: postScanA, loading: scanningA } = useApiPost<ScanResponse>();
  const { post: postScanB, loading: scanningB } = useApiPost<NakedScanResponse>();
  const { post: postVision, loading: visionLoading } = useApiPost<ChartAnalysisResponse>();
  const { post: postAiText, loading: textLoading } = useApiPost<AiTextReviewResponse>();
  const [aiReview, setAiReview] = useState<ChartAnalysisResponse | null>(null);
  const [aiTextReview, setAiTextReview] = useState<AiTextReviewResponse | null>(null);
  const [aiReviewMode, setAiReviewMode] = useState<'vision' | 'text'>('vision');
  const [agentOpen, setAgentOpen] = useState(false);
  const [executing, setExecuting] = useState(false);
  const { priceFor, ageSecFor, sourceFor } = useLivePrices();

  // Build the unified row list from both engine caches.
  const rows: UnifiedRow[] = useMemo(
    () => unifyScanResults(
      scanCacheA as EngineASignal[] | null,
      scanCacheB as EngineASignal[] | null,
      lastScan ?? null,
    ),
    [scanCacheA, scanCacheB, lastScan],
  );

  // Apply live price + filters + sort.
  const filteredRows = useMemo(() => {
    const liveRows = rows.map((r) => ({
      ...r,
      signal: {
        ...r.signal,
        livePrice: priceFor(r.signal),
        livePriceAgeSec: ageSecFor(r.signal),
        livePriceSource: sourceFor(r.signal),
      },
    }));
    const list = liveRows.filter((r) => {
      const pair = (r.signal.display || r.signal.pair || r.signal.symbol || '').toLowerCase();
      if (filter && !pair.includes(filter.toLowerCase())) return false;
      if (directionFilter !== 'all' && r.signal.direction !== directionFilter) return false;
      if (assetClass !== 'all' && (r.signal.type || '').toLowerCase() !== assetClass) return false;
      return true;
    });
    list.sort((a, b) => compareUnified(a, b, sortBy));
    return list;
  }, [rows, priceFor, filter, directionFilter, assetClass, sortBy]);

  /** Grouped by scoreGroup (Engine A) or asset type. Watchlist falls within its group. */
  const groupedRows = useMemo(() => {
    const map = new Map<string, UnifiedRow[]>();
    for (const r of filteredRows) {
      const k = signalGroupKey(r.signal);
      if (!map.has(k)) map.set(k, []);
      map.get(k)!.push(r);
    }
    const entries = [...map.entries()].map(([key, items]) => {
      const sorted = [...items].sort((a, b) => compareUnified(a, b, sortBy));
      const top = sorted.length
        ? toNum(sorted[0].signal.confluenceScore ?? sorted[0].signal.score, 0)
        : 0;
      return { key, items: sorted, topScore: top };
    });
    entries.sort((a, b) => {
      if (b.topScore !== a.topScore) return b.topScore - a.topScore;
      return a.key.localeCompare(b.key);
    });
    return entries;
  }, [filteredRows, sortBy]);

  const selectedRow = useMemo<UnifiedRow | null>(() => {
    if (!selectedId) return null;
    return filteredRows.find((r) => r.id === selectedId)
      ?? rows.find((r) => r.id === selectedId)
      ?? null;
  }, [selectedId, filteredRows, rows]);

  const scanEngineA = useCallback(async () => {
    const ac = assetClass === 'all' ? '' : assetClass;
    try {
      const result = await postScanA('/api/scan', { asset_class: ac, force: false, style });
      if (!result) return null;
      const tradeSignals = Array.isArray(result.signals) ? result.signals : [];
      const watchlistSignals = Array.isArray(result.watchlist) ? result.watchlist : [];
      const displaySignals = [
        ...tradeSignals.map((s) => ({
          ...s,
          signalClass: s.signalClass || 'TRADE',
          signalTier: s.signalTier || 'trade',
        })),
        ...watchlistSignals.map((s) => ({
          ...s,
          signalClass: s.signalClass || 'WATCHLIST',
          signalTier: s.signalTier || 'watchlist',
          trade: false,
        })),
      ];
      setScanCacheA(displaySignals, {
        count: displaySignals.length,
        scannedAt: new Date().toISOString(),
      });
      return { trade: tradeSignals.length, watchlist: watchlistSignals.length, pairs: result.totalPairs ?? result.pairs_scanned };
    } catch {
      return null;
    }
  }, [assetClass, style, postScanA, setScanCacheA]);

  const scanEngineB = useCallback(async () => {
    const ac = assetClass === 'all' ? '' : assetClass;
    try {
      const result = await postScanB('/api/scan-naked', { assetClass: ac, style });
      if (!result || !result.signals) return null;
      const pairsScanned = result.totalPairs ?? result.activePairs;
      const funnel = result.scanFunnel as Record<string, number> | undefined;
      setScanCacheB(result.signals as EngineASignal[], {
        count: result.signals.length,
        scannedAt: new Date().toISOString(),
        ...(pairsScanned != null ? { pairsScanned } : {}),
        ...(funnel && Object.keys(funnel).length ? { scanFunnel: funnel } : {}),
      });
      return { count: result.signals.length, pairs: pairsScanned };
    } catch {
      return null;
    }
  }, [assetClass, style, postScanB, setScanCacheB]);

  const runScanA = useCallback(async () => {
    const a = await scanEngineA();
    setSortBy('score');
    showToast(
      a ? `Engine A scan complete · ${a.trade} trade / ${a.watchlist} watch` : 'Engine A scan failed',
      a ? 'success' : 'error',
    );
  }, [scanEngineA, showToast]);

  const runScanB = useCallback(async () => {
    const b = await scanEngineB();
    setSortBy('score');
    showToast(
      b ? `Engine B scan complete · ${b.count} setups` : 'Engine B scan failed',
      b ? 'success' : 'error',
    );
  }, [scanEngineB, showToast]);

  const isPaper = autoTrade?.enabled ?? false;
  const onOpenAndReview = useCallback((row: UnifiedRow) => {
    const sig = row.signal;
    const symbol = resolveSignalSymbol(sig);
    if (!symbol) {
      showToast('Cannot open chart: missing symbol', 'error');
      return;
    }
    setTvChartIntent({
      id: `tv-${symbol}-${Date.now()}`,
      source: intentSourceForRow(row),
      symbol,
      display: sig.display || sig.pair || symbol,
      signal: sig,
      preferredTf: preferredTvChartTf(sig),
      autoReview: true,
      createdAt: new Date().toISOString(),
    });
    setActivePanel('tvChart');
    showToast(`Opening ${sig.display || symbol} on TV Chart for AI review`, 'info');
  }, [setActivePanel, setTvChartIntent, showToast]);

  const selectedCanExecute = canExecuteRow(selectedRow);
  const executeDisabled = isTestMode || !selectedCanExecute;
  const executeLabel = isTestMode
    ? 'TEST MODE'
    : !selectedCanExecute
      ? 'WATCHLIST'
      : isPaper
        ? 'PAPER MODE'
        : 'Execute';

  const runAiReview = useCallback(
    async (sig: EngineASignal | null) => {
      if (!sig) return;
      const sym = sig.symbol || sig.pair || sig.display;
      if (!sym) {
        showToast('Selected signal has no symbol', 'error');
        return;
      }
      try {
        const candlePayload = await fetchVisionCandlePayload(sym);
        const result = await postVision('/api/chart-analysis', {
          symbol: sym,
          tf: 'H4',
          signal: sig,
          engineB: sig.naked_data || sig.engine_b,
          server_render: true,
          chart_source: 'signals_panel',
          ...candlePayload,
          entry: sig.entry ?? sig.price,
          sl: sig.sl,
          tp: sig.tp ?? sig.tp1,
        });
        if (!result || (result as ChartAnalysisResponse).error) {
          showToast(`AI Review failed: ${(result as ChartAnalysisResponse | null)?.error || 'unknown'}`, 'error');
          setAiReview(null);
          return;
        }
        setAiReview(result);
        setAiReviewMode('vision');
        showToast('AI Vision review ready', 'success');
      } catch (err) {
        showToast(`AI Review error: ${err instanceof Error ? err.message : 'unknown'}`, 'error');
      }
    },
    [postVision, showToast],
  );

  const runAiTextReview = useCallback(
    async (sig: EngineASignal | null) => {
      if (!sig) return;
      const sym = sig.symbol || sig.pair || sig.display;
      if (!sym) {
        showToast('Selected signal has no symbol', 'error');
        return;
      }
      try {
        const result = await postAiText('/api/analyze', {
          signal: sig,
          stylePreference: sig.style || 'auto',
        });
        if (!result || result.error) {
          showToast(`AI Text Review failed: ${result?.error || 'unknown'}`, 'error');
          setAiTextReview(null);
          return;
        }
        setAiTextReview(result);
        setAiReviewMode('text');
        showToast('AI Text review ready', 'success');
      } catch (err) {
        showToast(`AI Text Review error: ${err instanceof Error ? err.message : 'unknown'}`, 'error');
      }
    },
    [postAiText, showToast],
  );

  const handleSelect = useCallback((row: UnifiedRow) => {
    setSelectedId(row.id);
    setAiReview(null);
    setAiTextReview(null);
    setAgentOpen(false);
    setDetailTab('overview');
  }, []);

  const requestExecute = useCallback(
    (row: UnifiedRow, modeArg: 'scalp' | 'intraday' | 'swing' | 'auto') => {
      setPendingStyle(modeArg);
      setConfirmRow(row);
    },
    [],
  );

  const onConfirmExecute = useCallback(async () => {
    if (!confirmRow) return;
    const sig = confirmRow.signal;
    const effectiveStyle = pendingStyle === 'auto'
      ? (sig.style || (style === 'auto' ? 'swing' : style))
      : pendingStyle;
    const isEngineBOnly = confirmRow.engines.has('B') && !confirmRow.engines.has('A');
    const payload = buildQuickExecutePayload({
      signal: preferredExecutionSignal(confirmRow),
      engineBOverlay: (isEngineBOnly
        ? (sig.naked_data ?? sig.engine_b ?? {})
        : {}) as Record<string, unknown>,
      isEngineBOnly,
      pipMode: String(effectiveStyle),
      sizingOverride,
    });
    setExecuting(true);
    try {
      const result = await apiClient.postJson(
        '/api/quick-execute',
        payload as unknown as Record<string, unknown>,
      ) as ExecuteResponse;
      if (result.approval && !result.approval.approved) {
        showToast(`Rejected: ${result.approval.reason}`, 'error');
      } else if (result.success || result.ticket) {
        showToast(
          `Executed ${sig.pair || sig.display} (${effectiveStyle.toUpperCase()}) - ticket ${result.ticket || '?'}`,
          'success',
        );
      } else {
        showToast(`Error: ${result.error || 'Unknown'}`, 'error');
      }
    } catch (err) {
      showToast(`Execution failed: ${err instanceof Error ? err.message : 'unknown'}`, 'error');
    } finally {
      setExecuting(false);
      setConfirmRow(null);
      setPendingStyle('auto');
    }
  }, [confirmRow, style, pendingStyle, sizingOverride, showToast]);

  const lastScannedAtA = scanCacheAMeta?.scannedAt ?? (lastScan as ScanResponse | null)?.scannedAt;
  const lastScannedAtB = scanCacheBMeta?.scannedAt;
  const scanning = scanningA || scanningB;

  return (
    <div className="space-y-4">
      {/* -- TOOLBAR -- */}
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-3 flex items-center gap-2 flex-wrap">
          <div className="relative flex-1 min-w-[180px]">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <Input
              placeholder="Filter by pair..."
              className="pl-8 h-8 text-xs"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>
          <Select value={assetClass} onValueChange={setAssetClass}>
            <SelectTrigger className="w-[120px] h-8 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All assets</SelectItem>
              <SelectItem value="forex">Forex</SelectItem>
              <SelectItem value="crypto">Crypto</SelectItem>
              <SelectItem value="stock">Stocks</SelectItem>
              <SelectItem value="index">Indices</SelectItem>
              <SelectItem value="commodity">Commodities</SelectItem>
              <SelectItem value="etf">ETFs</SelectItem>
            </SelectContent>
          </Select>
          <Select value={style} onValueChange={setStyle}>
            <SelectTrigger className="w-[110px] h-8 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">Auto</SelectItem>
              <SelectItem value="scalp">Scalp</SelectItem>
              <SelectItem value="intraday">Intraday</SelectItem>
              <SelectItem value="swing">Swing</SelectItem>
            </SelectContent>
          </Select>
          <Select value={directionFilter} onValueChange={setDirectionFilter}>
            <SelectTrigger className="w-[110px] h-8 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any direction</SelectItem>
              <SelectItem value="LONG">Long</SelectItem>
              <SelectItem value="SHORT">Short</SelectItem>
            </SelectContent>
          </Select>
          <Select value={sortBy} onValueChange={setSortBy}>
            <SelectTrigger className="w-[120px] h-8 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="score">Score</SelectItem>
              <SelectItem value="conviction">Conviction</SelectItem>
              <SelectItem value="rr">R:R</SelectItem>
              <SelectItem value="time">Time</SelectItem>
            </SelectContent>
          </Select>

          <div className="flex items-center gap-1 ml-auto">
            <Button
              size="sm"
              variant="outline"
              className="h-8 gap-1 text-xs"
              onClick={runScanA}
              disabled={scanningA}
            >
              {scanningA
                ? <span className="w-3.5 h-3.5 rounded-full border-2 border-current border-t-transparent animate-spin" />
                : <Zap className="w-3.5 h-3.5" />
              }
              {scanningA ? 'A…' : 'Scan A'}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-8 gap-1 text-xs"
              onClick={runScanB}
              disabled={scanningB}
            >
              {scanningB
                ? <span className="w-3.5 h-3.5 rounded-full border-2 border-current border-t-transparent animate-spin" />
                : <Layers className="w-3.5 h-3.5" />
              }
              {scanningB ? 'B…' : 'Scan B'}
            </Button>
            <RefreshButton onClick={refreshLast} loading={lastLoading} />
          </div>
        </CardContent>
      </Card>

      {lastError && <ErrorBanner message={lastError} onRetry={refreshLast} />}

      {/* Status row */}
      <div className="flex items-center gap-3 text-[11px] text-muted-foreground flex-wrap">
        <span>
          {filteredRows.length} signal{filteredRows.length === 1 ? '' : 's'} shown
          {rows.length !== filteredRows.length && ` (of ${rows.length} total)`}
        </span>
        {lastScannedAtA && (
          <span>Engine A: {new Date(lastScannedAtA).toLocaleTimeString()}</span>
        )}
        {lastScannedAtB && (
          <span>Engine B: {new Date(lastScannedAtB).toLocaleTimeString()}{' '}
            {scanCacheBMeta?.pairsScanned != null && `· ${scanCacheBMeta.pairsScanned} pairs`}
          </span>
        )}
        {scanCacheBMeta?.scanFunnel && (
          <span className="text-[10px] max-w-3xl leading-snug" title="Per-pair reject breakdown from last Engine B scan">
            Engine B rejects: {formatEngineBScanFunnel(scanCacheBMeta.scanFunnel) ?? '—'}
          </span>
        )}
      </div>

      <div className="grid grid-cols-5 gap-4">
        {/* ─── Signal list (left, spans 3) ─── */}
        <Card className="col-span-3 border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle
              className="text-xs font-semibold flex items-center justify-between uppercase tracking-wider"
              style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}
            >
              <span>Signal Feed</span>
              <Badge variant="outline" className="text-[10px]">
                {filteredRows.length} shown
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-[680px] pr-2">
              {scanning && filteredRows.length === 0 ? (
                <div className="space-y-2">
                  {Array.from({ length: 6 }).map((_, i) => (
                    <Skeleton key={i} className="h-24 w-full" />
                  ))}
                </div>
              ) : filteredRows.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-[300px] text-muted-foreground text-center px-4">
                  <Filter className="w-8 h-8 mb-3 opacity-40" />
                  {rows.length === 0 ? (
                    <>
                      <p className="text-sm">No signals yet</p>
                      <p className="text-[11px] mt-1">
                        Run Engine A or Engine B from the controls above.
                      </p>
                    </>
                  ) : (
                    <>
                      <p className="text-sm">No signals match your filters</p>
                      <p className="text-[11px] mt-1">
                        {rows.length} signals total — adjust pair / direction / asset filters.
                      </p>
                    </>
                  )}
                </div>
              ) : (
                <div className="space-y-4">
                  {groupedRows.map(({ key, items }) => (
                    <div key={key} className="space-y-2">
                      <div
                        className="flex items-center justify-between sticky top-0 z-[1] bg-card/95 backdrop-blur-sm py-1 border-b border-border/50"
                      >
                        <span className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
                          {formatGroupLabel(key)}
                        </span>
                        <Badge variant="outline" className="text-[9px] font-mono">
                          {items.length} - top {fmtNum(items[0]?.signal.confluenceScore ?? items[0]?.signal.score, 2)}
                        </Badge>
                      </div>
                      <div className="space-y-2 pl-0">
                        {items.map((row) => (
                          <UnifiedSignalRow
                            key={row.id}
                            row={row}
                            selected={selectedId === row.id}
                            onSelect={handleSelect}
                          />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </ScrollArea>
          </CardContent>
        </Card>

        {/* ─── Signal detail (right, spans 2) — tabbed ─── */}
        <Card className="col-span-2 border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle
              className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider"
              style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}
            >
              <Activity className="w-4 h-4 text-primary" />
              Signal Detail
              {selectedRow && (
                <Badge variant="outline" className="ml-auto text-[10px] font-mono">
                  {unifiedEngineLabel(selectedRow.engines)} · {selectedRow.signal.display || selectedRow.signal.pair}
                </Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {selectedRow ? (
              <Tabs value={detailTab} onValueChange={setDetailTab} className="w-full">
                <TabsList className="w-full grid grid-cols-5 h-9">
                  <TabsTrigger value="overview" className="text-[10px] gap-1">
                    <Activity className="w-3 h-3" /> Overview
                  </TabsTrigger>
                  <TabsTrigger value="chart" className="text-[10px] gap-1">
                    <BarChart2 className="w-3 h-3" /> Chart
                  </TabsTrigger>
                  <TabsTrigger value="ai" className="text-[10px] gap-1">
                    <Bot className="w-3 h-3" /> AI Review
                  </TabsTrigger>
                  <TabsTrigger value="engineB" className="text-[10px] gap-1">
                    <Layers className="w-3 h-3" /> Engine B
                  </TabsTrigger>
                  <TabsTrigger value="diag" className="text-[10px] gap-1">
                    <Gauge className="w-3 h-3" /> Diagnostics
                  </TabsTrigger>
                </TabsList>

                <ScrollArea className="h-[620px] pr-2 mt-3">
                  {/* ── OVERVIEW TAB ── */}
                  <TabsContent value="overview" className="space-y-3 mt-0">
                    <EngineASignalCard
                      signal={{
                        ...selectedRow.signal,
                        livePrice: priceFor(selectedRow.signal),
                        livePriceAgeSec: ageSecFor(selectedRow.signal),
                        livePriceSource: sourceFor(selectedRow.signal),
                      }}
                      onExecute={(s) => requestExecute({ ...selectedRow, signal: s }, pendingStyle)}
                      executeDisabled={executeDisabled}
                      executeLabel={executeLabel}
                    />

                    <Button
                      size="sm"
                      variant="secondary"
                      className="w-full gap-2"
                      onClick={() => onOpenAndReview(selectedRow)}
                    >
                      <Sparkles className="w-3.5 h-3.5" />
                      Open &amp; Review
                    </Button>

                    {/* Per-style execute toolbar */}
                    <Card className="border-border/60 bg-card/50">
                      <CardContent className="p-3 space-y-3">
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-[10px] uppercase text-muted-foreground">Execute by Style</p>
                          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                            <span>Sizing</span>
                            <Input
                              type="number"
                              min={0.25}
                              max={1.0}
                              step={0.05}
                              className="h-7 w-16 text-xs font-mono"
                              value={sizingOverride}
                              onChange={(e) => {
                                const v = parseFloat(e.target.value);
                                setSizingOverride(Number.isFinite(v) ? Math.max(0.25, Math.min(1.0, v)) : 1.0);
                              }}
                            />
                            <span className="font-mono">x</span>
                          </div>
                        </div>
                        <div className="grid grid-cols-3 gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-9 gap-1 text-xs hover:bg-warning/10"
                            onClick={() => requestExecute(selectedRow, 'scalp')}
                            disabled={executeDisabled || executing}
                          >
                            <Clock className="w-3.5 h-3.5" />
                            Scalp
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-9 gap-1 text-xs hover:bg-primary/10"
                            onClick={() => requestExecute(selectedRow, 'intraday')}
                            disabled={executeDisabled || executing}
                          >
                            <Sun className="w-3.5 h-3.5" />
                            Intraday
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-9 gap-1 text-xs hover:bg-long/10"
                            onClick={() => requestExecute(selectedRow, 'swing')}
                            disabled={executeDisabled || executing}
                          >
                            <Moon className="w-3.5 h-3.5" />
                            Swing
                          </Button>
                        </div>
                        <p className="text-[10px] text-muted-foreground">
                          Backend recomputes SL/TP for the chosen style (ATR ladder). Sizing scales risk between 0.25x and 1.0x.
                        </p>
                      </CardContent>
                    </Card>

                    {/* Levels detail */}
                    <Card className="border-border/60 bg-card/50">
                      <CardContent className="p-3 space-y-2">
                        <p className="text-[10px] uppercase text-muted-foreground">All Levels</p>
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          <DetailRow
                            label="Live"
                            value={fmtPrice(priceFor(selectedRow.signal), selectedRow.signal.pair, selectedRow.signal.type)}
                            meta={fmtLiveQuoteMeta(ageSecFor(selectedRow.signal), sourceFor(selectedRow.signal))}
                          />
                          <DetailRow label="Entry"  value={fmtPrice(selectedRow.signal.entry ?? selectedRow.signal.price, selectedRow.signal.pair, selectedRow.signal.type)} />
                          <DetailRow label="SL"     value={fmtPrice(selectedRow.signal.sl, selectedRow.signal.pair, selectedRow.signal.type)} accent="short" />
                          <DetailRow label="TP1"    value={fmtPrice(selectedRow.signal.tp ?? selectedRow.signal.tp1, selectedRow.signal.pair, selectedRow.signal.type)} accent="long" />
                          <DetailRow label="TP2"    value={fmtPrice(selectedRow.signal.tp2, selectedRow.signal.pair, selectedRow.signal.type)} accent="long" />
                          <DetailRow
                            label="ATR"
                            value={fmtPrice(selectedRow.signal.atr, selectedRow.signal.pair, selectedRow.signal.type)}
                            meta={fmtAtrMeta(selectedRow.signal.atrDiagnostics, selectedRow.signal.atrFreshness)}
                          />
                          <DetailRow label="R:R"    value={fmtNum(selectedRow.signal.rr ?? selectedRow.signal.rr1, 2)} />
                          <DetailRow label="Scalp SL"    value={fmtPrice(selectedRow.signal.scalp_sl, selectedRow.signal.pair, selectedRow.signal.type)} />
                          <DetailRow label="Scalp TP"    value={fmtPrice(selectedRow.signal.scalp_tp, selectedRow.signal.pair, selectedRow.signal.type)} />
                          <DetailRow label="Intraday SL" value={fmtPrice(selectedRow.signal.intraday_sl, selectedRow.signal.pair, selectedRow.signal.type)} />
                          <DetailRow label="Intraday TP" value={fmtPrice(selectedRow.signal.intraday_tp, selectedRow.signal.pair, selectedRow.signal.type)} />
                        </div>
                      </CardContent>
                    </Card>
                  </TabsContent>

                  {/* ── CHART TAB ── */}
                  <TabsContent value="chart" className="space-y-3 mt-0">
                    <H4ChartCard signal={selectedRow.signal} />
                  </TabsContent>

                  {/* ── AI REVIEW TAB ── */}
                  <TabsContent value="ai" className="space-y-3 mt-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-8 text-xs gap-1"
                        onClick={() => runAiReview(selectedRow.signal)}
                        disabled={visionLoading}
                      >
                        <Eye className={visionLoading ? 'w-3.5 h-3.5 animate-pulse' : 'w-3.5 h-3.5'} />
                        {visionLoading ? 'AI Vision...' : 'Run AI Vision'}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-8 text-xs gap-1"
                        onClick={() => runAiTextReview(selectedRow.signal)}
                        disabled={textLoading}
                      >
                        <FileText className={textLoading ? 'w-3.5 h-3.5 animate-pulse' : 'w-3.5 h-3.5'} />
                        {textLoading ? 'AI Text...' : 'Run AI Text'}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-8 text-xs gap-1"
                        onClick={() => setAgentOpen((open) => !open)}
                      >
                        <Bot className="w-3.5 h-3.5" />
                        {agentOpen ? 'Close chat' : 'Discuss with AI'}
                      </Button>
                      {aiReview?.structured?.right_edge_status && aiReviewMode === 'vision' && (
                        <Badge
                          className={
                            aiReview.structured.right_edge_status === 'CONFIRMS' ? 'badge-long'
                            : aiReview.structured.right_edge_status === 'POTENTIAL_REVERSAL' ? 'badge-short'
                            : 'badge-neutral'
                          }
                        >
                          RIGHT EDGE: {aiReview.structured.right_edge_status.replace('_', ' ')}
                        </Badge>
                      )}
                      {aiTextReview?.grade && aiReviewMode === 'text' && (
                        <Badge variant="outline" className="text-[10px] uppercase">
                          Grade {aiTextReview.grade}
                        </Badge>
                      )}
                    </div>

                    {agentOpen && (
                      <AITradingAgentPanel
                        symbol={signalSymbol(selectedRow.signal)}
                        traceId={signalTraceId(selectedRow.signal)}
                        signal={(selectedRow.signal as unknown as Record<string, unknown>) ?? null}
                        seedMessage="Review this trade. What supports it, what argues against it, and what would confirm or invalidate it?"
                      />
                    )}

                    {(aiReview || aiTextReview) && (
                      <div className="flex items-center gap-1 bg-muted/30 rounded-md p-1 w-fit">
                        <button
                          type="button"
                          onClick={() => setAiReviewMode('vision')}
                          className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                            aiReviewMode === 'vision' ? 'bg-background shadow-sm text-foreground' : 'text-muted-foreground hover:text-foreground'
                          }`}
                          disabled={!aiReview}
                        >
                          Vision
                        </button>
                        <button
                          type="button"
                          onClick={() => setAiReviewMode('text')}
                          className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                            aiReviewMode === 'text' ? 'bg-background shadow-sm text-foreground' : 'text-muted-foreground hover:text-foreground'
                          }`}
                          disabled={!aiTextReview}
                        >
                          Text
                        </button>
                      </div>
                    )}

                    {!aiReview && !aiTextReview && !agentOpen && (
                      <div className="text-[11px] text-muted-foreground border border-border/40 rounded-md p-3 leading-snug">
                        Run an AI Vision or Text review to compare AI commentary against this signal. AI review is advisory only — it cannot change trade gates or scoring.
                      </div>
                    )}

                    {aiReview && aiReviewMode === 'vision' && (
                      <VisionReviewCard vision={aiReview} />
                    )}

                    {aiTextReview && aiReviewMode === 'text' && (
                      <Card className="border-border/60 bg-card/50">
                        <CardContent className="p-3 space-y-2">
                          <div className="flex items-center justify-between">
                            <p className="text-[10px] uppercase text-muted-foreground">AI Text Review · Marcus Reid</p>
                            <span className="text-[10px] text-muted-foreground font-mono">
                              {aiTextReview.grade || '-'} · edge {aiTextReview.edgeProbability ?? '-'}%
                            </span>
                          </div>
                          {aiTextReview.style_ratings && (
                            <div className="grid grid-cols-3 gap-2 text-[11px]">
                              <div>
                                <div className="text-muted-foreground text-[10px]">Scalp</div>
                                <div className="font-mono">{aiTextReview.style_ratings.scalp?.grade || '-'}</div>
                              </div>
                              <div>
                                <div className="text-muted-foreground text-[10px]">Intraday</div>
                                <div className="font-mono">{aiTextReview.style_ratings.intraday?.grade || '-'}</div>
                              </div>
                              <div>
                                <div className="text-muted-foreground text-[10px]">Swing</div>
                                <div className="font-mono">{aiTextReview.style_ratings.swing?.grade || '-'}</div>
                              </div>
                            </div>
                          )}
                          <div className="grid grid-cols-2 gap-2 text-[11px]">
                            <div><span className="text-muted-foreground">Verdict:</span> <span className="font-mono">{aiTextReview.verdict || '-'}</span></div>
                            <div><span className="text-muted-foreground">Risk:</span> <span className="font-mono">{aiTextReview.riskLevel || '-'}</span></div>
                            <div><span className="text-muted-foreground">Position:</span> <span className="font-mono">{aiTextReview.positionSizing || '-'}</span></div>
                            <div><span className="text-muted-foreground">Entry:</span> <span className="font-mono">{aiTextReview.entryZone || '-'}</span></div>
                            <div><span className="text-muted-foreground">Invalidation:</span> <span className="font-mono">{aiTextReview.invalidation || '-'}</span></div>
                            <div><span className="text-muted-foreground">Key Levels:</span> <span className="font-mono">{aiTextReview.keyLevels || '-'}</span></div>
                          </div>
                          {aiTextReview.warnings && aiTextReview.warnings.length > 0 && (
                            <div className="text-[11px] text-warning">
                              {aiTextReview.warnings.map((w, i) => (
                                <div key={i}>- {w}</div>
                              ))}
                            </div>
                          )}
                          {aiTextReview.narrative && (
                            <pre className="text-[11px] font-mono whitespace-pre-wrap text-foreground/90 max-h-72 overflow-y-auto p-2 bg-muted/20 rounded border border-border/40">
                              {aiTextReview.narrative}
                            </pre>
                          )}
                        </CardContent>
                      </Card>
                    )}
                  </TabsContent>

                  {/* ── ENGINE B TAB ── */}
                  <TabsContent value="engineB" className="space-y-3 mt-0">
                    {(selectedRow.signal.naked_data || selectedRow.signal.engine_b) ? (
                      <EngineBChecklistCard
                        data={selectedRow.signal.naked_data || selectedRow.signal.engine_b}
                        pair={selectedRow.signal.pair || selectedRow.signal.display}
                        type={selectedRow.signal.type}
                        livePrice={priceFor(selectedRow.signal)}
                        livePriceAgeSec={ageSecFor(selectedRow.signal)}
                        livePriceSource={sourceFor(selectedRow.signal)}
                      />
                    ) : (
                      <div className="text-[11px] text-muted-foreground border border-border/40 rounded-md p-3 leading-snug">
                        Engine B did not flag this setup. Engine B is strict by design — it requires a complete checklist (structure, location, trigger, room, RR, no D1 conflict).
                      </div>
                    )}
                  </TabsContent>

                  {/* ── DIAGNOSTICS TAB ── */}
                  <TabsContent value="diag" className="space-y-3 mt-0">
                    {selectedRow.signal.confidenceDetail && (
                      <Card className="border-border/60 bg-card/50">
                        <CardContent className="p-3 space-y-2">
                          <div className="flex items-center justify-between">
                            <p className="text-[10px] uppercase text-muted-foreground flex items-center gap-1">
                              <Shield className="w-3 h-3" /> Confidence Engine
                            </p>
                            <Badge variant="outline" className="text-[10px]">
                              {fmtNum(selectedRow.signal.confidenceDetail.confidence, 2)}
                              {selectedRow.signal.confidenceDetail.degraded ? ' · degraded' : ''}
                            </Badge>
                          </div>
                          <div className="grid grid-cols-2 gap-2 text-xs">
                            <DetailRow label="Indicator agreement" value={fmtNum(selectedRow.signal.confidenceDetail.components?.indicator_agreement, 2)} />
                            <DetailRow label="TF alignment" value={fmtNum(selectedRow.signal.confidenceDetail.components?.timeframe_alignment, 2)} />
                            <DetailRow label="Regime fit" value={fmtNum(selectedRow.signal.confidenceDetail.components?.regime_fit, 2)} />
                            <DetailRow label="Liquidity" value={fmtNum(selectedRow.signal.confidenceDetail.components?.liquidity_quality, 2)} />
                            <DetailRow label="Confidence" value={fmtNum(selectedRow.signal.confidenceDetail.confidence, 2)} />
                            <DetailRow label="Session quality" value={String(selectedRow.signal.confidenceDetail.session_quality || '-')} />
                            {selectedRow.signal.confidenceDetail.available_count != null && (
                              <DetailRow label="Components active" value={`${selectedRow.signal.confidenceDetail.available_count}/4`} />
                            )}
                          </div>
                          {selectedRow.signal.confidenceDetail.weights_used && (
                            <div className="text-[10px] text-muted-foreground">
                              Weights: {Object.entries(selectedRow.signal.confidenceDetail.weights_used)
                                .map(([k, v]) => `${k}=${fmtNum(v, 2)}`)
                                .join(' · ')}
                            </div>
                          )}
                        </CardContent>
                      </Card>
                    )}

                    {selectedRow.signal.factorDiagnostics && (
                      <Card className="border-border/60 bg-card/50">
                        <CardContent className="p-3 space-y-2">
                          <p className="text-[10px] uppercase text-muted-foreground">Factor Diagnostics</p>
                          <div className="grid grid-cols-2 gap-2 text-xs">
                            {Object.entries(selectedRow.signal.factorDiagnostics)
                              .slice(0, 16)
                              .map(([k, v]) => (
                                <DetailRow
                                  key={k}
                                  label={k}
                                  value={typeof v === 'number' ? fmtNum(v, 2) : String(v ?? '-')}
                                />
                              ))}
                          </div>
                        </CardContent>
                      </Card>
                    )}

                    {!selectedRow.signal.confidenceDetail && !selectedRow.signal.factorDiagnostics && (
                      <div className="text-[11px] text-muted-foreground border border-border/40 rounded-md p-3 leading-snug">
                        No diagnostics attached to this signal. Engine A signals always carry confidence + factor diagnostics; Engine B-only setups may not.
                      </div>
                    )}
                  </TabsContent>
                </ScrollArea>
              </Tabs>
            ) : (
              <div className="flex flex-col items-center justify-center h-[680px] text-muted-foreground">
                <Activity className="w-8 h-8 mb-3 opacity-40" />
                <p className="text-sm">Select a signal to inspect</p>
                <p className="text-[11px] mt-1 max-w-xs text-center">
                  Use the unified list on the left — Engine A and Engine B share one feed, each row tagged with its source.
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <AlertDialog
        open={!!confirmRow}
        onOpenChange={() => { setConfirmRow(null); setPendingStyle('auto'); }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Confirm Execution — {pendingStyle === 'auto'
                ? (confirmRow?.signal.style?.toUpperCase() || 'AUTO')
                : pendingStyle.toUpperCase()}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-xs">
                <div>
                  Execute <b>{confirmRow?.signal.direction}</b> {confirmRow?.signal.pair || confirmRow?.signal.display} at{' '}
                  <span className="font-mono">{fmtPrice(confirmRow?.signal.entry ?? confirmRow?.signal.price, confirmRow?.signal.pair, confirmRow?.signal.type)}</span>
                </div>
                <div className="font-mono">
                  SL: {fmtPrice(confirmRow?.signal.sl, confirmRow?.signal.pair, confirmRow?.signal.type)} ·{' '}
                  TP: {fmtPrice(confirmRow?.signal.tp ?? confirmRow?.signal.tp1, confirmRow?.signal.pair, confirmRow?.signal.type)} ·{' '}
                  R:R {fmtNum(confirmRow?.signal.rr ?? confirmRow?.signal.rr1, 2)}
                </div>
                <div className="text-muted-foreground">
                  Engine: <span className="font-mono">{confirmRow ? unifiedEngineLabel(confirmRow.engines) : '—'}</span>{' · '}
                  Style: <span className="font-mono">{(pendingStyle === 'auto' ? confirmRow?.signal.style || 'auto' : pendingStyle).toUpperCase()}</span>{' · '}
                  Sizing: <span className="font-mono">{fmtNum(sizingOverride, 2)}x</span>
                </div>
                <div className="text-[10px] text-muted-foreground">
                  Backend will recompute SL/TP for the selected style via /api/quick-execute (recompute_levels_for_style),
                  apply risk_check, and route to MT5 or Bybit.
                </div>
                {isPaper && <span className="block text-warning">This will execute in PAPER mode.</span>}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setPendingStyle('auto')}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={onConfirmExecute} disabled={executing}>
              {executing ? 'Executing...' : `Confirm ${pendingStyle === 'auto' ? '' : pendingStyle.toUpperCase()}`}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/**
 * Compact unified row wrapping EngineASignalCard with a small left-rail
 * indicating which engine(s) flagged the setup. Engine A and Engine B
 * scores remain entirely separate — this is purely a visual layer.
 */
function UnifiedSignalRow({
  row,
  selected,
  onSelect,
}: {
  row: UnifiedRow;
  selected: boolean;
  onSelect: (row: UnifiedRow) => void;
}) {
  const label = unifiedEngineLabel(row.engines);
  const badge = label === 'B'
    ? { text: 'B', icon: <Layers className="w-3 h-3" />, bg: 'hsl(var(--info) / 0.18)', fg: 'hsl(var(--info))' }
    : { text: 'A', icon: <Zap className="w-3 h-3" />, bg: 'hsl(var(--gold) / 0.12)', fg: 'hsl(var(--gold-light))' };

  return (
    <div className="flex items-stretch gap-2">
      <div
        className="shrink-0 w-9 rounded-md border flex flex-col items-center justify-center text-[9px] font-mono uppercase tracking-wider"
        style={{ background: badge.bg, borderColor: 'hsl(var(--border) / 0.6)', color: badge.fg }}
        title={`Flagged by Engine ${label}`}
      >
        {badge.icon}
        <span className="mt-1 font-semibold">{badge.text}</span>
      </div>
      <div className="flex-1 min-w-0">
        <EngineASignalCard
          signal={row.signal}
          selected={selected}
          onSelect={() => onSelect(row)}
          compact
        />
      </div>
    </div>
  );
}

function DetailRow({ label, value, accent, meta }: { label: string; value: string; accent?: 'short' | 'long'; meta?: string }) {
  const fg = accent === 'long' ? 'text-long' : accent === 'short' ? 'text-short' : 'text-foreground';
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[10px] text-muted-foreground capitalize">{label.replace(/_/g, ' ')}</span>
      <span className={`text-xs font-mono text-right ${fg}`}>
        {value}
        {meta && <span className="block text-[9px] text-muted-foreground">{meta}</span>}
      </span>
    </div>
  );
}
