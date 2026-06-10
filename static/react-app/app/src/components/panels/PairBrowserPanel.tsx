import { useState, useCallback, useMemo, useEffect } from 'react';
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ErrorBanner } from '@/components/shared';
import { Search, Eye, Newspaper, GitCompare, Zap, Layers, Activity, ExternalLink, AlertTriangle } from 'lucide-react';
import { fmtNum, cn } from '@/lib/utils';
import { fmtPrice } from '@/lib/athenaFormat';
import { fetchVisionCandlePayload } from '@/lib/visionReview';
import { EngineASignalCard, EngineBChecklistCard, VisionReviewCard } from '@/components/athena';
import type {
  EngineASignal,
  EngineBNakedResult,
  CompareResponse,
  PairScanResponse,
  ChartAnalysisResponse,
} from '@/types/athena';

interface PairsApiResponse {
  groups?: Record<string, Array<{ sym: string; label: string; enabled?: boolean }>>;
  total?: number;
  active?: number;
}

interface NewsApiResponse {
  pair?: string;
  eodhdTicker?: string;
  structured?: {
    overall_sentiment?: string;
    sentiment_score?: number;
    summary?: string;
    drivers?: string[];
    items?: Array<{ title?: string; source?: string; sentiment?: string; url?: string; date?: string }>;
  };
  confluenceVote?: { vote: string; weight?: number };
  error?: string;
}

interface NakedAnalysisResponse extends EngineBNakedResult {
  error?: string;
}

type Direction = 'AUTO' | 'LONG' | 'SHORT';
type Style = 'auto' | 'scalp' | 'intraday' | 'swing';

const TRADINGVIEW_SYMBOL_MAP: Record<string, string> = {
  EURX: 'PEPPERSTONE:EURX',
  JPYX: 'PEPPERSTONE:JPYX',
  USDX: 'PEPPERSTONE:USDX',
};

function tradingViewSymbolForLabel(label: string): string {
  const normalized = label.trim().toUpperCase();
  return TRADINGVIEW_SYMBOL_MAP[normalized] || label.replace(/\//g, '');
}

export default function PairBrowserPanel() {
  const { showToast } = useStore();
  const [search, setSearch] = useState('');
  const [selectedSym, setSelectedSym] = useState<string>('EUR/USD');
  const [selectedLabel, setSelectedLabel] = useState<string>('EUR/USD');
  const [direction, setDirection] = useState<Direction>('AUTO');
  const [style, setStyle] = useState<Style>('auto');
  const [activeTab, setActiveTab] = useState('engineA');

  // Results
  const [engineA, setEngineA] = useState<EngineASignal | null>(null);
  const [engineB, setEngineB] = useState<EngineBNakedResult | null>(null);
  const [compare, setCompare] = useState<CompareResponse | null>(null);
  const [vision, setVision] = useState<ChartAnalysisResponse | null>(null);
  const [news, setNews] = useState<NewsApiResponse | null>(null);

  const { data: pairs, loading: pairsLoading, error: pairsError } = useApiPoll<PairsApiResponse>('/api/pairs', 0);
  const { post: postPairScan, loading: scanningA } = useApiPost<PairScanResponse>();
  const { post: postNaked, loading: scanningB } = useApiPost<NakedAnalysisResponse>();
  const { post: postCompare, loading: comparing } = useApiPost<CompareResponse>();
  const { post: postVision, loading: visionLoading } = useApiPost<ChartAnalysisResponse>();
  const { post: postNews, loading: newsLoading } = useApiPost<NewsApiResponse>();
  const { post: postExecute, loading: executing } = useApiPost<{ success?: boolean; ticket?: string; error?: string }>();
  const { priceFor, ageSecFor, sourceFor } = useLivePrices();

  const groupEntries: Array<[string, Array<{ sym: string; label: string; enabled?: boolean }>]> = useMemo(() => {
    return Object.entries(pairs?.groups || {});
  }, [pairs]);

  const allLookup = useMemo(() => {
    const map = new Map<string, { sym: string; label: string; group: string }>();
    for (const [g, list] of groupEntries) {
      for (const item of list) {
        map.set(item.sym, { sym: item.sym, label: item.label, group: g });
        map.set(item.label, { sym: item.sym, label: item.label, group: g });
      }
    }
    return map;
  }, [groupEntries]);

  // Make sure label tracks selected sym so compare-engines etc. can pass `display` correctly.
  useEffect(() => {
    const hit = allLookup.get(selectedSym);
    if (hit) setSelectedLabel(hit.label);
  }, [selectedSym, allLookup]);

  // Reset results when pair changes
  useEffect(() => {
    setEngineA(null);
    setEngineB(null);
    setCompare(null);
    setVision(null);
    setNews(null);
  }, [selectedSym]);

  const buildSeed = useCallback(
    (requireDirection: boolean) => {
      const lookupHit = allLookup.get(selectedSym);
      const seed: Record<string, unknown> = {
        symbol: selectedSym,
        pair: lookupHit?.label,
        display: lookupHit?.label,
      };
      // Direction
      if (direction === 'LONG' || direction === 'SHORT') {
        seed.direction = direction;
      } else if (engineA?.direction) {
        seed.direction = engineA.direction;
      } else if (requireDirection) {
        return null;
      }
      // Pull entry from Engine A signal if available
      if (engineA?.entry != null) seed.entry = engineA.entry;
      else if (engineA?.price != null) seed.price = engineA.price;
      seed.style = style;
      return seed;
    },
    [selectedSym, allLookup, direction, engineA, style],
  );

  const runEngineA = useCallback(
    async (silent = false): Promise<EngineASignal | null> => {
      const result = await postPairScan('/api/pair-scan', { symbol: selectedSym, style, includeIntermarket: true });
      if (!result || result.error) {
        if (!silent) showToast(`Engine A failed: ${result?.error || 'unknown'}`, 'error');
        return null;
      }
      const sig = result.signal as EngineASignal | null;
      setEngineA(sig);
      // If naked_data is attached, surface as engineB too
      if (sig?.naked_data) setEngineB(sig.naked_data as EngineBNakedResult);
      if (!silent) showToast(`Engine A ready for ${selectedLabel}`, 'success');
      return sig;
    },
    [postPairScan, selectedSym, selectedLabel, style, showToast],
  );

  const runEngineB = useCallback(async () => {
    let signal: EngineASignal | null = engineA;
    if (direction === 'AUTO' && !signal) {
      signal = await runEngineA(true);
      if (!signal) return;
    }
    const seed = buildSeed(true);
    if (!seed) {
      showToast('Run Engine A first or pick LONG / SHORT.', 'error');
      return;
    }
    const result = await postNaked('/api/naked-analysis', { signal: seed, force_ai: true });
    if (!result || (result as NakedAnalysisResponse).error) {
      showToast(`Engine B failed: ${(result as NakedAnalysisResponse | null)?.error || 'unknown'}`, 'error');
      return;
    }
    setEngineB(result as EngineBNakedResult);
    showToast(`Engine B ready for ${selectedLabel}`, 'success');
  }, [engineA, direction, buildSeed, runEngineA, postNaked, showToast, selectedLabel]);

  const runCompare = useCallback(async () => {
    const seed = buildSeed(false) || { symbol: selectedSym, style };
    const result = await postCompare('/api/compare-engines', { signal: seed, style });
    if (!result || result.error) {
      showToast(`Compare failed: ${result?.error || 'unknown'}`, 'error');
      return;
    }
    setCompare(result);
    if (result.engineA) setEngineA(result.engineA);
    if (result.engineB) setEngineB(result.engineB);
    showToast(`Compare ready: ${result.summary?.verdict || 'done'}`, 'success');
    setActiveTab('compare');
  }, [buildSeed, selectedSym, style, postCompare, showToast]);

  const runVision = useCallback(async () => {
    let signal: EngineASignal | null = engineA;
    if (!signal) {
      signal = await runEngineA(true);
      if (!signal) return;
    }
    const sym = signal.symbol || selectedSym;
    try {
      const candlePayload = await fetchVisionCandlePayload(sym);
      const result = await postVision('/api/chart-analysis', {
        symbol: sym,
        tf: 'H4',
        signal: signal,
        engineB: engineB,
        server_render: true,
        chart_source: 'pair_browser',
        ...candlePayload,
        entry: signal.entry ?? signal.price,
        sl: signal.sl,
        tp: signal.tp ?? signal.tp1,
      });
      if (!result || result.error) {
        showToast(`Vision failed: ${result?.error || 'unknown'}`, 'error');
        return;
      }
      setVision(result);
      setActiveTab('vision');
      showToast('AI vision ready', 'success');
    } catch (err) {
      showToast(`Vision error: ${err instanceof Error ? err.message : 'unknown'}`, 'error');
    }
  }, [engineA, engineB, runEngineA, postVision, selectedSym, showToast]);

  const runNews = useCallback(async () => {
    const result = await postNews('/api/news-sentiment', { symbol: selectedSym });
    if (!result || result.error) {
      showToast(`News failed: ${result?.error || 'no news'}`, 'error');
      return;
    }
    setNews(result);
    showToast('News loaded', 'success');
  }, [postNews, selectedSym, showToast]);

  const runExecute = useCallback(async () => {
    if (!engineA && !engineB) {
      showToast('Run Engine A or B first', 'error');
      return;
    }
    const sig = engineA || (engineB as unknown as EngineASignal);
    const payload = {
      signal: {
        symbol: sig.symbol,
        pair: sig.pair || sig.display,
        type: sig.type,
        direction: sig.direction,
        entry: sig.entry ?? (sig as { price?: number }).price,
        sl: sig.sl,
        tp: sig.tp ?? sig.tp1,
        style,
        source: engineA ? 'engine_a_pair_browser' : 'engine_b_pair_browser',
      },
    };
    const r = await postExecute('/api/execute', payload);
    if (r?.success) showToast(`Executed — ticket ${r.ticket}`, 'success');
    else showToast(`Execution failed: ${r?.error || 'unknown'}`, 'error');
  }, [engineA, engineB, style, postExecute, showToast]);

  const filteredGroups: Array<[string, Array<{ sym: string; label: string; enabled?: boolean }>]> = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return groupEntries;
    return groupEntries
      .map(([g, list]) => [g, list.filter((p) => p.sym.toLowerCase().includes(q) || p.label.toLowerCase().includes(q))] as [string, typeof groupEntries[0][1]])
      .filter(([, list]) => list.length > 0);
  }, [search, groupEntries]);

  return (
    <div className="flex gap-4 h-[calc(100vh-120px)]">
      {/* Left: pair list */}
      <Card className="w-[260px] shrink-0 border-border/60 bg-card/50 flex flex-col">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>Pairs ({pairs?.total ?? '—'})</CardTitle>
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <Input
              placeholder="Filter…"
              className="pl-7 h-8 text-xs"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </CardHeader>
        <CardContent className="flex-1 overflow-hidden p-0">
          <ScrollArea className="h-full px-3 pb-4">
            {pairsLoading ? (
              <div className="space-y-1 px-1">
                {Array.from({ length: 12 }).map((_, i) => (
                  <Skeleton key={i} className="h-6 w-full" />
                ))}
              </div>
            ) : pairsError ? (
              <ErrorBanner message={pairsError} />
            ) : (
              filteredGroups.map(([groupName, list]) => (
                <div key={groupName} className="mb-3">
                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground mt-2 mb-1 px-1">
                    {groupName} <span className="text-foreground/40">({list.length})</span>
                  </p>
                  {list.map((p) => (
                    <button
                      key={p.sym}
                      onClick={() => setSelectedSym(p.sym)}
                      className={cn(
                        'w-full text-left px-2 py-1 rounded text-xs font-mono transition-colors',
                        selectedSym === p.sym
                          ? 'bg-primary/20 text-primary'
                          : p.enabled === false
                            ? 'text-muted-foreground/60 hover:bg-muted/30'
                            : 'hover:bg-muted/30 text-foreground',
                      )}
                    >
                      {p.label}
                      {p.enabled === false && <span className="ml-1 text-[9px]">[off]</span>}
                    </button>
                  ))}
                </div>
              ))
            )}
          </ScrollArea>
        </CardContent>
      </Card>

      {/* Right: detail */}
      <div className="flex-1 flex flex-col gap-3 overflow-hidden">
        {/* Toolbar */}
        <Card className="border-border/60 bg-card/50">
          <CardContent className="p-3 flex items-center gap-2 flex-wrap">
            <div className="flex items-center gap-2 mr-3">
              <h2 className="text-base font-mono font-bold">{selectedLabel}</h2>
              {engineA?.direction && (
                <Badge className={engineA.direction === 'LONG' ? 'badge-long' : 'badge-short'}>{engineA.direction}</Badge>
              )}
            </div>

            <Select value={direction} onValueChange={(v) => setDirection(v as Direction)}>
              <SelectTrigger className="w-[110px] h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="AUTO">Auto direction</SelectItem>
                <SelectItem value="LONG">Force LONG</SelectItem>
                <SelectItem value="SHORT">Force SHORT</SelectItem>
              </SelectContent>
            </Select>

            <Select value={style} onValueChange={(v) => setStyle(v as Style)}>
              <SelectTrigger className="w-[110px] h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">Auto style</SelectItem>
                <SelectItem value="scalp">Scalp</SelectItem>
                <SelectItem value="intraday">Intraday</SelectItem>
                <SelectItem value="swing">Swing</SelectItem>
              </SelectContent>
            </Select>

            <div className="flex items-center gap-1 ml-auto">
              <Button size="sm" className="h-8 text-xs gap-1" onClick={() => runEngineA()} disabled={scanningA}>
                <Zap className={scanningA ? 'w-3.5 h-3.5 animate-pulse' : 'w-3.5 h-3.5'} />
                {scanningA ? 'Engine A…' : 'Engine A'}
              </Button>
              <Button size="sm" className="h-8 text-xs gap-1" onClick={runEngineB} disabled={scanningB}>
                <Layers className={scanningB ? 'w-3.5 h-3.5 animate-pulse' : 'w-3.5 h-3.5'} />
                {scanningB ? 'Engine B…' : 'Engine B'}
              </Button>
              <Button size="sm" variant="outline" className="h-8 text-xs gap-1" onClick={runCompare} disabled={comparing}>
                <GitCompare className="w-3.5 h-3.5" />
                {comparing ? 'Compare…' : 'Compare'}
              </Button>
              <Button size="sm" variant="outline" className="h-8 text-xs gap-1" onClick={runVision} disabled={visionLoading}>
                <Eye className="w-3.5 h-3.5" />
                {visionLoading ? 'Vision…' : 'AI Vision'}
              </Button>
              <Button size="sm" variant="outline" className="h-8 text-xs gap-1" onClick={runNews} disabled={newsLoading}>
                <Newspaper className="w-3.5 h-3.5" />
                {newsLoading ? 'News…' : 'News'}
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Tabs */}
        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col overflow-hidden">
          <TabsList className="w-fit">
            <TabsTrigger value="engineA" className="text-xs gap-1">
              <Zap className="w-3 h-3" /> Engine A
            </TabsTrigger>
            <TabsTrigger value="engineB" className="text-xs gap-1">
              <Layers className="w-3 h-3" /> Engine B
            </TabsTrigger>
            <TabsTrigger value="compare" className="text-xs gap-1">
              <GitCompare className="w-3 h-3" /> Compare
            </TabsTrigger>
            <TabsTrigger value="vision" className="text-xs gap-1">
              <Eye className="w-3 h-3" /> Vision
            </TabsTrigger>
            <TabsTrigger value="news" className="text-xs gap-1">
              <Newspaper className="w-3 h-3" /> News
            </TabsTrigger>
            <TabsTrigger value="execute" className="text-xs gap-1">
              <Activity className="w-3 h-3" /> Execute
            </TabsTrigger>
          </TabsList>

          <ScrollArea className="flex-1 pr-2 mt-2">
            <TabsContent value="engineA" className="mt-0 space-y-3">
              {engineA ? (
                <>
                  <EngineASignalCard
                    signal={{
                      ...engineA,
                      livePrice: priceFor(engineA),
                      livePriceAgeSec: ageSecFor(engineA),
                      livePriceSource: sourceFor(engineA),
                    }}
                  />
                  {engineA.factorDiagnostics && (
                    <Card className="border-border/60 bg-card/50">
                      <CardContent className="p-3 space-y-2">
                        <p className="text-[10px] uppercase text-muted-foreground">Factor Diagnostics</p>
                        <div className="grid grid-cols-3 gap-2 text-xs">
                          {Object.entries(engineA.factorDiagnostics).slice(0, 12).map(([k, v]) => (
                            <DetailRow key={k} label={k} value={typeof v === 'number' ? fmtNum(v, 2) : String(v ?? '—')} />
                          ))}
                        </div>
                      </CardContent>
                    </Card>
                  )}
                  {engineA.confidenceDetail && (
                    <Card className="border-border/60 bg-card/50">
                      <CardContent className="p-3 space-y-2">
                        <p className="text-[10px] uppercase text-muted-foreground">Confidence Engine</p>
                        <div className="grid grid-cols-3 gap-2 text-xs">
                          {Object.entries(engineA.confidenceDetail).slice(0, 9).map(([k, v]) => (
                            <DetailRow key={k} label={k} value={typeof v === 'number' ? fmtNum(v, 2) : String(v ?? '—')} />
                          ))}
                        </div>
                      </CardContent>
                    </Card>
                  )}
                </>
              ) : (
                <Empty label='Click "Engine A" to scan' icon={<Zap className="w-8 h-8 opacity-40" />} />
              )}
            </TabsContent>

            <TabsContent value="engineB" className="mt-0">
              {engineB ? (
                <EngineBChecklistCard
                  data={engineB}
                  pair={selectedLabel}
                  type={engineA?.type}
                  livePrice={priceFor(selectedLabel)}
                  livePriceAgeSec={ageSecFor(selectedLabel)}
                  livePriceSource={sourceFor(selectedLabel)}
                />
              ) : (
                <Empty label='Click "Engine B" to scan' icon={<Layers className="w-8 h-8 opacity-40" />} />
              )}
            </TabsContent>

            <TabsContent value="compare" className="mt-0">
              {compare ? (
                <CompareView
                  compare={compare}
                  pair={selectedLabel}
                  priceFor={priceFor}
                  ageSecFor={ageSecFor}
                  sourceFor={sourceFor}
                />
              ) : (
                <Empty label='Click "Compare" to run A vs B' icon={<GitCompare className="w-8 h-8 opacity-40" />} />
              )}
            </TabsContent>

            <TabsContent value="vision" className="mt-0">
              {vision ? <VisionReviewCard vision={vision} /> : <Empty label='Click "AI Vision" to run' icon={<Eye className="w-8 h-8 opacity-40" />} />}
            </TabsContent>

            <TabsContent value="news" className="mt-0">
              {news ? <NewsView news={news} /> : <Empty label='Click "News" to load' icon={<Newspaper className="w-8 h-8 opacity-40" />} />}
            </TabsContent>

            <TabsContent value="execute" className="mt-0 space-y-3">
              <Card className="border-border/60 bg-card/50">
                <CardHeader className="pb-2">
                  <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
                    <Activity className="w-4 h-4 text-primary" /> Manual Execute
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <p className="text-[11px] text-muted-foreground">
                    Risk gates, freshness, and kill-switch are still re-checked server-side. This sends one execution to{' '}
                    <span className="font-mono">/api/execute</span> using whichever engine produced the latest levels.
                  </p>
                  {!engineA && !engineB && (
                    <div className="text-[11px] text-warning flex items-center gap-2">
                      <AlertTriangle className="w-4 h-4" /> Run Engine A or Engine B first.
                    </div>
                  )}
                  <Button size="sm" onClick={runExecute} disabled={executing || (!engineA && !engineB)}>
                    {executing ? 'Executing…' : 'Execute'}
                  </Button>
                </CardContent>
              </Card>

              <Card className="border-border/60 bg-card/50">
                <CardHeader className="pb-2">
                  <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
                    <ExternalLink className="w-4 h-4 text-primary" /> External Charts
                  </CardTitle>
                </CardHeader>
                <CardContent className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-8 text-xs"
                    onClick={() => {
                      const slug = tradingViewSymbolForLabel(selectedLabel);
                      window.open(`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(slug)}`, '_blank', 'noopener');
                    }}
                  >
                    Open TradingView
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-8 text-xs"
                    onClick={() => {
                      const slug = selectedLabel.replace(/\//g, '_');
                      window.open(`/chart?symbol=${encodeURIComponent(slug)}`, '_blank', 'noopener');
                    }}
                  >
                    Open Athena Chart
                  </Button>
                </CardContent>
              </Card>
            </TabsContent>
          </ScrollArea>
        </Tabs>
      </div>
    </div>
  );
}

function CompareView({
  compare,
  pair,
  priceFor,
  ageSecFor,
  sourceFor,
}: {
  compare: CompareResponse;
  pair: string;
  priceFor: (item: { display?: unknown; pair?: unknown; symbol?: unknown } | string | null | undefined) => number | undefined;
  ageSecFor: (item: { display?: unknown; pair?: unknown; symbol?: unknown } | string | null | undefined) => number | undefined;
  sourceFor: (item: { display?: unknown; pair?: unknown; symbol?: unknown } | string | null | undefined) => string | undefined;
}) {
  const verdict = compare.summary?.verdict || '—';
  const verdictBg = verdict === 'ALIGNED' ? 'bg-long/20 text-long' : verdict === 'CONFLICT' ? 'bg-short/20 text-short' : 'bg-muted/40 text-muted-foreground';
  return (
    <div className="space-y-3">
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Compare verdict</h3>
            <Badge className={cn('text-[11px]', verdictBg)}>{verdict}</Badge>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <SmallStat label="Same direction" value={compare.summary?.sameDirection ? 'YES' : 'NO'} />
            <SmallStat label="Structure aligned" value={compare.summary?.structureAligned ? 'YES' : 'NO'} />
            <SmallStat label="Macro aligned" value={compare.summary?.macroAligned ? 'YES' : 'NO'} />
          </div>
          <div className="text-[10px] text-muted-foreground">
            Engine A style: <span className="text-foreground font-mono">{compare.summary?.engineAStyle}</span> · Engine B style:{' '}
            <span className="text-foreground font-mono">{compare.summary?.engineBStyle}</span> · Engine B min score:{' '}
            <span className="text-foreground font-mono">{fmtNum(compare.summary?.engineBMinScore, 2)}</span>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <p className="text-[11px] uppercase text-muted-foreground">Engine A</p>
          {compare.engineA ? (
            <EngineASignalCard
              signal={{
                ...compare.engineA,
                livePrice: priceFor(compare.engineA),
                livePriceAgeSec: ageSecFor(compare.engineA),
                livePriceSource: sourceFor(compare.engineA),
              }}
              compact
            />
          ) : <Empty label="No Engine A signal" icon={<Zap className="w-6 h-6 opacity-40" />} />}
        </div>
        <div className="space-y-2">
          <p className="text-[11px] uppercase text-muted-foreground">Engine B</p>
          {compare.engineB ? (
            <EngineBChecklistCard
              data={compare.engineB}
              pair={pair}
              type={compare.engineA?.type}
              livePrice={priceFor(pair)}
              livePriceAgeSec={ageSecFor(pair)}
              livePriceSource={sourceFor(pair)}
              compact
            />
          ) : (
            <Empty label="No Engine B result" icon={<Layers className="w-6 h-6 opacity-40" />} />
          )}
        </div>
      </div>
    </div>
  );
}

function VisionView({ vision }: { vision: ChartAnalysisResponse }) {
  const s = vision.structured || {};
  const re = s.right_edge_status;
  const reBadgeClass = re === 'CONFIRMS' ? 'badge-long'
    : re === 'POTENTIAL_REVERSAL' ? 'badge-short'
    : 'badge-neutral';
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-4 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Eye className="w-4 h-4 text-primary" />
          <span className="text-sm font-semibold">AI Chart Analysis</span>
          {re && <Badge className={`${reBadgeClass} text-[10px]`}>RIGHT EDGE: {String(re).replace('_', ' ')}</Badge>}
          {s.rating && <Badge variant="outline" className="text-[10px]">{s.rating}</Badge>}
          {s.final_verdict && <Badge variant="outline" className="text-[10px]">Verdict: {s.final_verdict}</Badge>}
          {vision.model && <span className="text-[10px] text-muted-foreground font-mono ml-auto">{vision.model} · {vision.tf || 'H4'}</span>}
        </div>

        {(s.style_ratings) && (
          <div className="grid grid-cols-3 gap-2 text-[11px]">
            <div>
              <div className="text-muted-foreground text-[10px]">Scalp</div>
              <div className="font-mono">{s.style_ratings.scalp || '—'}</div>
            </div>
            <div>
              <div className="text-muted-foreground text-[10px]">Intraday</div>
              <div className="font-mono">{s.style_ratings.intraday || '—'}</div>
            </div>
            <div>
              <div className="text-muted-foreground text-[10px]">Swing</div>
              <div className="font-mono">{s.style_ratings.swing || '—'}</div>
            </div>
          </div>
        )}

        {(s.sl_flag === 'too_tight' || s.tp_flag === 'too_far') && (
          <div className="text-[11px] text-warning">
            {s.sl_flag === 'too_tight' && 'SL flagged tight · '}
            {s.tp_flag === 'too_far' && 'TP flagged far'}
          </div>
        )}

        {vision.analysis && (
          <pre className="text-[11px] leading-relaxed whitespace-pre-wrap font-mono bg-muted/20 p-3 rounded-md max-h-[420px] overflow-y-auto">{vision.analysis}</pre>
        )}

        {s.level_suggestions && Object.keys(s.level_suggestions).length > 0 && (
          <div>
            <p className="text-[10px] uppercase text-muted-foreground mb-1">Suggested Levels</p>
            <div className="grid grid-cols-3 gap-2 text-xs">
              {Object.entries(s.level_suggestions).slice(0, 12).map(([k, v]) => (
                <DetailRow key={k} label={k} value={fmtPrice(v)} />
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function NewsView({ news }: { news: NewsApiResponse }) {
  const s = news.structured || {};
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-4 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Newspaper className="w-4 h-4 text-primary" />
          <span className="text-sm font-semibold">News Sentiment</span>
          {s.overall_sentiment && <Badge variant="outline" className="text-[10px]">Overall: {s.overall_sentiment}</Badge>}
          {s.sentiment_score != null && <Badge variant="outline" className="text-[10px]">Score: {fmtNum(s.sentiment_score, 2)}</Badge>}
          {news.confluenceVote && <Badge variant="outline" className="text-[10px]">Vote: {news.confluenceVote.vote}</Badge>}
        </div>
        {s.summary && <p className="text-[11px] leading-relaxed">{s.summary}</p>}
        {Array.isArray(s.drivers) && s.drivers.length > 0 && (
          <div>
            <p className="text-[10px] uppercase text-muted-foreground mb-1">Drivers</p>
            <ul className="text-[11px] list-disc pl-4 space-y-1">
              {s.drivers.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          </div>
        )}
        {Array.isArray(s.items) && s.items.length > 0 && (
          <div className="space-y-2">
            {s.items.slice(0, 8).map((h, i) => (
              <div key={i} className="p-2 rounded-md bg-muted/30">
                <p className="text-xs">{h.title}</p>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-[10px] text-muted-foreground">{h.source}</span>
                  {h.sentiment && <Badge variant="outline" className="text-[9px]">{h.sentiment}</Badge>}
                  {h.url && (
                    <a href={h.url} target="_blank" rel="noopener noreferrer" className="text-[10px] text-primary underline">
                      open
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Empty({ label, icon }: { label: string; icon: React.ReactNode }) {
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-12 flex flex-col items-center justify-center text-muted-foreground">
        {icon}
        <p className="text-sm mt-3">{label}</p>
      </CardContent>
    </Card>
  );
}

function SmallStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-2 rounded-md bg-muted/30">
      <p className="text-[10px] text-muted-foreground uppercase">{label}</p>
      <p className="text-xs font-mono font-bold">{value}</p>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[10px] text-muted-foreground capitalize truncate">{label.replace(/_/g, ' ')}</span>
      <span className="text-xs font-mono">{value}</span>
    </div>
  );
}
