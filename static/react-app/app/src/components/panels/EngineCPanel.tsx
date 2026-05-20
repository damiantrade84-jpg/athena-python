import { useCallback, useMemo, useState } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { useLivePrices } from '@/hooks/useLivePrices';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
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
import { ErrorBanner } from '@/components/shared';
import { GitMerge, Play, BarChart3, Layers, Activity, Zap, Eye, Clock, Sun, Moon, FileText } from 'lucide-react';
import { fmtNum, toNum } from '@/lib/utils';
import { fmtLiveQuoteMeta, fmtPrice } from '@/lib/athenaFormat';
import { fetchVisionCandlePayload } from '@/lib/visionReview';
import { VisionReviewCard } from '@/components/athena';
import type {
  EngineCConsensusRow,
  EngineCScanResponse,
  EngineCBacktestResponse,
  CompareResponse,
  PairsResponse,
  PairListEntry,
  ChartAnalysisResponse,
  AiTextReviewResponse,
} from '@/types/athena';
import { AreaChart, Area, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

const ASSET_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All assets' },
  { value: 'forex', label: 'Forex' },
  { value: 'crypto', label: 'Crypto' },
  { value: 'commodity', label: 'Commodities' },
  { value: 'index', label: 'Indices' },
  { value: 'stock', label: 'Stocks' },
  { value: 'etf', label: 'ETFs' },
];

const STYLE_OPTIONS: { value: string; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'scalp', label: 'Scalp' },
  { value: 'intraday', label: 'Intraday' },
  { value: 'swing', label: 'Swing' },
];

type BucketKey = 'aligned' | 'a_only' | 'b_only' | 'conflict' | 'skipped';

const BUCKET_LABELS: Record<BucketKey, string> = {
  aligned: 'ALIGNED',
  a_only: 'A_ONLY',
  b_only: 'B_ONLY',
  conflict: 'CONFLICT',
  skipped: 'SKIPPED',
};

/** Derive group key from row for sub-grouping within buckets */
function rowGroupKey(row: EngineCConsensusRow): string {
  const sg = ((row as Record<string, unknown>).scoreGroup as string) || '';
  if (sg.trim()) return sg.trim().toLowerCase();
  const t = (row.type || '').trim().toLowerCase();
  return t || 'other';
}

/** Format group key for display */
function formatGroupLabel(key: string): string {
  if (!key || key === 'other') return 'Other';
  return key
    .split('_')
    .map((w) => (w.length ? w[0].toUpperCase() + w.slice(1) : ''))
    .join(' ');
}

/** Group rows by scoreGroup and sort groups by top conviction */
function groupRows(rows: EngineCConsensusRow[]): { key: string; rows: EngineCConsensusRow[] }[] {
  const map = new Map<string, EngineCConsensusRow[]>();
  for (const r of rows) {
    const k = rowGroupKey(r);
    if (!map.has(k)) map.set(k, []);
    map.get(k)!.push(r);
  }
  // Sort rows within each group by conviction descending
  const entries = [...map.entries()].map(([key, items]) => {
    const sorted = [...items].sort((a, b) => toNum(b.conviction) - toNum(a.conviction));
    const topConv = sorted.length ? toNum(sorted[0].conviction, 0) : 0;
    return { key, rows: sorted, topConv };
  });
  // Sort groups by top conviction, then alphabetically
  entries.sort((a, b) => {
    if (b.topConv !== a.topConv) return b.topConv - a.topConv;
    return a.key.localeCompare(b.key);
  });
  return entries.map(({ key, rows }) => ({ key, rows }));
}

function bucketBadgeClass(b: BucketKey): string {
  switch (b) {
    case 'aligned': return 'badge-long';
    case 'a_only': return 'badge-neutral';
    case 'b_only': return 'badge-neutral';
    case 'conflict': return 'badge-short';
    default: return 'badge-neutral';
  }
}

function tierBadgeClass(tier?: string): string {
  switch ((tier || '').toUpperCase()) {
    case 'HIGH': return 'badge-long';
    case 'MEDIUM': return 'badge-neutral';
    case 'LOW': return 'badge-neutral';
    case 'WATCHLIST': return 'badge-neutral';
    case 'SKIP': return 'badge-short';
    default: return 'badge-neutral';
  }
}

function flattenPairs(p: PairsResponse | null | undefined): PairListEntry[] {
  if (!p) return [];
  if (Array.isArray(p.pairs)) return p.pairs;

  // New /api/pairs format: { groups: { Forex: [{sym, label, enabled}, ...], ... } }
  const grouped = (p as Record<string, unknown>).groups;
  if (grouped && typeof grouped === 'object') {
    const out: PairListEntry[] = [];
    const typeMap: Record<string, string> = {
      Forex: 'forex',
      Crypto: 'crypto',
      'US Stocks': 'stock',
      Indices: 'index',
      Commodities: 'commodity',
      ETFs: 'etf',
    };
    for (const [label, items] of Object.entries(grouped)) {
      const type = typeMap[label] || label.toLowerCase();
      if (Array.isArray(items)) {
        for (const item of items) {
          if (item && typeof item === 'object') {
            out.push({
              symbol: String((item as Record<string, unknown>).sym || ''),
              display: String((item as Record<string, unknown>).label || (item as Record<string, unknown>).sym || ''),
              type,
              enabled: Boolean((item as Record<string, unknown>).enabled),
            });
          }
        }
      }
    }
    return out;
  }

  // Legacy bucketed shape
  const out: PairListEntry[] = [];
  const legacyGroups: { list?: string[]; type: string }[] = [
    { list: p.forex, type: 'forex' },
    { list: p.crypto, type: 'crypto' },
    { list: p.stocks, type: 'stock' },
    { list: p.indices, type: 'index' },
    { list: p.commodities, type: 'commodity' },
    { list: p.etf, type: 'etf' },
    { list: p.jse, type: 'stock' },
  ];
  for (const g of legacyGroups) {
    for (const sym of g.list || []) {
      out.push({ symbol: sym, display: sym, type: g.type });
    }
  }
  return out;
}

export default function EngineCPanel() {
  const { showToast } = useStore();
  const [assetClass, setAssetClass] = useState<string>('forex');
  const [style, setStyle] = useState<string>('auto');
  const [activeBucket, setActiveBucket] = useState<BucketKey>('aligned');
  const [scanResult, setScanResult] = useState<EngineCScanResponse | null>(null);
  const [selected, setSelected] = useState<EngineCConsensusRow | null>(null);

  const [comparePair, setComparePair] = useState<string>('EURUSD');
  const [compareStyle, setCompareStyle] = useState<string>('auto');
  const [compareResult, setCompareResult] = useState<CompareResponse | null>(null);

  const [scanPairFilter, setScanPairFilter] = useState('');
  const [comparePairBulk, setComparePairBulk] = useState('');
  const [bulkCompareResults, setBulkCompareResults] = useState<CompareResponse[]>([]);

  const [btPair, setBtPair] = useState<string>('EURUSD');
  const [btResult, setBtResult] = useState<EngineCBacktestResponse | null>(null);

  // Execution state
  const [confirmRow, setConfirmRow] = useState<EngineCConsensusRow | null>(null);
  const [pendingStyle, setPendingStyle] = useState<'scalp' | 'intraday' | 'swing'>('swing');
  const [sizingOverride, setSizingOverride] = useState<number>(1.0);

  // AI Review state
  const [aiReview, setAiReview] = useState<ChartAnalysisResponse | null>(null);
  const [aiTextReview, setAiTextReview] = useState<AiTextReviewResponse | null>(null);
  const [aiReviewMode, setAiReviewMode] = useState<'vision' | 'text'>('vision');

  const { data: pairsData } = useApiPoll<PairsResponse>('/api/pairs', 0);
  const allPairs = useMemo(() => flattenPairs(pairsData), [pairsData]);

  const { post: postScan, loading: scanning, error: scanError } = useApiPost<EngineCScanResponse>();
  const { post: postCompare, loading: comparing } = useApiPost<CompareResponse>();
  const { post: postBacktest, loading: backtesting } = useApiPost<EngineCBacktestResponse>();
  const { post: postExecute, loading: executing } = useApiPost<{ success?: boolean; ticket?: string; error?: string }>();
  const { post: postVision, loading: visionLoading } = useApiPost<ChartAnalysisResponse>();
  const { post: postAiText, loading: textLoading } = useApiPost<AiTextReviewResponse>();
  const { priceFor, ageSecFor, sourceFor } = useLivePrices();

  const runScan = useCallback(async () => {
    setSelected(null);
    const ac = assetClass === 'all' ? '' : assetClass;
    const tokens = scanPairFilter.split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean);
    const body: Record<string, unknown> = { assetClass: ac, style };
    if (tokens.length > 0) body.pairs = tokens;
    const result = await postScan('/api/engine-c-scan', body);
    if (!result || result.error) {
      showToast(`Engine C scan failed: ${result?.error || 'unknown'}`, 'error');
      return;
    }
    setScanResult(result);
    const counts = (['aligned', 'a_only', 'b_only', 'conflict', 'skipped'] as BucketKey[]).map(
      (b) => `${BUCKET_LABELS[b]}=${(result[b] || []).length}`,
    );
    showToast(`Engine C: ${counts.join(' · ')}`, 'success');
    if ((result.aligned || []).length === 0) {
      const fallback: BucketKey = (['a_only', 'b_only', 'conflict', 'skipped'] as BucketKey[]).find(
        (b) => (result[b] || []).length > 0,
      ) || 'aligned';
      setActiveBucket(fallback);
    } else {
      setActiveBucket('aligned');
    }
  }, [assetClass, style, scanPairFilter, postScan, showToast]);

  const runCompareBulk = useCallback(async () => {
    const tokens = comparePairBulk.split(/[,;\n\r]+/).map((s) => s.trim()).filter(Boolean);
    if (!tokens.length) {
      showToast('Enter comma-separated symbols for bulk compare.', 'info');
      return;
    }
    setBulkCompareResults([]);
    const out: CompareResponse[] = [];
    for (const tok of tokens) {
      const seed = { symbol: tok, pair: tok, display: tok };
      const row = await postCompare('/api/compare-engines', { signal: seed, style: compareStyle });
      if (row && !row.error) out.push(row);
    }
    setBulkCompareResults(out);
    showToast(`Compare finished: ${out.length}/${tokens.length} OK`, out.length ? 'success' : 'error');
  }, [comparePairBulk, compareStyle, postCompare, showToast]);

  const runCompare = useCallback(async () => {
    if (!comparePair) return;
    const seed = { symbol: comparePair, pair: comparePair, display: comparePair };
    const result = await postCompare('/api/compare-engines', { signal: seed, style: compareStyle });
    if (!result || result.error) {
      showToast(`Compare failed: ${result?.error || 'unknown'}`, 'error');
      return;
    }
    setCompareResult(result);
    showToast(`Compare ${comparePair}: ${result.summary?.verdict || 'done'}`, 'success');
  }, [comparePair, compareStyle, postCompare, showToast]);

  const runBacktest = useCallback(async () => {
    if (!btPair) return;
    const result = await postBacktest('/api/backtest-consensus', { pair: btPair });
    if (!result || result.error) {
      showToast(`Backtest failed: ${result?.error || 'unknown'}`, 'error');
      return;
    }
    setBtResult(result);
    showToast(`Backtest ${btPair}: ${result.trades ?? 0} trades`, 'success');
  }, [btPair, postBacktest, showToast]);

  // Reset AI review when selected row changes
  const handleSelect = useCallback((row: EngineCConsensusRow) => {
    setSelected(row);
    setAiReview(null);
    setAiTextReview(null);
  }, []);

  // Execution
  const requestExecute = useCallback(
    (row: EngineCConsensusRow, pipMode: 'scalp' | 'intraday' | 'swing') => {
      setPendingStyle(pipMode);
      setConfirmRow(row);
    },
    [],
  );

  const onConfirmExecute = useCallback(async () => {
    if (!confirmRow) return;
    const display = confirmRow.display || confirmRow.symbol || confirmRow.pair || '';
    const engineB = (confirmRow.engine_b_raw || {}) as Record<string, unknown>;
    const rowSizing = toNum(confirmRow.sizing_override, 1);
    const combined = Math.max(0.25, Math.min(1.0, sizingOverride * rowSizing));
    const payload = {
      signal: {
        pair: display,
        display,
        symbol: confirmRow.symbol || display,
        type: confirmRow.type,
        direction: confirmRow.direction,
        price: confirmRow.entry,
        sl: confirmRow.sl,
        tp1: confirmRow.tp,
        tp2: confirmRow.tp,
        style: pendingStyle,
        confluenceScore: confirmRow.conviction,
        ts: new Date().toISOString(),
      },
      engine_b: engineB,
      pip_mode: pendingStyle,
      sizing_override: combined,
    };
    const result = await postExecute('/api/quick-execute', payload as unknown as Record<string, unknown>);
    if (result) {
      if (result.success || result.ticket) {
        showToast(`Engine C: ${confirmRow.direction} ${display} executed (${pendingStyle.toUpperCase()}) - ticket ${result.ticket || '?'}`, 'success');
      } else {
        showToast(`Error: ${result.error || 'Unknown'}`, 'error');
      }
    } else {
      showToast('Execution failed', 'error');
    }
    setConfirmRow(null);
  }, [confirmRow, pendingStyle, sizingOverride, postExecute, showToast]);

  // AI Vision Review
  const runAiReview = useCallback(
    async (row: EngineCConsensusRow | null) => {
      if (!row) return;
      const sym = row.symbol || row.display || row.pair;
      if (!sym) { showToast('No symbol for AI review', 'error'); return; }
      try {
        const candlePayload = await fetchVisionCandlePayload(sym);
        const result = await postVision('/api/chart-analysis', {
          symbol: sym,
          tf: 'H4',
          signal: { direction: row.direction, entry: row.entry, sl: row.sl, tp: row.tp, type: row.type },
          engineB: row.engine_b_raw || {},
          server_render: true,
          chart_source: 'engine_c_panel',
          ...candlePayload,
          entry: row.entry,
          sl: row.sl,
          tp: row.tp,
        });
        if (!result || result.error) {
          showToast(`AI Review failed: ${result?.error || 'unknown'}`, 'error');
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

  // AI Text Review
  const runAiTextReview = useCallback(
    async (row: EngineCConsensusRow | null) => {
      if (!row) return;
      const sym = row.symbol || row.display || row.pair;
      if (!sym) { showToast('No symbol for AI review', 'error'); return; }
      try {
        const sig = {
          symbol: sym,
          pair: row.display || sym,
          display: row.display || sym,
          engine: 'engine_c',
          engine_source: 'engine_c',
          direction: row.direction,
          price: row.entry,
          entry: row.entry,
          sl: row.sl,
          tp: row.tp,
          tp1: row.tp,
          tp2: row.tp,
          rr: row.rr,
          rr1: row.rr,
          rr2: row.rr,
          type: row.type,
          confluenceScore: row.conviction,
          maxScore: 1,
          conviction: row.conviction,
          combinedConviction: row.conviction,
          decision_state: row.decision_state,
          tier: row.tier,
          components: row.components,
          verdict: row.verdict,
          engine_c: {
            decision_state: row.decision_state,
            conviction: row.conviction,
            tier: row.tier,
            components: row.components,
            verdict: row.verdict,
          },
          engine_a: row.engine_a_raw || undefined,
          engine_b: row.engine_b_raw || undefined,
          style: row.style || pendingStyle,
        };
        const result = await postAiText('/api/analyze', {
          signal: sig,
          stylePreference: row.style || 'auto',
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
    [postAiText, pendingStyle, showToast],
  );

  const buckets: Record<BucketKey, EngineCConsensusRow[]> = {
    aligned: scanResult?.aligned || [],
    a_only: scanResult?.a_only || [],
    b_only: scanResult?.b_only || [],
    conflict: scanResult?.conflict || [],
    skipped: scanResult?.skipped || [],
  };

  const groupedRows = useMemo(() => groupRows(buckets[activeBucket]), [buckets, activeBucket]);

  const equityChart = useMemo(() => {
    const ec = btResult?.equity_curve;
    if (!Array.isArray(ec) || ec.length === 0) return [] as { idx: number; equity: number }[];
    if (typeof ec[0] === 'number') {
      return (ec as number[]).map((equity, idx) => ({ idx, equity }));
    }
    return (ec as { idx?: number; equity: number; date?: string }[]).map((p, idx) => ({
      idx: typeof p.idx === 'number' ? p.idx : idx,
      equity: Number(p.equity) || 0,
    }));
  }, [btResult?.equity_curve]);

  return (
    <div className="space-y-5">
      {/* Description */}
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-4">
          <div className="flex items-center gap-2 mb-2">
            <GitMerge className="w-4 h-4 text-primary" />
            <h3 className="text-sm font-semibold">Engine C — Consensus Layer</h3>
          </div>
          <p className="text-xs text-muted-foreground">
            Combines Engine A (factor scoring) and Engine B (naked structure) into a single decision per pair.
            Outputs verdicts <span className="font-mono">ALIGNED</span> / <span className="font-mono">A_ONLY</span> /
            <span className="font-mono"> B_ONLY</span> / <span className="font-mono">CONFLICT</span> /
            <span className="font-mono"> SKIPPED</span>, with regime-blended weights, structural SL/TP, conviction tiers and
            optional Vision overlay.
          </p>
        </CardContent>
      </Card>

      {/* Run scan */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
            <Play className="w-4 h-4 text-primary" />
            Run Consensus Scan
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <Select value={assetClass} onValueChange={setAssetClass}>
              <SelectTrigger className="w-[140px] h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {ASSET_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={style} onValueChange={setStyle}>
              <SelectTrigger className="w-[120px] h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {STYLE_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <Button size="sm" className="h-8 gap-1 text-xs" onClick={runScan} disabled={scanning}>
              <Zap className={scanning ? 'w-3.5 h-3.5 animate-pulse' : 'w-3.5 h-3.5'} />
              {scanning ? 'Engine C scanning…' : 'Engine C Scan'}
            </Button>
          </div>
          <div className="space-y-1">
            <p className="text-[10px] uppercase text-muted-foreground">Restrict to pairs (optional)</p>
            <Textarea
              value={scanPairFilter}
              onChange={(e) => setScanPairFilter(e.target.value)}
              placeholder="Comma or space separated, e.g. EUR/USD, GBP/USD, XAU/USD — empty = entire asset class"
              className="min-h-[56px] text-xs font-mono"
            />
          </div>

          {scanError && <ErrorBanner message={scanError} onRetry={runScan} />}

          {/* Bucket counts */}
          {scanResult && (
            <div className="grid grid-cols-5 gap-2">
              {(['aligned', 'a_only', 'b_only', 'conflict', 'skipped'] as BucketKey[]).map((b) => (
                <button
                  type="button"
                  key={b}
                  className={`rounded-md p-2 text-left border transition-colors ${
                    activeBucket === b ? 'border-primary/60 bg-primary/10' : 'border-border/60 bg-muted/30'
                  }`}
                  onClick={() => setActiveBucket(b)}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] uppercase tracking-wider text-muted-foreground">{BUCKET_LABELS[b]}</span>
                    <Badge className={`${bucketBadgeClass(b)} text-[10px]`}>{buckets[b].length}</Badge>
                  </div>
                </button>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Buckets list + selected detail */}
      {scanResult && (
        <div className="grid grid-cols-5 gap-4">
          <Card className="col-span-3 border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center justify-between uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <span>{BUCKET_LABELS[activeBucket]} ({buckets[activeBucket].length})</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[480px] pr-2">
                {buckets[activeBucket].length === 0 ? (
                  <div className="text-xs text-muted-foreground text-center py-12">
                    No rows in {BUCKET_LABELS[activeBucket]}
                  </div>
                ) : (
                  <div className="space-y-4">
                    {groupedRows.map(({ key, rows }) => (
                      <div key={key} className="space-y-2">
                        <div className="flex items-center justify-between sticky top-0 z-[1] bg-card/95 backdrop-blur-sm py-1 border-b border-border/50">
                          <span className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
                            {formatGroupLabel(key)}
                          </span>
                          <Badge variant="outline" className="text-[9px] font-mono">
                            {rows.length} · conv {fmtNum(rows[0]?.conviction, 2)}
                          </Badge>
                        </div>
                        <div className="space-y-2">
                          {rows.map((row, i) => (
                            <ConsensusRowCard
                              key={`${row.display || row.symbol || row.pair || key}-${i}`}
                              row={row}
                              livePrice={priceFor(row)}
                              livePriceAgeSec={ageSecFor(row)}
                              livePriceSource={sourceFor(row)}
                              bucket={activeBucket}
                              selected={selected === row}
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

          <Card className="col-span-2 border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <Activity className="w-4 h-4 text-primary" />
                Consensus Detail
              </CardTitle>
            </CardHeader>
            <CardContent>
              {selected ? (
                <ScrollArea className="h-[600px] pr-2">
                  <ConsensusDetail
                    row={{
                      ...selected,
                      livePrice: priceFor(selected),
                      livePriceAgeSec: ageSecFor(selected),
                      livePriceSource: sourceFor(selected),
                    }}
                    onExecute={requestExecute}
                    executing={executing}
                    sizingOverride={sizingOverride}
                    onSizingChange={setSizingOverride}
                    aiReview={aiReview}
                    aiTextReview={aiTextReview}
                    aiReviewMode={aiReviewMode}
                    onAiReviewModeChange={setAiReviewMode}
                    onRunVision={runAiReview}
                    onRunText={runAiTextReview}
                    visionLoading={visionLoading}
                    textLoading={textLoading}
                  />
                </ScrollArea>
              ) : (
                <div className="flex flex-col items-center justify-center h-[600px] text-muted-foreground">
                  <Layers className="w-8 h-8 mb-3 opacity-40" />
                  <p className="text-sm">Select a row to inspect consensus components</p>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* Compare engines per pair */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
            <Eye className="w-4 h-4 text-primary" />
            Compare Engines (single pair)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <Select value={comparePair} onValueChange={setComparePair}>
              <SelectTrigger className="w-[180px] h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {(allPairs || []).slice(0, 200).map((p) => (
                  <SelectItem key={p.symbol || p.display} value={p.symbol || p.display}>
                    {p.display || p.symbol}
                  </SelectItem>
                ))}
                {(allPairs || []).length === 0 && (
                  <SelectItem value="EURUSD">EURUSD</SelectItem>
                )}
              </SelectContent>
            </Select>
            <Select value={compareStyle} onValueChange={setCompareStyle}>
              <SelectTrigger className="w-[120px] h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {STYLE_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <Button size="sm" className="h-8 gap-1 text-xs" onClick={runCompare} disabled={comparing}>
              {comparing ? 'Comparing…' : 'Run Compare'}
            </Button>
          </div>

          {compareResult && (
            <div className="grid grid-cols-3 gap-3">
              <div className="p-3 rounded-md bg-muted/30">
                <p className="text-[10px] uppercase text-muted-foreground">Verdict</p>
                <p className={`text-sm font-mono font-bold mt-1 ${
                  compareResult.summary?.verdict === 'ALIGNED' ? 'text-long'
                  : compareResult.summary?.verdict === 'CONFLICT' ? 'text-short'
                  : 'text-foreground'
                }`}>
                  {compareResult.summary?.verdict || '—'}
                </p>
                <div className="mt-2 text-[10px] space-y-1 text-muted-foreground">
                  <div>Same direction: {compareResult.summary?.sameDirection ? 'YES' : 'no'}</div>
                  <div>Structure aligned: {compareResult.summary?.structureAligned ? 'YES' : 'no'}</div>
                  <div>Macro aligned: {compareResult.summary?.macroAligned ? 'YES' : 'no'}</div>
                  <div>Engine A style: {compareResult.summary?.engineAStyle || '—'}</div>
                  <div>Engine B style: {compareResult.summary?.engineBStyle || '—'}</div>
                </div>
              </div>
              <div className="p-3 rounded-md bg-muted/30">
                <p className="text-[10px] uppercase text-muted-foreground">Engine A</p>
                {compareResult.engineA ? (
                  <div className="text-xs space-y-1 mt-1">
                    <div>
                      <span className="font-mono font-bold">{compareResult.engineA.direction || '—'}</span>
                      <span className="ml-2 text-muted-foreground">
                        {fmtNum(compareResult.engineA.confluenceScore ?? compareResult.engineA.score, 2)}
                        {' / '}
                        {fmtNum(compareResult.engineA.maxScore ?? compareResult.engineA.maxScoreOverride, 1)}
                      </span>
                    </div>
                    <div className="text-[10px] text-muted-foreground">
                      Entry {fmtPrice(compareResult.engineA.entry ?? compareResult.engineA.price, comparePair, compareResult.engineA.type)} ·
                      SL {fmtPrice(compareResult.engineA.sl, comparePair, compareResult.engineA.type)} ·
                      TP {fmtPrice(compareResult.engineA.tp ?? compareResult.engineA.tp1, comparePair, compareResult.engineA.type)}
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground mt-1">No A signal</p>
                )}
              </div>
              <div className="p-3 rounded-md bg-muted/30">
                <p className="text-[10px] uppercase text-muted-foreground">Engine B</p>
                {compareResult.engineB ? (
                  <div className="text-xs space-y-1 mt-1">
                    <div>
                      <span className="font-mono font-bold">{compareResult.engineB.direction || '—'}</span>
                      <span className="ml-2 text-muted-foreground">
                        {fmtNum(compareResult.engineB.score, 2)}
                        {' (passed: '}
                        {compareResult.engineB.passed ? 'yes' : 'no'}
                        {')'}
                      </span>
                    </div>
                    <div className="text-[10px] text-muted-foreground">
                      {compareResult.summary?.engineBMinScore != null && `min: ${fmtNum(compareResult.summary.engineBMinScore, 2)} · `}
                      RR {fmtNum(compareResult.engineB.rr, 2)}
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground mt-1">No B signal</p>
                )}
              </div>
            </div>
          )}

          <div className="border-t border-border/40 pt-3 mt-2 space-y-2">
            <p className="text-[10px] uppercase text-muted-foreground">Bulk compare (one request per symbol)</p>
            <Textarea
              value={comparePairBulk}
              onChange={(e) => setComparePairBulk(e.target.value)}
              placeholder="EUR/USD, GBP/USD, XAU/USD ..."
              className="min-h-[52px] text-xs font-mono"
            />
            <Button size="sm" variant="outline" className="h-8 text-xs" onClick={runCompareBulk} disabled={comparing}>
              Run bulk compare
            </Button>
            {bulkCompareResults.length > 0 && (
              <ScrollArea className="h-[180px] border border-border/40 rounded-md">
                <table className="w-full text-left text-[11px]">
                  <thead>
                    <tr className="border-b border-border/40 bg-muted/30">
                      <th className="p-2 text-muted-foreground">#</th>
                      <th className="p-2 text-muted-foreground">Verdict</th>
                      <th className="p-2 text-muted-foreground">A dir</th>
                      <th className="p-2 text-muted-foreground">B dir</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bulkCompareResults.map((r, i) => (
                      <tr key={i} className="border-b border-border/20">
                        <td className="p-2 font-mono">{i + 1}</td>
                        <td className="p-2 font-mono">{r.summary?.verdict || '—'}</td>
                        <td className="p-2 font-mono">{r.engineA?.direction || '—'}</td>
                        <td className="p-2 font-mono">{r.engineB?.direction || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ScrollArea>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Backtest */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
            <BarChart3 className="w-4 h-4 text-primary" />
            Engine C Backtest
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <Input
              value={btPair}
              onChange={(e) => setBtPair(e.target.value.toUpperCase())}
              className="w-32 h-8 text-xs font-mono"
              placeholder="Pair"
            />
            <Button size="sm" className="h-8 gap-1 text-xs" onClick={runBacktest} disabled={backtesting}>
              {backtesting ? 'Backtesting…' : 'Run Backtest'}
            </Button>
          </div>
          {backtesting && <Skeleton className="h-32 w-full" />}
          {btResult && !backtesting && (
            <div className="space-y-3">
              <div className="grid grid-cols-6 gap-3">
                <KpiBox title="Trades" value={btResult.trades ?? 0} />
                <KpiBox title="Win rate" value={btResult.win_rate != null ? `${fmtNum(btResult.win_rate, 1)}%` : '—'} />
                <KpiBox
                  title="Profit factor"
                  value={btResult.profit_factor == null ? '—' : fmtNum(btResult.profit_factor, 2)}
                  accent={(btResult.profit_factor ?? 0) >= 1.2 ? 'text-long' : 'text-warning'}
                />
                <KpiBox title="SQN" value={btResult.sqn == null ? '—' : fmtNum(btResult.sqn, 2)} />
                <KpiBox title="Sharpe" value={btResult.sharpe == null ? '—' : fmtNum(btResult.sharpe, 2)} />
                <KpiBox title="Max DD" value={btResult.max_dd_pct == null ? '—' : `${fmtNum(btResult.max_dd_pct, 1)}%`} />
              </div>
              {equityChart.length > 0 && (
                <div className="h-[220px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={equityChart} margin={{ left: 0, right: 0, top: 4, bottom: 0 }}>
                      <defs>
                        <linearGradient id="ecBtFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.45} />
                          <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <XAxis dataKey="idx" hide />
                      <YAxis domain={['auto', 'auto']} tick={{ fontSize: 10 }} width={50} />
                      <Tooltip
                        contentStyle={{ background: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', fontSize: 11 }}
                        formatter={(v: number) => [`$${fmtNum(v, 2)}`, 'Equity']}
                      />
                      <Area type="monotone" dataKey="equity" stroke="hsl(var(--primary))" fill="url(#ecBtFill)" strokeWidth={2} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              )}
              {btResult.notes && <p className="text-[10px] text-muted-foreground">{btResult.notes}</p>}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Execution confirmation dialog */}
      <AlertDialog open={!!confirmRow} onOpenChange={(open) => { if (!open) setConfirmRow(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Confirm Execution - {pendingStyle.toUpperCase()}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-xs">
                <div>
                  Execute <b>{confirmRow?.direction}</b> {confirmRow?.display || confirmRow?.symbol} at{' '}
                  <span className="font-mono">{fmtPrice(confirmRow?.entry, confirmRow?.pair, confirmRow?.type)}</span>
                </div>
                <div className="font-mono">
                  SL: {fmtPrice(confirmRow?.sl, confirmRow?.pair, confirmRow?.type)} ·
                  TP: {fmtPrice(confirmRow?.tp, confirmRow?.pair, confirmRow?.type)} ·
                  RR: {fmtNum(confirmRow?.rr, 2)}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  Sizing: <span className="font-mono">{fmtNum(sizingOverride, 2)}x</span> ·
                  Tier: {confirmRow?.tier || '—'} · Conviction: {fmtNum(confirmRow?.conviction, 2)}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  Backend will recompute SL/TP for the selected style via /api/quick-execute (recompute_levels_for_style),
                  apply risk_check, and route to MT5 or Bybit.
                </div>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={onConfirmExecute} disabled={executing}>
              {executing ? 'Executing...' : `Confirm ${pendingStyle.toUpperCase()}`}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ConsensusRowCard({
  row, livePrice, livePriceAgeSec, livePriceSource, bucket, selected, onSelect,
}: {
  row: EngineCConsensusRow;
  livePrice?: number;
  livePriceAgeSec?: number;
  livePriceSource?: string;
  bucket: BucketKey;
  selected: boolean;
  onSelect: (r: EngineCConsensusRow) => void;
}) {
  const dir = String(row.direction || '').toUpperCase();
  const long = dir === 'LONG' || dir === 'BUY';
  const conviction = toNum(row.conviction);
  const aNorm = row.components?.a_norm;
  const bNorm = row.components?.b_norm;
  const tier = row.tier || 'SKIP';
  const verdict = row.verdict || '—';
  const display = row.display || row.symbol || row.pair || '—';
  const live = toNum(livePrice);

  return (
    <button
      type="button"
      className={`w-full text-left p-3 rounded-md border transition-colors ${
        selected ? 'border-primary/60 bg-primary/10' : 'border-border/60 bg-muted/30 hover:bg-muted/50'
      }`}
      onClick={() => onSelect(row)}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-mono font-bold truncate">{display}</span>
          {row.direction && (
            <Badge className={long ? 'badge-long' : 'badge-short'}>{dir}</Badge>
          )}
          <Badge variant="outline" className="text-[10px] uppercase">{verdict}</Badge>
        </div>
        <div className="flex items-center gap-2">
          <Badge className={`${tierBadgeClass(tier)} text-[10px]`}>{tier}</Badge>
          <Badge className={`${bucketBadgeClass(bucket)} text-[10px]`}>{BUCKET_LABELS[bucket]}</Badge>
        </div>
      </div>

      <div className="mt-2 grid grid-cols-4 gap-2 text-[10px] text-muted-foreground">
        <div>conv {fmtNum(conviction, 2)}</div>
        <div>A {fmtNum(aNorm, 2)}</div>
        <div>B {fmtNum(bNorm, 2)}</div>
        <div className="text-primary">
          live {live > 0 ? fmtPrice(live, row.pair, row.type) : '—'}
          <span className="block text-[9px] text-muted-foreground">
            {fmtLiveQuoteMeta(livePriceAgeSec, livePriceSource)}
          </span>
        </div>
      </div>

      {bucket === 'skipped' && (row.reason || row.detail) && (
        <p className="mt-1 text-[10px] text-muted-foreground italic truncate">
          {row.reason || row.detail}
        </p>
      )}
      {row.decision_state_reason && bucket !== 'skipped' && (
        <p className="mt-1 text-[10px] text-warning truncate">
          {row.decision_state}: {row.decision_state_reason}
        </p>
      )}
    </button>
  );
}

function ConsensusDetail({
  row,
  onExecute,
  executing,
  sizingOverride,
  onSizingChange,
  aiReview,
  aiTextReview,
  aiReviewMode,
  onAiReviewModeChange,
  onRunVision,
  onRunText,
  visionLoading,
  textLoading,
}: {
  row: EngineCConsensusRow;
  onExecute: (row: EngineCConsensusRow, style: 'scalp' | 'intraday' | 'swing') => void;
  executing: boolean;
  sizingOverride: number;
  onSizingChange: (v: number) => void;
  aiReview: ChartAnalysisResponse | null;
  aiTextReview: AiTextReviewResponse | null;
  aiReviewMode: 'vision' | 'text';
  onAiReviewModeChange: (m: 'vision' | 'text') => void;
  onRunVision: (row: EngineCConsensusRow) => void;
  onRunText: (row: EngineCConsensusRow) => void;
  visionLoading: boolean;
  textLoading: boolean;
}) {
  const display = row.display || row.symbol || row.pair || '—';
  const aNorm = row.components?.a_norm;
  const bNorm = row.components?.b_norm;
  return (
    <div className="space-y-3">
      <div className="p-3 rounded-md bg-muted/30 space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-sm font-mono font-bold">{display}</p>
          <Badge variant="outline" className="text-[10px]">{row.verdict}</Badge>
        </div>
        <div className="text-[10px] text-muted-foreground">
          Direction: {row.direction || '—'} ·
          Tier: {row.tier || '—'} ·
          Decision: {row.decision_state || '—'}
          {row.decision_state_reason ? ` (${row.decision_state_reason})` : ''}
        </div>
      </div>

      {/* Execute by Style toolbar */}
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-3 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[10px] uppercase text-muted-foreground">Execute by Style</p>
            <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
              <span>Sizing</span>
              <Input
                type="number"
                min={0.25}
                max={1}
                step={0.25}
                value={sizingOverride}
                onChange={(e) => onSizingChange(Math.max(0.25, Math.min(1.0, Number(e.target.value) || 1.0)))}
                className="w-16 h-7 text-xs font-mono text-center"
              />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              className="h-9 gap-1 text-xs hover:bg-warning/10"
              onClick={() => onExecute(row, 'scalp')}
              disabled={executing || !row.direction}
            >
              <Clock className="w-3.5 h-3.5" />
              Scalp
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-9 gap-1 text-xs hover:bg-primary/10"
              onClick={() => onExecute(row, 'intraday')}
              disabled={executing || !row.direction}
            >
              <Sun className="w-3.5 h-3.5" />
              Intraday
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-9 gap-1 text-xs hover:bg-long/10"
              onClick={() => onExecute(row, 'swing')}
              disabled={executing || !row.direction}
            >
              <Moon className="w-3.5 h-3.5" />
              Swing
            </Button>
          </div>
          {!row.direction && (
            <p className="text-[10px] text-warning">No direction — execution disabled</p>
          )}
        </CardContent>
      </Card>

      {/* AI Review actions */}
      <div className="flex items-center gap-2 flex-wrap">
        <Button
          size="sm"
          variant="outline"
          className="h-8 text-xs gap-1"
          onClick={() => onRunVision(row)}
          disabled={visionLoading}
        >
          <Eye className={visionLoading ? 'w-3.5 h-3.5 animate-pulse' : 'w-3.5 h-3.5'} />
          {visionLoading ? 'AI Vision...' : 'AI Review (Vision)'}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-8 text-xs gap-1"
          onClick={() => onRunText(row)}
          disabled={textLoading}
        >
          <FileText className={textLoading ? 'w-3.5 h-3.5 animate-pulse' : 'w-3.5 h-3.5'} />
          {textLoading ? 'AI Text...' : 'AI Review (Text)'}
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

      {/* AI Review mode toggle */}
      {(aiReview || aiTextReview) && (
        <div className="flex items-center gap-1 bg-muted/30 rounded-md p-1 w-fit">
          <button
            type="button"
            onClick={() => onAiReviewModeChange('vision')}
            className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
              aiReviewMode === 'vision' ? 'bg-background shadow-sm text-foreground' : 'text-muted-foreground hover:text-foreground'
            }`}
            disabled={!aiReview}
          >
            Vision
          </button>
          <button
            type="button"
            onClick={() => onAiReviewModeChange('text')}
            className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
              aiReviewMode === 'text' ? 'bg-background shadow-sm text-foreground' : 'text-muted-foreground hover:text-foreground'
            }`}
            disabled={!aiTextReview}
          >
            Text
          </button>
        </div>
      )}

      {/* AI Vision Review output */}
      {aiReview && aiReviewMode === 'vision' && (
        <VisionReviewCard vision={aiReview} />
      )}

      {/* AI Text Review (Marcus Reid) output */}
      {aiTextReview && aiReviewMode === 'text' && (
        <Card className="border-border/60 bg-card/50">
          <CardContent className="p-3 space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-[10px] uppercase text-muted-foreground">AI Text Review - Marcus Reid</p>
              <span className="text-[10px] text-muted-foreground font-mono">
                {aiTextReview.grade || '-'} - edge {aiTextReview.edgeProbability ?? '-'}%
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

      {/* Data tabs */}
      <Tabs defaultValue="levels" className="w-full">
        <TabsList className="grid grid-cols-4 w-full">
          <TabsTrigger value="levels">Levels</TabsTrigger>
          <TabsTrigger value="components">A/B</TabsTrigger>
          <TabsTrigger value="weights">Weights</TabsTrigger>
          <TabsTrigger value="diag">Diag</TabsTrigger>
        </TabsList>

        <TabsContent value="levels" className="mt-3">
          <div className="grid grid-cols-2 gap-2 text-xs">
            <Row
              k="Live"
              v={fmtPrice(row.livePrice, row.pair, row.type)}
              meta={fmtLiveQuoteMeta(row.livePriceAgeSec, row.livePriceSource)}
            />
            <Row k="Entry" v={fmtPrice(row.entry, row.pair, row.type)} />
            <Row k="SL" v={`${fmtPrice(row.sl, row.pair, row.type)}${row.sl_method ? ` · ${row.sl_method}` : ''}`} accent="short" />
            <Row k="TP" v={`${fmtPrice(row.tp, row.pair, row.type)}${row.tp_method ? ` · ${row.tp_method}` : ''}`} accent="long" />
            <Row k="RR" v={fmtNum(row.rr, 2)} />
            <Row k="ATR" v={fmtNum(row.atr, 6)} />
            <Row k="Conviction" v={fmtNum(row.conviction, 3)} />
            <Row k="Sizing override" v={fmtNum(row.sizing_override, 2)} />
            <Row k="Style" v={String(row.style || '—')} />
            <Row k="Regime" v={String(row.regime || '—')} />
            <Row k="A SL" v={fmtPrice(row.sl_a, row.pair, row.type)} />
            <Row k="B SL" v={fmtPrice(row.sl_b, row.pair, row.type)} />
            <Row k="Score group" v={String(row.scoreGroup || '—')} />
          </div>
        </TabsContent>

        <TabsContent value="components" className="mt-3">
          <div className="grid grid-cols-2 gap-2 text-xs">
            <Row k="A norm" v={fmtNum(aNorm, 3)} />
            <Row k="A direction" v={String(row.components?.a_direction || '—')} />
            <Row k="A has signal" v={row.components?.a_has_signal ? 'yes' : 'no'} />
            <Row k="A COT active" v={row.components?.a_cot_active ? 'yes' : 'no'} />
            <Row k="B norm" v={fmtNum(bNorm, 3)} />
            <Row k="B direction" v={String(row.components?.b_direction || '—')} />
            <Row k="B has signal" v={row.components?.b_has_signal ? 'yes' : 'no'} />
            <Row k="B checklist" v={row.components?.b_checklist_passed ? 'passed' : 'failed'} />
            <Row k="B BOS" v={row.components?.b_bos ? 'yes' : 'no'} />
            <Row k="B OB at zone" v={row.components?.b_ob_at_zone ? 'yes' : 'no'} />
            <Row k="B sequence" v={String(row.components?.b_sequence || '—')} />
            <Row k="B diagnostic" v={String(row.components?.b_signal_diagnostic || '—')} />
          </div>
        </TabsContent>

        <TabsContent value="weights" className="mt-3 space-y-2">
          {row.engine_weights && Object.keys(row.engine_weights).length > 0 ? (
            <div className="grid grid-cols-2 gap-2 text-xs">
              {Object.entries(row.engine_weights).map(([k, v]) => (
                <Row key={k} k={k} v={fmtNum(v, 3)} />
              ))}
            </div>
          ) : (
            <p className="text-[11px] text-muted-foreground">No weights returned for this verdict.</p>
          )}
          {row.engine_base_weights && Object.keys(row.engine_base_weights).length > 0 && (
            <>
              <p className="text-[10px] uppercase text-muted-foreground mt-2">Base (regime)</p>
              <div className="grid grid-cols-2 gap-2 text-xs">
                {Object.entries(row.engine_base_weights).map(([k, v]) => (
                  <Row key={`b-${k}`} k={k} v={fmtNum(v, 3)} />
                ))}
              </div>
            </>
          )}
          <p className="text-[10px] text-muted-foreground mt-2">
            Intermarket multiplier: <span className="font-mono">{fmtNum(row.intermarket_multiplier, 3)}</span>
          </p>
        </TabsContent>

        <TabsContent value="diag" className="mt-3 space-y-2">
          {row.opposing_high_confidence && (
            <p className="text-[11px] text-warning">Opposing high-confidence signal flagged.</p>
          )}
          {row.vision_applied && (
            <div className="text-[11px]">
              Vision: <span className="font-mono">{row.vision_action || '—'}</span> · rating{' '}
              <span className="font-mono">{row.vision_rating || '—'}</span>
              {row.vision_sl_flag && <span> · SL {row.vision_sl_flag}</span>}
              {row.vision_tp_flag && <span> · TP {row.vision_tp_flag}</span>}
            </div>
          )}
          {row.disagreement_diagnosis && Object.keys(row.disagreement_diagnosis).length > 0 && (
            <pre className="text-[10px] font-mono whitespace-pre-wrap p-2 bg-muted/20 rounded border border-border/40 max-h-72 overflow-y-auto">
              {JSON.stringify(row.disagreement_diagnosis, null, 2)}
            </pre>
          )}
          {row.intermarket_confirmation && Object.keys(row.intermarket_confirmation).length > 0 && (
            <details className="text-[10px]">
              <summary className="cursor-pointer text-muted-foreground">Intermarket confirmation</summary>
              <pre className="font-mono whitespace-pre-wrap p-2 bg-muted/20 rounded border border-border/40 max-h-72 overflow-y-auto mt-1">
                {JSON.stringify(row.intermarket_confirmation, null, 2)}
              </pre>
            </details>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

function Row({ k, v, accent, meta }: { k: string; v: string | number | null | undefined; accent?: 'long' | 'short'; meta?: string }) {
  const cls = accent === 'long' ? 'text-long' : accent === 'short' ? 'text-short' : 'text-foreground';
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[10px] uppercase text-muted-foreground">{k}</span>
      <span className={`font-mono text-right ${cls}`}>
        {v == null || v === '' ? '—' : v}
        {meta && <span className="block text-[9px] text-muted-foreground">{meta}</span>}
      </span>
    </div>
  );
}

function KpiBox({ title, value, accent }: { title: string; value: string | number; accent?: string }) {
  return (
    <div className="p-3 rounded-md bg-muted/30">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{title}</p>
      <p className={`text-lg font-mono font-bold mt-1 ${accent || ''}`}>{value}</p>
    </div>
  );
}
