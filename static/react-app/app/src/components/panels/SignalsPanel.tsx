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
import { Search, Zap, Filter, Layers, Activity, Eye, Clock, Sun, Moon, FileText } from 'lucide-react';
import { fmtNum, toNum } from '@/lib/utils';
import { fmtPrice } from '@/lib/athenaFormat';
import { EngineASignalCard, EngineBChecklistCard } from '@/components/athena';
import type { EngineASignal, ScanResponse, NakedScanResponse, ChartAnalysisResponse, AiTextReviewResponse } from '@/types/athena';

type ScanSource = 'A' | 'B';

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

function compareSignals(a: EngineASignal, b: EngineASignal, sortBy: string): number {
  if (sortBy === 'conviction') return toNum(b.conviction) - toNum(a.conviction);
  if (sortBy === 'rr') return toNum(b.rr ?? b.rr1) - toNum(a.rr ?? a.rr1);
  if (sortBy === 'time') return new Date(b.timestamp || 0).getTime() - new Date(a.timestamp || 0).getTime();
  return toNum(b.confluenceScore ?? b.score) - toNum(a.confluenceScore ?? a.score);
}

export default function SignalsPanel() {
  const { showToast, isTestMode } = useStore();
  const [filter, setFilter] = useState('');
  const [directionFilter, setDirectionFilter] = useState<string>('all');
  const [assetClass, setAssetClass] = useState<string>('forex');
  const [style, setStyle] = useState<string>('auto');
  const [sortBy, setSortBy] = useState<string>('score');
  const [scanSource, setScanSource] = useState<ScanSource>('A');
  const [selected, setSelected] = useState<EngineASignal | null>(null);
  const [confirmSig, setConfirmSig] = useState<EngineASignal | null>(null);
  const [pendingStyle, setPendingStyle] = useState<'scalp' | 'intraday' | 'swing' | 'auto'>('auto');
  const [sizingOverride, setSizingOverride] = useState<number>(1.0);

  // Hot-cached snapshot from server-side last scan (Engine A only — matches Athena behaviour).
  const { data: lastScan, loading: lastLoading, error: lastError, refresh: refreshLast } =
    useApiPoll<ScanResponse>('/api/last-scan', 30_000);

  const { data: autoTrade } = useApiPoll<{ enabled: boolean }>('/api/auto-trade', 30_000);
  const { post: postScanA, loading: scanningA } = useApiPost<ScanResponse>();
  const { post: postScanB, loading: scanningB } = useApiPost<NakedScanResponse>();
  const { post: postExecute, loading: executing } = useApiPost<{
    success?: boolean;
    ticket?: string;
    error?: string;
    approval?: { approved: boolean; reason: string };
  }>();
  const { post: postVision, loading: visionLoading } = useApiPost<ChartAnalysisResponse>();
  const { post: postAiText, loading: textLoading } = useApiPost<AiTextReviewResponse>();
  const [aiReview, setAiReview] = useState<ChartAnalysisResponse | null>(null);
  const [aiTextReview, setAiTextReview] = useState<AiTextReviewResponse | null>(null);
  const [aiReviewMode, setAiReviewMode] = useState<'vision' | 'text'>('vision');
  const { priceFor } = useLivePrices(3000);

  // Local scan results override (so each engine button replaces the live view).
  const [localScan, setLocalScan] = useState<{ source: ScanSource; signals: EngineASignal[] } | null>(null);
  const baseSignals: EngineASignal[] = localScan?.signals
    ?? (Array.isArray(lastScan?.signals) ? (lastScan!.signals as EngineASignal[]) : []);
  const baseSource: ScanSource = localScan?.source ?? 'A';
  const liveSignals = useMemo(
    () => baseSignals.map((s) => ({ ...s, livePrice: priceFor(s) })),
    [baseSignals, priceFor],
  );

  const filtered = useMemo(() => {
    const list = liveSignals.filter((s) => {
      const pair = (s.display || s.pair || s.symbol || '').toLowerCase();
      if (filter && !pair.includes(filter.toLowerCase())) return false;
      if (directionFilter !== 'all' && s.direction !== directionFilter) return false;
      if (assetClass !== 'all' && (s.type || '').toLowerCase() !== assetClass) return false;
      return true;
    });
    list.sort((a, b) => compareSignals(a, b, sortBy));
    return list;
  }, [liveSignals, filter, directionFilter, assetClass, sortBy]);

  /** Signal feed: grouped by scoreGroup (Engine A) or asset type; sorted inside each group; groups ordered by best score first. */
  const groupedSignals = useMemo(() => {
    const map = new Map<string, EngineASignal[]>();
    for (const s of filtered) {
      const k = signalGroupKey(s);
      if (!map.has(k)) map.set(k, []);
      map.get(k)!.push(s);
    }
    const entries = [...map.entries()].map(([key, signals]) => {
      const sorted = [...signals].sort((a, b) => compareSignals(a, b, sortBy));
      const top = sorted.length ? toNum(sorted[0].confluenceScore ?? sorted[0].score, 0) : 0;
      return { key, signals: sorted, topScore: top };
    });
    entries.sort((a, b) => {
      if (b.topScore !== a.topScore) return b.topScore - a.topScore;
      return a.key.localeCompare(b.key);
    });
    return entries;
  }, [filtered, sortBy]);

  const runScan = useCallback(
    async (which: ScanSource) => {
      const ac = assetClass === 'all' ? '' : assetClass;
      const prevSource = scanSource;
      const prevLocal = localScan;
      setScanSource(which);
      try {
        if (which === 'A') {
          const result = await postScanA('/api/scan', { asset_class: ac, force: false, style });
          if (result?.signals) {
            setLocalScan({ source: 'A', signals: result.signals as EngineASignal[] });
            setSortBy('score');
            showToast(
              `Engine A: ${result.signals.length} signals · ${result.pairs_scanned ?? '?'} pairs in ${fmtNum(result.scan_time, 1, '?')}s`,
              'success',
            );
          } else {
            showToast('Engine A scan returned nothing', 'info');
          }
        } else {
          const result = await postScanB('/api/scan-naked', { assetClass: ac, style });
          if (result?.signals) {
            setLocalScan({ source: 'B', signals: result.signals as EngineASignal[] });
            showToast(`Engine B: ${result.signals.length} structural signals`, 'success');
          } else {
            showToast('Engine B scan returned nothing', 'info');
          }
        }
      } catch {
        showToast(`${which === 'A' ? 'Engine A' : 'Engine B'} scan failed`, 'error');
        setScanSource(prevSource);
        setLocalScan(prevLocal);
      }
    },
    [assetClass, style, postScanA, postScanB, showToast, scanSource, localScan],
  );

  const isPaper = autoTrade?.enabled ?? false;
  const executeDisabled = isTestMode;
  const executeLabel = isTestMode ? 'TEST MODE' : isPaper ? 'PAPER MODE' : 'Execute';

  const runAiReview = useCallback(
    async (sig: EngineASignal | null) => {
      if (!sig) return;
      const sym = sig.symbol || sig.pair || sig.display;
      if (!sym) {
        showToast('Selected signal has no symbol', 'error');
        return;
      }
      try {
        const candleRes = await fetch(`/api/candles?symbol=${encodeURIComponent(sym)}&tf=H4&limit=300`);
        const candleJson = await candleRes.json();
        if (!candleRes.ok || !Array.isArray(candleJson?.candles) || candleJson.candles.length === 0) {
          showToast(`AI Review: no H4 candles for ${sym}`, 'error');
          return;
        }
        const result = await postVision('/api/chart-analysis', {
          symbol: sym,
          tf: 'H4',
          signal: sig,
          engineB: sig.naked_data || sig.engine_b,
          server_render: true,
          chart_source: 'signals_panel',
          candles: candleJson.candles,
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

  // Reset AI review whenever the selected signal changes
  const handleSelect = useCallback((s: EngineASignal) => {
    setSelected(s);
    setAiReview(null);
    setAiTextReview(null);
  }, []);

  const requestExecute = useCallback(
    (sig: EngineASignal, modeArg: 'scalp' | 'intraday' | 'swing' | 'auto') => {
      setPendingStyle(modeArg);
      setConfirmSig(sig);
    },
    [],
  );

  const onConfirmExecute = useCallback(async () => {
    if (!confirmSig) return;
    const effectiveStyle = pendingStyle === 'auto'
      ? (confirmSig.style || (style === 'auto' ? 'swing' : style))
      : pendingStyle;
    const nakedData = (confirmSig.naked_data ?? confirmSig.engine_b ?? {}) as Record<string, unknown>;
    // /api/quick-execute matches the legacy "Quick Exec / Style" UI flow:
    //   { signal: <full live signal>, engine_b: {...}, pip_mode, sizing_override }
    // Backend recomputes SL/TP for the chosen pip_mode via recompute_levels_for_style().
    const payload = {
      signal: {
        ...confirmSig,
        symbol: confirmSig.symbol || confirmSig.pair || confirmSig.display,
        pair: confirmSig.pair || confirmSig.display,
        display: confirmSig.display || confirmSig.pair,
        type: confirmSig.type,
        direction: confirmSig.direction,
        price: confirmSig.entry ?? confirmSig.price,
        entry: confirmSig.entry ?? confirmSig.price,
        sl: confirmSig.sl,
        tp1: confirmSig.tp1 ?? confirmSig.tp,
        tp2: confirmSig.tp2 ?? confirmSig.tp,
        style: effectiveStyle,
        source: baseSource === 'A' ? 'engine_a' : 'engine_b',
      },
      engine_b: nakedData,
      pip_mode: effectiveStyle,
      sizing_override: Math.max(0.25, Math.min(1.0, sizingOverride || 1.0)),
    };
    const result = await postExecute('/api/quick-execute', payload as unknown as Record<string, unknown>);
    if (result) {
      if (result.approval && !result.approval.approved) {
        showToast(`Rejected: ${result.approval.reason}`, 'error');
      } else if (result.success || result.ticket) {
        showToast(
          `Executed ${confirmSig.pair || confirmSig.display} (${effectiveStyle.toUpperCase()}) — ticket ${result.ticket || '?'}`,
          'success',
        );
      } else {
        showToast(`Error: ${result.error || 'Unknown'}`, 'error');
      }
    } else {
      showToast('Execution failed', 'error');
    }
    setConfirmSig(null);
    setPendingStyle('auto');
  }, [confirmSig, baseSource, style, pendingStyle, sizingOverride, postExecute, showToast]);

  const sourceLabel = baseSource === 'A' ? 'Engine A' : 'Engine B';
  const lastScanAgeIso = (lastScan as ScanResponse | null)?.scannedAt;

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-3 flex items-center gap-2 flex-wrap">
          <div className="relative flex-1 min-w-[180px]">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <Input
              placeholder="Filter by pair…"
              className="pl-8 h-8 text-xs"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>
          <Select value={assetClass} onValueChange={setAssetClass}>
            <SelectTrigger className="w-[120px] h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
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
            <SelectTrigger className="w-[110px] h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">Auto</SelectItem>
              <SelectItem value="scalp">Scalp</SelectItem>
              <SelectItem value="intraday">Intraday</SelectItem>
              <SelectItem value="swing">Swing</SelectItem>
            </SelectContent>
          </Select>
          <Select value={directionFilter} onValueChange={setDirectionFilter}>
            <SelectTrigger className="w-[110px] h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any direction</SelectItem>
              <SelectItem value="LONG">Long</SelectItem>
              <SelectItem value="SHORT">Short</SelectItem>
            </SelectContent>
          </Select>
          <Select value={sortBy} onValueChange={setSortBy}>
            <SelectTrigger className="w-[120px] h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
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
              variant={scanSource === 'A' ? 'default' : 'outline'}
              className="h-8 gap-1 text-xs"
              onClick={() => runScan('A')}
              disabled={scanningA || scanningB}
            >
              <Zap className={scanningA ? 'w-3.5 h-3.5 animate-pulse' : 'w-3.5 h-3.5'} />
              {scanningA ? 'Engine A…' : 'Engine A scan'}
            </Button>
            <Button
              size="sm"
              variant={scanSource === 'B' ? 'default' : 'outline'}
              className="h-8 gap-1 text-xs"
              onClick={() => runScan('B')}
              disabled={scanningA || scanningB}
            >
              <Layers className={scanningB ? 'w-3.5 h-3.5 animate-pulse' : 'w-3.5 h-3.5'} />
              {scanningB ? 'Engine B…' : 'Engine B scan'}
            </Button>
            <RefreshButton onClick={refreshLast} loading={lastLoading} />
          </div>
        </CardContent>
      </Card>

      {lastError && <ErrorBanner message={lastError} onRetry={refreshLast} />}

      {/* Status row */}
      <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
        <Badge variant="outline" className="text-[10px] uppercase">
          Source: {sourceLabel}
        </Badge>
        <span>{filtered.length} signals match filter ({baseSignals.length} total)</span>
        {lastScanAgeIso && <span>· Last scan: {new Date(lastScanAgeIso).toLocaleTimeString()}</span>}
      </div>

      <div className="grid grid-cols-5 gap-4">
        {/* Signal list */}
        <Card className="col-span-3 border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center justify-between">
              <span>Signal Feed</span>
              <Badge variant="outline" className="text-[10px]">
                {filtered.length} shown
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-[640px] pr-2">
              {(scanningA || scanningB) && filtered.length === 0 ? (
                <div className="space-y-2">
                  {Array.from({ length: 6 }).map((_, i) => (
                    <Skeleton key={i} className="h-24 w-full" />
                  ))}
                </div>
              ) : filtered.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-[300px] text-muted-foreground text-center px-4">
                  <Filter className="w-8 h-8 mb-3 opacity-40" />
                  {baseSignals.length === 0 && baseSource === 'B' ? (
                    <>
                      <p className="text-sm">Engine B returned no structural setups</p>
                      <p className="text-[11px] mt-1 max-w-md">
                        Engine B is strict by design. It requires a complete checklist (structure, location, trigger, room, RR, no D1 conflict).
                        It is normal to see zero setups in chop or low-conviction conditions.
                      </p>
                    </>
                  ) : baseSignals.length === 0 ? (
                    <>
                      <p className="text-sm">No signals yet</p>
                      <p className="text-[11px] mt-1">Click <span className="font-mono">Engine A scan</span> or <span className="font-mono">Engine B scan</span> to run.</p>
                    </>
                  ) : (
                    <>
                      <p className="text-sm">No signals match your filters</p>
                      <p className="text-[11px] mt-1">{baseSignals.length} signals total — adjust pair / direction / asset filters.</p>
                    </>
                  )}
                </div>
              ) : (
                <div className="space-y-4">
                  {groupedSignals.map(({ key, signals }) => (
                    <div key={key} className="space-y-2">
                      <div className="flex items-center justify-between sticky top-0 z-[1] bg-card/95 backdrop-blur-sm py-1 border-b border-border/50">
                        <span className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
                          {formatGroupLabel(key)}
                        </span>
                        <Badge variant="outline" className="text-[9px] font-mono">
                          {signals.length} · top {fmtNum(signals[0]?.confluenceScore ?? signals[0]?.score, 2)}
                        </Badge>
                      </div>
                      <div className="space-y-2 pl-0">
                        {signals.map((s) => (
                          <EngineASignalCard
                            key={s.id || `${s.pair}-${s.direction}-${s.timestamp || Math.random()}`}
                            signal={s}
                            selected={selected?.id === s.id}
                            onSelect={handleSelect}
                            compact
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

        {/* Signal detail */}
        <Card className="col-span-2 border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <Activity className="w-4 h-4 text-primary" />
              Signal Detail
            </CardTitle>
          </CardHeader>
          <CardContent>
            {selected ? (
              <ScrollArea className="h-[640px] pr-2">
                <div className="space-y-3">
                  <EngineASignalCard
                    signal={{ ...selected, livePrice: priceFor(selected) }}
                    onExecute={(s) => requestExecute(s, pendingStyle)}
                    executeDisabled={executeDisabled}
                    executeLabel={executeLabel}
                  />

                  {/* Per-style execute toolbar (Scalp / Intraday / Swing) */}
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
                          <span className="font-mono">×</span>
                        </div>
                      </div>
                      <div className="grid grid-cols-3 gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-9 gap-1 text-xs hover:bg-warning/10"
                          onClick={() => requestExecute(selected, 'scalp')}
                          disabled={executeDisabled || executing}
                        >
                          <Clock className="w-3.5 h-3.5" />
                          Scalp
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-9 gap-1 text-xs hover:bg-primary/10"
                          onClick={() => requestExecute(selected, 'intraday')}
                          disabled={executeDisabled || executing}
                        >
                          <Sun className="w-3.5 h-3.5" />
                          Intraday
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-9 gap-1 text-xs hover:bg-long/10"
                          onClick={() => requestExecute(selected, 'swing')}
                          disabled={executeDisabled || executing}
                        >
                          <Moon className="w-3.5 h-3.5" />
                          Swing
                        </Button>
                      </div>
                      <p className="text-[10px] text-muted-foreground">
                        Backend recomputes SL/TP for the chosen style (ATR ladder). Sizing scales risk between 0.25× and 1.0×.
                      </p>
                    </CardContent>
                  </Card>

                  {/* AI Review action */}
                  <div className="flex items-center gap-2 flex-wrap">
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-8 text-xs gap-1"
                      onClick={() => runAiReview(selected)}
                      disabled={visionLoading}
                    >
                      <Eye className={visionLoading ? 'w-3.5 h-3.5 animate-pulse' : 'w-3.5 h-3.5'} />
                      {visionLoading ? 'AI Vision…' : 'AI Review (Vision)'}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-8 text-xs gap-1"
                      onClick={() => runAiTextReview(selected)}
                      disabled={textLoading}
                    >
                      <FileText className={textLoading ? 'w-3.5 h-3.5 animate-pulse' : 'w-3.5 h-3.5'} />
                      {textLoading ? 'AI Text…' : 'AI Review (Text)'}
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

                  {/* AI Vision Review output */}
                  {aiReview && aiReviewMode === 'vision' && (
                    <Card className="border-border/60 bg-card/50">
                      <CardContent className="p-3 space-y-2">
                        <div className="flex items-center justify-between">
                          <p className="text-[10px] uppercase text-muted-foreground">AI Vision Review</p>
                          <span className="text-[10px] text-muted-foreground font-mono">{aiReview.model || 'vision'} · {aiReview.tf || 'H4'}</span>
                        </div>

                        {aiReview.structured?.style_ratings && (
                          <div className="grid grid-cols-3 gap-2 text-[11px]">
                            <div>
                              <div className="text-muted-foreground text-[10px]">Scalp</div>
                              <div className="font-mono">{aiReview.structured.style_ratings.scalp || '—'}</div>
                            </div>
                            <div>
                              <div className="text-muted-foreground text-[10px]">Intraday</div>
                              <div className="font-mono">{aiReview.structured.style_ratings.intraday || '—'}</div>
                            </div>
                            <div>
                              <div className="text-muted-foreground text-[10px]">Swing</div>
                              <div className="font-mono">{aiReview.structured.style_ratings.swing || '—'}</div>
                            </div>
                          </div>
                        )}

                        {(aiReview.structured?.sl_flag === 'too_tight' || aiReview.structured?.tp_flag === 'too_far') && (
                          <div className="text-[11px] text-warning">
                            {aiReview.structured.sl_flag === 'too_tight' && 'SL flagged tight · '}
                            {aiReview.structured.tp_flag === 'too_far' && 'TP flagged far'}
                          </div>
                        )}

                        {aiReview.structured?.final_verdict && (
                          <div className="text-[11px]">
                            <span className="text-muted-foreground">Verdict: </span>
                            <span className="font-mono">{aiReview.structured.final_verdict}</span>
                          </div>
                        )}

                        {aiReview.analysis && (
                          <pre className="text-[11px] font-mono whitespace-pre-wrap text-foreground/90 max-h-72 overflow-y-auto p-2 bg-muted/20 rounded border border-border/40">
                            {aiReview.analysis}
                          </pre>
                        )}
                      </CardContent>
                    </Card>
                  )}

                  {/* AI Text Review (Marcus Reid) output */}
                  {aiTextReview && aiReviewMode === 'text' && (
                    <Card className="border-border/60 bg-card/50">
                      <CardContent className="p-3 space-y-2">
                        <div className="flex items-center justify-between">
                          <p className="text-[10px] uppercase text-muted-foreground">AI Text Review — Marcus Reid</p>
                          <span className="text-[10px] text-muted-foreground font-mono">
                            {aiTextReview.grade || '—'} · edge {aiTextReview.edgeProbability ?? '—'}%
                          </span>
                        </div>

                        {aiTextReview.style_ratings && (
                          <div className="grid grid-cols-3 gap-2 text-[11px]">
                            <div>
                              <div className="text-muted-foreground text-[10px]">Scalp</div>
                              <div className="font-mono">{aiTextReview.style_ratings.scalp?.grade || '—'}</div>
                            </div>
                            <div>
                              <div className="text-muted-foreground text-[10px]">Intraday</div>
                              <div className="font-mono">{aiTextReview.style_ratings.intraday?.grade || '—'}</div>
                            </div>
                            <div>
                              <div className="text-muted-foreground text-[10px]">Swing</div>
                              <div className="font-mono">{aiTextReview.style_ratings.swing?.grade || '—'}</div>
                            </div>
                          </div>
                        )}

                        <div className="grid grid-cols-2 gap-2 text-[11px]">
                          <div><span className="text-muted-foreground">Verdict:</span> <span className="font-mono">{aiTextReview.verdict || '—'}</span></div>
                          <div><span className="text-muted-foreground">Risk:</span> <span className="font-mono">{aiTextReview.riskLevel || '—'}</span></div>
                          <div><span className="text-muted-foreground">Position:</span> <span className="font-mono">{aiTextReview.positionSizing || '—'}</span></div>
                          <div><span className="text-muted-foreground">Entry:</span> <span className="font-mono">{aiTextReview.entryZone || '—'}</span></div>
                          <div><span className="text-muted-foreground">Invalidation:</span> <span className="font-mono">{aiTextReview.invalidation || '—'}</span></div>
                          <div><span className="text-muted-foreground">Key Levels:</span> <span className="font-mono">{aiTextReview.keyLevels || '—'}</span></div>
                        </div>

                        {aiTextReview.warnings && aiTextReview.warnings.length > 0 && (
                          <div className="text-[11px] text-warning">
                            {aiTextReview.warnings.map((w, i) => (
                              <div key={i}>• {w}</div>
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

                  {/* Confidence engine breakdown */}
                  {selected.confidenceDetail && (
                    <Card className="border-border/60 bg-card/50">
                      <CardContent className="p-3 space-y-2">
                        <div className="flex items-center justify-between">
                          <p className="text-[10px] uppercase text-muted-foreground">Confidence Engine</p>
                          <Badge variant="outline" className="text-[10px]">
                            {fmtNum(selected.confidenceDetail.confidence, 2)}
                            {selected.confidenceDetail.degraded ? ' · degraded' : ''}
                          </Badge>
                        </div>
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          <DetailRow label="Indicator agreement" value={fmtNum(selected.confidenceDetail.components?.indicator_agreement, 2)} />
                          <DetailRow label="TF alignment" value={fmtNum(selected.confidenceDetail.components?.timeframe_alignment, 2)} />
                          <DetailRow label="Regime fit" value={fmtNum(selected.confidenceDetail.components?.regime_fit, 2)} />
                          <DetailRow label="Liquidity" value={fmtNum(selected.confidenceDetail.components?.liquidity_quality, 2)} />
                          <DetailRow label="Confidence" value={fmtNum(selected.confidenceDetail.confidence, 2)} />
                          <DetailRow label="Session quality" value={String(selected.confidenceDetail.session_quality || '—')} />
                          {selected.confidenceDetail.available_count != null && (
                            <DetailRow label="Components active" value={`${selected.confidenceDetail.available_count}/4`} />
                          )}
                        </div>
                        {selected.confidenceDetail.weights_used && (
                          <div className="text-[10px] text-muted-foreground">
                            Weights: {Object.entries(selected.confidenceDetail.weights_used)
                              .map(([k, v]) => `${k}=${fmtNum(v, 2)}`)
                              .join(' · ')}
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  )}

                  {/* Engine A factor diagnostics */}
                  {selected.factorDiagnostics && (
                    <Card className="border-border/60 bg-card/50">
                      <CardContent className="p-3 space-y-2">
                        <p className="text-[10px] uppercase text-muted-foreground">Factor Diagnostics</p>
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          {Object.entries(selected.factorDiagnostics)
                            .slice(0, 12)
                            .map(([k, v]) => (
                              <DetailRow key={k} label={k} value={typeof v === 'number' ? fmtNum(v, 2) : String(v ?? '—')} />
                            ))}
                        </div>
                      </CardContent>
                    </Card>
                  )}

                  {/* Engine B (naked_data / engine_b) attached */}
                  {(selected.naked_data || selected.engine_b) && (
                    <div>
                      <p className="text-[10px] uppercase text-muted-foreground mb-1">Engine B Attached</p>
                      <EngineBChecklistCard
                        data={selected.naked_data || selected.engine_b}
                        pair={selected.pair || selected.display}
                        type={selected.type}
                        livePrice={priceFor(selected)}
                      />
                    </div>
                  )}

                  {/* Levels detail */}
                  <Card className="border-border/60 bg-card/50">
                    <CardContent className="p-3 space-y-2">
                      <p className="text-[10px] uppercase text-muted-foreground">All Levels</p>
                      <div className="grid grid-cols-2 gap-2 text-xs">
                        <DetailRow label="Live" value={fmtPrice(priceFor(selected), selected.pair, selected.type)} />
                        <DetailRow label="Entry" value={fmtPrice(selected.entry ?? selected.price, selected.pair, selected.type)} />
                        <DetailRow label="SL" value={fmtPrice(selected.sl, selected.pair, selected.type)} accent="short" />
                        <DetailRow label="TP1" value={fmtPrice(selected.tp ?? selected.tp1, selected.pair, selected.type)} accent="long" />
                        <DetailRow label="TP2" value={fmtPrice(selected.tp2, selected.pair, selected.type)} accent="long" />
                        <DetailRow label="ATR" value={fmtPrice(selected.atr, selected.pair, selected.type)} />
                        <DetailRow label="R:R" value={fmtNum(selected.rr ?? selected.rr1, 2)} />
                        <DetailRow label="Scalp SL" value={fmtPrice(selected.scalp_sl, selected.pair, selected.type)} />
                        <DetailRow label="Scalp TP" value={fmtPrice(selected.scalp_tp, selected.pair, selected.type)} />
                        <DetailRow label="Intraday SL" value={fmtPrice(selected.intraday_sl, selected.pair, selected.type)} />
                        <DetailRow label="Intraday TP" value={fmtPrice(selected.intraday_tp, selected.pair, selected.type)} />
                      </div>
                    </CardContent>
                  </Card>
                </div>
              </ScrollArea>
            ) : (
              <div className="flex flex-col items-center justify-center h-[640px] text-muted-foreground">
                <Activity className="w-8 h-8 mb-3 opacity-40" />
                <p className="text-sm">Select a signal to inspect</p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <AlertDialog open={!!confirmSig} onOpenChange={() => { setConfirmSig(null); setPendingStyle('auto'); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Confirm Execution · {pendingStyle === 'auto' ? (confirmSig?.style?.toUpperCase() || 'AUTO') : pendingStyle.toUpperCase()}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-xs">
                <div>
                  Execute <b>{confirmSig?.direction}</b> {confirmSig?.pair || confirmSig?.display} at{' '}
                  <span className="font-mono">{fmtPrice(confirmSig?.entry ?? confirmSig?.price, confirmSig?.pair, confirmSig?.type)}</span>
                </div>
                <div className="font-mono">
                  SL: {fmtPrice(confirmSig?.sl, confirmSig?.pair, confirmSig?.type)} ·
                  TP: {fmtPrice(confirmSig?.tp ?? confirmSig?.tp1, confirmSig?.pair, confirmSig?.type)} ·
                  R:R {fmtNum(confirmSig?.rr ?? confirmSig?.rr1, 2)}
                </div>
                <div className="text-muted-foreground">
                  Style: <span className="font-mono">{(pendingStyle === 'auto' ? confirmSig?.style || 'auto' : pendingStyle).toUpperCase()}</span>
                  {' · '}Sizing: <span className="font-mono">{fmtNum(sizingOverride, 2)}×</span>
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
              {executing ? 'Executing…' : `Confirm ${pendingStyle === 'auto' ? '' : pendingStyle.toUpperCase()}`}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function DetailRow({ label, value, accent }: { label: string; value: string; accent?: 'short' | 'long' }) {
  const fg = accent === 'long' ? 'text-long' : accent === 'short' ? 'text-short' : 'text-foreground';
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[10px] text-muted-foreground capitalize">{label.replace(/_/g, ' ')}</span>
      <span className={`text-xs font-mono ${fg}`}>{value}</span>
    </div>
  );
}
