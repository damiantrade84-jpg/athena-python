import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ErrorBanner } from '@/components/shared';
import { Microscope, Play, Rocket, History, ListChecks, Brain } from 'lucide-react';
import { fmtNum } from '@/lib/utils';

interface RunSummary {
  run_id?: string;
  status?: string;
  mode?: string;
  results_count?: number;
  total?: number;
  symbols?: string[];
  timeframes?: string[];
  families?: string[];
  strategies?: string[];
  start_time?: string;
  end_time?: string;
  market_group?: string;
  trading_style?: string;
  [k: string]: unknown;
}

interface RunsResponse { runs?: RunSummary[]; error?: string }

interface RunStatusResponse {
  run_id?: string;
  status?: 'queued' | 'running' | 'complete' | 'failed' | string;
  error?: string;
  traceback?: string;
  mode?: string;
  results_count?: number;
  summary?: {
    total?: number;
    strong?: number;
    weak?: number;
    reject?: number;
    files_ok?: boolean;
    report_errors?: string[];
  };
  [k: string]: unknown;
}

interface RankedRow {
  symbol?: string;
  timeframe?: string;
  family?: string;
  strategy?: string;
  direction?: string;
  status?: string;
  trades?: number;
  win_rate?: number;
  profit_factor?: number;
  expectancy?: number;
  sqn?: number;
  sharpe?: number;
  max_drawdown?: number;
  [k: string]: unknown;
}

interface RankedResponse {
  run_id?: string;
  ranked?: RankedRow[];
  recommendations?: Array<Record<string, unknown>>;
  automated_next_tests?: Array<Record<string, unknown>>;
  note?: string;
  error?: string;
}

interface StartRunResponse {
  run_id?: string;
  mode?: string;
  status?: string;
  error?: string;
}

interface AiReviewResponse {
  ai_review?: string;
  summary?: string;
  recommendation?: string;
  error?: string;
  [k: string]: unknown;
}

const MARKET_GROUPS = [
  { value: 'crypto', label: 'Crypto' },
  { value: 'forex', label: 'Forex' },
  { value: 'commodities', label: 'Commodities' },
  { value: 'indices', label: 'Indices' },
  { value: 'stocks', label: 'Stocks' },
];

const STYLES = [
  { value: 'scalp', label: 'Scalp' },
  { value: 'intra', label: 'Intraday' },
  { value: 'swing', label: 'Swing' },
];

const DEPTHS = [
  { value: 'fast', label: 'Fast' },
  { value: 'standard', label: 'Standard' },
  { value: 'deep', label: 'Deep' },
];

const MANUAL_MODES = [
  { value: 'tiny', label: 'Tiny (focused)' },
  { value: 'small', label: 'Small' },
  { value: 'standard', label: 'Standard' },
  { value: 'large', label: 'Large' },
];

const DIRECTIONS = [
  { value: 'both', label: 'Both' },
  { value: 'long', label: 'Long only' },
  { value: 'short', label: 'Short only' },
];

function statusBadge(status?: string): string {
  switch ((status || '').toLowerCase()) {
    case 'complete': return 'badge-long';
    case 'running':
    case 'queued': return 'badge-neutral';
    case 'failed': return 'badge-short';
    default: return 'badge-neutral';
  }
}

export default function ResearchLabPanel() {
  const { showToast } = useStore();

  const [activeTab, setActiveTab] = useState<'autopilot' | 'manual' | 'runs'>('autopilot');

  // Autopilot
  const [marketGroup, setMarketGroup] = useState('crypto');
  const [tradingStyle, setTradingStyle] = useState('intra');
  const [researchDepth, setResearchDepth] = useState('standard');

  // Manual
  const [mode, setMode] = useState('tiny');
  const [direction, setDirection] = useState('both');
  const [symbols, setSymbols] = useState('');
  const [timeframes, setTimeframes] = useState('');
  const [families, setFamilies] = useState('');
  const [strategies, setStrategies] = useState('');
  const [aiReview, setAiReview] = useState(true);

  // Run lifecycle
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<RunStatusResponse | null>(null);
  const [ranked, setRanked] = useState<RankedResponse | null>(null);
  const [aiReviewData, setAiReviewData] = useState<AiReviewResponse | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const { data: runsData, loading: runsLoading, error: runsError, refresh: refreshRuns } =
    useApiPoll<RunsResponse>('/api/research-lab/runs', 0);

  const { post: postStyleRun, loading: styleStarting } = useApiPost<StartRunResponse>();
  const { post: postManualRun, loading: manualStarting } = useApiPost<StartRunResponse>();

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const fetchRanked = useCallback(async (runId: string) => {
    try {
      const res = await fetch(`/api/research-lab/ranked/${runId}`);
      const json = (await res.json()) as RankedResponse;
      setRanked(json);
    } catch (e) {
      showToast(`Failed to load ranked: ${(e as Error).message}`, 'error');
    }
  }, [showToast]);

  const fetchAiReview = useCallback(async (runId: string) => {
    try {
      const res = await fetch(`/api/research-lab/ai-review/${runId}`);
      const json = (await res.json()) as AiReviewResponse;
      setAiReviewData(json);
    } catch {
      // ai-review endpoint may legitimately return 404 when not generated; ignore
    }
  }, []);

  const pollRun = useCallback((runId: string) => {
    stopPolling();
    pollTimerRef.current = setInterval(async () => {
      try {
        const res = await fetch(`/api/research-lab/run/${runId}`);
        const json = (await res.json()) as RunStatusResponse;
        setRunStatus(json);
        if (json.status === 'complete') {
          stopPolling();
          await fetchRanked(runId);
          await fetchAiReview(runId);
          refreshRuns();
          showToast(`Run ${runId} complete — ${json.results_count ?? json.summary?.total ?? '?'} results`, 'success');
        } else if (json.status === 'failed') {
          stopPolling();
          refreshRuns();
          showToast(`Run ${runId} failed: ${json.error || 'unknown'}`, 'error');
        }
      } catch (e) {
        // Network blip — keep polling
        // eslint-disable-next-line no-console
        console.warn('[research-lab] poll error', e);
      }
    }, 3000);
  }, [stopPolling, fetchRanked, fetchAiReview, refreshRuns, showToast]);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const startAutopilot = useCallback(async () => {
    setRanked(null);
    setAiReviewData(null);
    setRunStatus(null);
    const res = await postStyleRun('/api/research-lab/style-run', {
      market_group: marketGroup,
      trading_style: tradingStyle,
      research_depth: researchDepth,
    });
    if (!res || res.error || !res.run_id) {
      showToast(`Autopilot failed: ${res?.error || 'unknown'}`, 'error');
      return;
    }
    setCurrentRunId(res.run_id);
    showToast(`Autopilot started: ${res.run_id} (${res.mode || 'standard'})`, 'info');
    pollRun(res.run_id);
  }, [postStyleRun, marketGroup, tradingStyle, researchDepth, showToast, pollRun]);

  const startManual = useCallback(async () => {
    setRanked(null);
    setAiReviewData(null);
    setRunStatus(null);
    const payload: Record<string, unknown> = {
      mode,
      direction,
      run_ai_review: aiReview,
    };
    const csv = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean);
    if (symbols.trim()) payload.symbols = csv(symbols);
    if (timeframes.trim()) payload.timeframes = csv(timeframes);
    if (families.trim()) payload.families = csv(families);
    if (strategies.trim()) payload.strategies = csv(strategies);
    const res = await postManualRun('/api/research-lab/run', payload);
    if (!res || res.error || !res.run_id) {
      showToast(`Run failed: ${res?.error || 'unknown'}`, 'error');
      return;
    }
    setCurrentRunId(res.run_id);
    showToast(`Run started: ${res.run_id}`, 'info');
    pollRun(res.run_id);
  }, [postManualRun, mode, direction, aiReview, symbols, timeframes, families, strategies, showToast, pollRun]);

  const openRun = useCallback(async (runId: string) => {
    setCurrentRunId(runId);
    try {
      const res = await fetch(`/api/research-lab/run/${runId}`);
      const json = (await res.json()) as RunStatusResponse;
      setRunStatus(json);
      if (json.status === 'complete') {
        await fetchRanked(runId);
        await fetchAiReview(runId);
      } else if (json.status === 'running' || json.status === 'queued') {
        pollRun(runId);
      }
    } catch (e) {
      showToast(`Failed to open run: ${(e as Error).message}`, 'error');
    }
  }, [fetchRanked, fetchAiReview, pollRun, showToast]);

  const triggerAiAnalyze = useCallback(async () => {
    if (!currentRunId) return;
    try {
      const res = await fetch(`/api/research-lab/analyze/${currentRunId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const json = (await res.json()) as AiReviewResponse;
      if (json.error) {
        showToast(`AI analysis failed: ${json.error}`, 'error');
      } else {
        setAiReviewData(json);
        showToast('AI analysis complete', 'success');
      }
    } catch (e) {
      showToast(`AI analysis failed: ${(e as Error).message}`, 'error');
    }
  }, [currentRunId, showToast]);

  const sortedRuns = useMemo(() => (runsData?.runs || []).slice(), [runsData]);

  return (
    <div className="space-y-5">
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-4">
          <div className="flex items-center gap-2 mb-2">
            <Microscope className="w-4 h-4 text-primary" />
            <h3 className="text-sm font-semibold">Research Lab</h3>
          </div>
          <p className="text-xs text-muted-foreground">
            Strategy discovery and ranking via <span className="font-mono">athena_research</span>. Endpoints under
            <span className="font-mono"> /api/research-lab/*</span>. Output writes to
            <span className="font-mono"> athena_research/output/&lt;run_id&gt;/</span>; rankings persist as
            <span className="font-mono"> ranked_strategies.csv</span>. Live runs are polled every 3 s.
          </p>
        </CardContent>
      </Card>

      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as typeof activeTab)}>
        <TabsList>
          <TabsTrigger value="autopilot" className="text-xs">One-Click Autopilot</TabsTrigger>
          <TabsTrigger value="manual" className="text-xs">Manual Run</TabsTrigger>
          <TabsTrigger value="runs" className="text-xs">Run History</TabsTrigger>
        </TabsList>

        <TabsContent value="autopilot" className="mt-3">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Rocket className="w-4 h-4 text-primary" />
                Style-driven Discovery
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center gap-2 flex-wrap">
                <Select value={marketGroup} onValueChange={setMarketGroup}>
                  <SelectTrigger className="w-[160px] h-8 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {MARKET_GROUPS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Select value={tradingStyle} onValueChange={setTradingStyle}>
                  <SelectTrigger className="w-[140px] h-8 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {STYLES.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Select value={researchDepth} onValueChange={setResearchDepth}>
                  <SelectTrigger className="w-[140px] h-8 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {DEPTHS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Button size="sm" className="h-8 gap-1 text-xs" onClick={startAutopilot} disabled={styleStarting}>
                  <Play className="w-3 h-3" />
                  {styleStarting ? 'Queuing…' : 'Run Autopilot'}
                </Button>
              </div>
              <p className="text-[10px] text-muted-foreground">
                Style profile resolves symbols / timeframes / families / strategies from
                <span className="font-mono"> RESEARCH_STYLE_PROFILES</span>. Use Manual Run for full control.
              </p>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="manual" className="mt-3">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <ListChecks className="w-4 h-4 text-primary" />
                Manual Discovery Run
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <Select value={mode} onValueChange={setMode}>
                  <SelectTrigger className="h-8 text-xs"><SelectValue placeholder="Mode" /></SelectTrigger>
                  <SelectContent>
                    {MANUAL_MODES.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Select value={direction} onValueChange={setDirection}>
                  <SelectTrigger className="h-8 text-xs"><SelectValue placeholder="Direction" /></SelectTrigger>
                  <SelectContent>
                    {DIRECTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                  </SelectContent>
                </Select>
                <div className="col-span-2 grid grid-cols-2 gap-3">
                  <Input value={symbols} onChange={(e) => setSymbols(e.target.value)} className="h-8 text-xs font-mono" placeholder="Symbols (comma-separated, e.g. BTC/USDT, EUR/USD)" />
                  <Input value={timeframes} onChange={(e) => setTimeframes(e.target.value)} className="h-8 text-xs font-mono" placeholder="Timeframes (e.g. M15, H1, H4)" />
                  <Input value={families} onChange={(e) => setFamilies(e.target.value)} className="h-8 text-xs font-mono" placeholder="Families (e.g. trend_momentum, volatility)" />
                  <Input value={strategies} onChange={(e) => setStrategies(e.target.value)} className="h-8 text-xs font-mono" placeholder="Strategies (e.g. macd_direction, bollinger_touch)" />
                </div>
                <label className="text-[11px] text-muted-foreground flex items-center gap-2 col-span-2">
                  <input type="checkbox" checked={aiReview} onChange={(e) => setAiReview(e.target.checked)} />
                  Run AI review on completion
                </label>
              </div>
              <Button size="sm" className="h-8 gap-1 text-xs" onClick={startManual} disabled={manualStarting}>
                <Play className="w-3 h-3" />
                {manualStarting ? 'Queuing…' : 'Run Discovery'}
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="runs" className="mt-3">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <History className="w-4 h-4 text-primary" />
                Run History
              </CardTitle>
            </CardHeader>
            <CardContent>
              {runsError && <ErrorBanner message={runsError} onRetry={refreshRuns} />}
              {runsLoading ? <Skeleton className="h-32 w-full" /> : (
                <ScrollArea className="h-[420px]">
                  {sortedRuns.length === 0 ? (
                    <div className="text-xs text-muted-foreground text-center py-12">No prior runs</div>
                  ) : (
                    <table className="w-full text-left">
                      <thead>
                        <tr className="border-b border-border/40">
                          <th className="text-[10px] uppercase py-2 text-muted-foreground">Run</th>
                          <th className="text-[10px] uppercase py-2 text-muted-foreground">Status</th>
                          <th className="text-[10px] uppercase py-2 text-muted-foreground">Mode</th>
                          <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Results</th>
                          <th className="text-[10px] uppercase py-2 text-muted-foreground">Started</th>
                          <th className="text-[10px] uppercase py-2 text-muted-foreground" />
                        </tr>
                      </thead>
                      <tbody>
                        {sortedRuns.map((r) => (
                          <tr key={r.run_id} className="border-b border-border/20 hover:bg-muted/30">
                            <td className="py-2 text-[11px] font-mono">{r.run_id}</td>
                            <td className="py-2"><Badge className={`${statusBadge(r.status)} text-[10px]`}>{r.status || '—'}</Badge></td>
                            <td className="py-2 text-[10px] text-muted-foreground">{r.mode || r.trading_style || '—'}</td>
                            <td className="py-2 text-[10px] text-right font-mono">{r.results_count ?? r.total ?? '—'}</td>
                            <td className="py-2 text-[10px] text-muted-foreground">{r.start_time ? new Date(r.start_time).toLocaleString() : '—'}</td>
                            <td className="py-2 text-right">
                              <Button size="sm" variant="outline" className="h-7 text-[10px]" onClick={() => openRun(String(r.run_id || ''))}>
                                Open
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </ScrollArea>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Active run detail */}
      {currentRunId && (
        <>
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center justify-between">
                <span>Run · <span className="font-mono text-xs">{currentRunId}</span></span>
                <Badge className={`${statusBadge(runStatus?.status)} text-[10px]`}>{runStatus?.status || '—'}</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {runStatus?.error && (
                <div className="p-2 rounded bg-short/10 border border-short/40 text-[11px] text-short">
                  {runStatus.error}
                </div>
              )}
              {runStatus?.summary && (
                <div className="grid grid-cols-4 gap-3">
                  <Stat title="Total" value={runStatus.summary.total ?? '—'} />
                  <Stat title="Strong" value={runStatus.summary.strong ?? 0} accent="text-long" />
                  <Stat title="Weak" value={runStatus.summary.weak ?? 0} accent="text-warning" />
                  <Stat title="Reject" value={runStatus.summary.reject ?? 0} accent="text-short" />
                </div>
              )}
              <div className="flex items-center gap-2 flex-wrap">
                {currentRunId && runStatus?.status === 'complete' && (
                  <>
                    <Button size="sm" variant="outline" className="h-7 gap-1 text-[10px]" onClick={() => fetchRanked(currentRunId)}>
                      Refresh ranked
                    </Button>
                    <Button size="sm" variant="outline" className="h-7 gap-1 text-[10px]" onClick={triggerAiAnalyze}>
                      <Brain className="w-3 h-3" /> Run AI analysis
                    </Button>
                    <a
                      className="text-[10px] underline text-muted-foreground"
                      href={`/api/research-lab/download/${currentRunId}/research_report.md`}
                      target="_blank" rel="noreferrer"
                    >
                      Download report
                    </a>
                  </>
                )}
              </div>
            </CardContent>
          </Card>

          {ranked && (ranked.ranked || []).length > 0 && (
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold">Ranked Strategies (top 50)</CardTitle>
              </CardHeader>
              <CardContent>
                <ScrollArea className="h-[420px]">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="border-b border-border/40">
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Symbol</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">TF</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Family</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Strategy</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Dir</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Status</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Trades</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">WR</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">PF</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">SQN</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">DD</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(ranked.ranked || []).map((row, i) => {
                        const strong = row.status === 'STRONG_CANDIDATE';
                        const weak = row.status === 'WEAK_CANDIDATE';
                        return (
                          <tr key={i} className="border-b border-border/20 hover:bg-muted/30">
                            <td className="py-2 text-[11px] font-mono">{row.symbol || '—'}</td>
                            <td className="py-2 text-[10px]">{row.timeframe || '—'}</td>
                            <td className="py-2 text-[10px]">{row.family || '—'}</td>
                            <td className="py-2 text-[10px]">{row.strategy || '—'}</td>
                            <td className="py-2 text-[10px] uppercase">{row.direction || '—'}</td>
                            <td className="py-2 text-[10px]">
                              <Badge className={`text-[10px] ${strong ? 'badge-long' : weak ? 'badge-neutral' : 'badge-short'}`}>
                                {row.status || '—'}
                              </Badge>
                            </td>
                            <td className="py-2 text-[10px] font-mono text-right">{row.trades ?? '—'}</td>
                            <td className="py-2 text-[10px] font-mono text-right">{row.win_rate != null ? `${fmtNum(row.win_rate, 1)}%` : '—'}</td>
                            <td className="py-2 text-[10px] font-mono text-right">{row.profit_factor != null ? fmtNum(row.profit_factor, 2) : '—'}</td>
                            <td className="py-2 text-[10px] font-mono text-right">{row.sqn != null ? fmtNum(row.sqn, 2) : '—'}</td>
                            <td className="py-2 text-[10px] font-mono text-right">{row.max_drawdown != null ? fmtNum(row.max_drawdown, 1) : '—'}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </ScrollArea>
              </CardContent>
            </Card>
          )}

          {ranked?.note && (!ranked.ranked || ranked.ranked.length === 0) && (
            <div className="p-3 rounded-md bg-muted/20 text-[11px] text-muted-foreground">
              {ranked.note}
            </div>
          )}

          {aiReviewData && (
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <Brain className="w-4 h-4 text-primary" />
                  AI Review
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-xs">
                {aiReviewData.summary && (
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">Summary</p>
                    <p className="whitespace-pre-wrap">{aiReviewData.summary}</p>
                  </div>
                )}
                {aiReviewData.recommendation && (
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">Recommendation</p>
                    <p className="whitespace-pre-wrap">{aiReviewData.recommendation}</p>
                  </div>
                )}
                {aiReviewData.ai_review && (
                  <pre className="text-[10px] font-mono whitespace-pre-wrap p-2 bg-muted/20 rounded border border-border/40 max-h-72 overflow-y-auto">
                    {aiReviewData.ai_review}
                  </pre>
                )}
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function Stat({ title, value, accent }: { title: string; value: string | number; accent?: string }) {
  return (
    <div className="p-3 rounded-md bg-muted/30">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{title}</p>
      <p className={`text-lg font-mono font-bold mt-1 ${accent || ''}`}>{value}</p>
    </div>
  );
}
