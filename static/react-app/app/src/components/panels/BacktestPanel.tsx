import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Archive,
  BarChart3,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleStop,
  Clock3,
  Database,
  Download,
  FlaskConical,
  GitCompareArrows,
  Layers3,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  Sigma,
  SlidersHorizontal,
  TimerReset,
  XCircle,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { useStore } from '@/hooks/useStore';
import { cn, fmtNum } from '@/lib/utils';
import EquityAreaChart from '@/components/shared/EquityAreaChart';
import type {
  ApiEnvelope,
  BacktestCapabilities,
  BacktestMetrics,
  BacktestRun,
  BacktestV2Engine,
  BacktestV2Mode,
  ComparisonResult,
  DatasetManifest,
  PreflightResult,
  TradePage,
} from '@/lib/backtestingV2';
import { assuranceTone, hasExplicitUniverse, statusTone } from '@/lib/backtestingV2';
import { buildBacktestRequest, type BacktestEngineKey } from '@/lib/backtestPayload';

type WorkstationTab = 'v3' | 'run' | 'jobs' | 'results' | 'compare' | 'data';

type V3HistoryRow = {
  id?: number;
  run_date?: string;
  pair?: string;
  symbol?: string;
  engine?: string;
  style?: string;
  trades?: number;
  win_rate?: number;
  profit_factor?: number;
  expectancy?: number;
  sqn?: number;
  total_r?: number;
  verdict?: string;
  wall_time_sec?: number;
};

type V3RunResult = {
  error?: string;
  totalTrades?: number;
  winRate?: number;
  profitFactor?: number;
  expectancyR?: number;
  sqn?: number;
  totalR?: number;
  maxDrawdownR?: number;
  entryTimeframe?: string;
  timeframePolicyVersion?: string;
  policyKey?: string;
  structureTf?: string;
  setupTf?: string;
  triggerTf?: string;
  executionTf?: string;
  m5Policy?: string;
  missingPolicyTimeframes?: string[];
  validation?: { verdict?: string; trialCount?: number };
  metricsV3?: { deflatedSqn?: number };
  wallTimeSec?: number;
  funnel?: { barsEvaluated?: number };
  runId?: number | string;
};

function isoDateOffset(days: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function compactHash(value?: string | null, size = 12): string {
  if (!value) return '—';
  return value.length > size ? `${value.slice(0, size)}…` : value;
}

function formatDuration(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}m ${remainder}s`;
}

function jobElapsedSeconds(run: BacktestRun): number {
  const started = Date.parse(run.started_at || run.created_at);
  const ended = run.completed_at ? Date.parse(run.completed_at) : Date.now();
  return Number.isFinite(started) && Number.isFinite(ended) ? Math.max(0, (ended - started) / 1000) : 0;
}

function jobEtaSeconds(run: BacktestRun, elapsed: number): number | null {
  if (run.progress_completed <= 0 || run.progress_total <= run.progress_completed) return null;
  return elapsed * (run.progress_total - run.progress_completed) / run.progress_completed;
}

function metricValue(value: number | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return value.toFixed(digits);
}

function AssuranceBadge({ value }: { value?: string | null }) {
  const state = value || 'EXPLORATORY';
  return (
    <Badge variant="outline" className={cn('text-[10px] tracking-[0.12em]', assuranceTone(state))}>
      <ShieldCheck className="mr-1 h-3 w-3" />
      {state.replaceAll('_', ' ')}
    </Badge>
  );
}

function MetricTile({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: string }) {
  return (
    <div className="relative overflow-hidden rounded-xl border border-border/60 bg-black/20 p-3 stat-card-top-line">
      <div className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">{label}</div>
      <div className={cn('mt-1 font-mono text-xl font-semibold text-foreground', tone)}>{value}</div>
      {note && <div className="mt-1 text-[10px] text-muted-foreground">{note}</div>}
    </div>
  );
}

type ResultIssue = { kind: 'EXCLUDED' | 'FAILED'; row: Record<string, unknown> };

function issueValue(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

function issueScope(row: Record<string, unknown>): string {
  const task = row.task && typeof row.task === 'object' && !Array.isArray(row.task)
    ? row.task as Record<string, unknown>
    : {};
  const engine = issueValue(row, 'engine') || issueValue(task, 'engine');
  const symbol = issueValue(row, 'symbol') || issueValue(task, 'symbol');
  const timeframe = issueValue(row, 'timeframe');
  const provider = issueValue(row, 'provider');
  return [engine ? `Engine ${engine}` : '', symbol, timeframe, provider].filter(Boolean).join(' · ') || 'Run';
}

function SectionTitle({ icon: Icon, title, note }: { icon: typeof Activity; title: string; note?: string }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div>
        <div className="flex items-center gap-2 panel-header-title">
          <Icon className="h-4 w-4 text-primary" />
          {title}
        </div>
        {note && <p className="mt-1 text-xs text-muted-foreground">{note}</p>}
      </div>
    </div>
  );
}

function BacktestPanel() {
  const { showToast } = useStore();
  const [tab, setTab] = useState<WorkstationTab>('v3');
  const [engineMode, setEngineMode] = useState<'A' | 'B' | 'AB'>('AB');
  const [styleA, setStyleA] = useState('auto');
  const [styleB, setStyleB] = useState('auto');
  const [mode, setMode] = useState<BacktestV2Mode>('CERTIFIED');
  const [startDate, setStartDate] = useState(isoDateOffset(-365 * 3));
  const [endDate, setEndDate] = useState(isoDateOffset(0));
  const [symbolSearch, setSymbolSearch] = useState('');
  const [assetFilter, setAssetFilter] = useState('all');
  const [selectedSymbols, setSelectedSymbols] = useState<Set<string>>(new Set());
  const [validationEnabled, setValidationEnabled] = useState(true);
  const [portfolioEnabled, setPortfolioEnabled] = useState(false);
  const [spreadBps, setSpreadBps] = useState(1);
  const [commissionBps, setCommissionBps] = useState(0.5);
  const [slippageBps, setSlippageBps] = useState(0.5);
  const [fundingBps, setFundingBps] = useState(0);
  const [swapBps, setSwapBps] = useState(0);
  const [comparisonFamily, setComparisonFamily] = useState('');
  const [variantId, setVariantId] = useState('');
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [tradeOffset, setTradeOffset] = useState(0);
  const [compareRunIds, setCompareRunIds] = useState<Set<string>>(new Set());
  const [latestComparison, setLatestComparison] = useState<ComparisonResult | null>(null);
  const [v3Engine, setV3Engine] = useState<BacktestEngineKey>('A');
  const [v3Style, setV3Style] = useState('intraday');
  const [v3Pair, setV3Pair] = useState('EUR/USD');
  const [v3Result, setV3Result] = useState<V3RunResult | null>(null);

  const capabilitiesPoll = useApiPoll<ApiEnvelope<BacktestCapabilities>>('/api/v2/backtest-capabilities', 60_000);
  const jobsPoll = useApiPoll<ApiEnvelope<BacktestRun[]>>('/api/v2/backtests?limit=200', 2_000);
  const datasetsPoll = useApiPoll<ApiEnvelope<DatasetManifest[]>>('/api/v2/backtest-datasets?limit=100', 15_000);
  const comparisonsPoll = useApiPoll<ApiEnvelope<Array<{ comparisonId: string; result: ComparisonResult }>>>('/api/v2/backtest-comparisons?limit=100', 15_000);
  const v3HistoryPoll = useApiPoll<V3HistoryRow[]>('/api/v3/backtest-history', 10_000);
  const v3RunMutation = useApiPost<V3RunResult>();
  const selectedRunPoll = useApiPoll<ApiEnvelope<BacktestRun>>(
    selectedRunId ? `/api/v2/backtests/${encodeURIComponent(selectedRunId)}` : '/api/v2/backtests/__none__',
    2_000,
    Boolean(selectedRunId),
  );
  const tradesPoll = useApiPoll<ApiEnvelope<TradePage>>(
    selectedRunId ? `/api/v2/backtests/${encodeURIComponent(selectedRunId)}/trades?limit=50&offset=${tradeOffset}` : '/api/v2/backtests/__none__/trades',
    0,
    Boolean(selectedRunId && selectedRunPoll.data?.data?.status === 'COMPLETED'),
  );
  const preflightMutation = useApiPost<ApiEnvelope<PreflightResult & { success?: boolean }>>();
  const runMutation = useApiPost<ApiEnvelope<{ runId: string; status: string; reused: boolean }>>();
  const cancelMutation = useApiPost<ApiEnvelope<{ runId: string; cancelRequested: boolean }>>();
  const compareMutation = useApiPost<ApiEnvelope<ComparisonResult>>();

  const capabilities = capabilitiesPoll.data?.data;
  const jobs = jobsPoll.data?.data || [];
  const datasets = datasetsPoll.data?.data || [];
  const selectedRun = selectedRunPoll.data?.data || jobs.find((run) => run.run_id === selectedRunId) || null;
  const result = selectedRun?.result || null;
  const resultIssues: ResultIssue[] = result ? [
    ...(result.exclusions || []).map((row) => ({ kind: 'EXCLUDED' as const, row })),
    ...(result.failures || []).map((row) => ({ kind: 'FAILED' as const, row })),
  ] : [];
  const hasEvaluatedTasks = Boolean(result?.tasks?.length);
  const tradePage = tradesPoll.data?.data;
  const explicitUniverseReady = hasExplicitUniverse(selectedSymbols);
  const engines = useMemo<BacktestV2Engine[]>(
    () => (engineMode === 'AB' ? ['A', 'B'] : [engineMode]),
    [engineMode],
  );

  useEffect(() => {
    if (mode === 'CERTIFIED' && !validationEnabled) setValidationEnabled(true);
  }, [mode, validationEnabled]);

  const filteredUniverse = useMemo(() => {
    const query = symbolSearch.trim().toUpperCase();
    return (capabilities?.universe || []).filter((item) => {
      if (assetFilter !== 'all' && item.assetType !== assetFilter) return false;
      if (!query) return true;
      return item.symbol.toUpperCase().includes(query) || item.providerSymbol.toUpperCase().includes(query);
    });
  }, [capabilities, symbolSearch, assetFilter]);

  const requestPayload = useMemo<Record<string, unknown>>(
    () => ({
      engines,
      styles: { A: styleA, B: styleB },
      symbols: Array.from(selectedSymbols).sort(),
      startDate,
      endDate,
      mode,
      validation: {
        enabled: validationEnabled,
        holdoutFraction: 0.2,
        folds: 5,
        initialTrainFraction: 0.4,
        oosFraction: 0.08,
        maxHoldBars: 240,
        bootstrapSamples: 1_000,
      },
      costModel: {
        version: 'athena-cost-model-v2.1',
        spreadBps,
        commissionBpsPerSide: commissionBps,
        slippageBps,
        fundingBpsPerDay: fundingBps,
        swapBpsPerDay: swapBps,
        rollBps: 0,
        dividendMode: 'adjusted_series',
        source: 'operator_declared_ui',
      },
      portfolio: {
        enabled: portfolioEnabled,
        initialCapital: 100_000,
        riskPerTrade: 0.01,
        maxConcurrentPositions: 10,
      },
      comparisonFamily: comparisonFamily.trim() || undefined,
      variantId: variantId.trim() || undefined,
    }),
    [engines, styleA, styleB, selectedSymbols, startDate, endDate, mode, validationEnabled, spreadBps, commissionBps, slippageBps, fundingBps, swapBps, portfolioEnabled, comparisonFamily, variantId],
  );

  useEffect(() => {
    setPreflight(null);
  }, [requestPayload]);

  useEffect(() => {
    if (!selectedRunId && jobs.length) {
      setSelectedRunId(jobs[0].run_id);
    }
  }, [jobs, selectedRunId]);

  const toggleSymbol = useCallback((symbol: string) => {
    setSelectedSymbols((current) => {
      const next = new Set(current);
      if (next.has(symbol)) next.delete(symbol);
      else next.add(symbol);
      return next;
    });
  }, []);

  const selectVisible = useCallback(() => {
    setSelectedSymbols((current) => {
      const next = new Set(current);
      filteredUniverse.forEach((item) => next.add(item.symbol));
      return next;
    });
  }, [filteredUniverse]);

  const runPreflight = useCallback(async () => {
    if (!explicitUniverseReady) {
      showToast('Select at least one instrument. V2 never interprets an empty universe as “all”.', 'error');
      return;
    }
    const response = await preflightMutation.post('/api/v2/backtest-datasets', requestPayload);
    if (!response?.success) {
      showToast(response?.error || 'Preflight failed', 'error');
      return;
    }
    setPreflight(response.data);
    showToast(`Preflight complete: ${response.data.certification.replaceAll('_', ' ')}`, 'success');
  }, [preflightMutation, requestPayload, explicitUniverseReady, showToast]);

  const submitRun = useCallback(async () => {
    if (!preflight?.instruments.length) return;
    const response = await runMutation.post('/api/v2/backtests', requestPayload);
    if (!response?.success || !response.data?.runId) {
      showToast(response?.error || 'Run submission failed', 'error');
      return;
    }
    setSelectedRunId(response.data.runId);
    setTradeOffset(0);
    setTab('jobs');
    await jobsPoll.refresh();
    showToast(response.data.reused ? 'Reused identical certified result' : 'Backtest queued', 'success');
  }, [preflight, runMutation, requestPayload, jobsPoll, showToast]);

  const cancelRun = useCallback(async (runId: string) => {
    const response = await cancelMutation.post(`/api/v2/backtests/${encodeURIComponent(runId)}/cancel`, {});
    if (response?.success) {
      showToast('Cancellation requested', 'success');
      await jobsPoll.refresh();
    } else {
      showToast(response?.error || 'Run cannot be cancelled', 'error');
    }
  }, [cancelMutation, jobsPoll, showToast]);

  const retryRun = useCallback(async (run: BacktestRun) => {
    const payload: Record<string, unknown> = {
      engines: run.request.engines,
      styles: run.request.styles,
      symbols: run.request.symbols,
      startDate: run.request.start_date,
      endDate: run.request.end_date,
      mode: run.request.mode,
      costModel: run.request.cost_model,
      validation: run.request.validation,
      portfolio: run.request.portfolio,
      comparisonFamily: run.request.comparison_family,
      variantId: run.request.variant_id,
      randomSeed: run.request.random_seed,
      force: true,
    };
    const response = await runMutation.post('/api/v2/backtests', payload);
    if (response?.data?.runId) {
      setSelectedRunId(response.data.runId);
      showToast('Retry queued with force=true', 'success');
      await jobsPoll.refresh();
    }
  }, [runMutation, jobsPoll, showToast]);

  const createComparison = useCallback(async () => {
    if (compareRunIds.size < 2) {
      showToast('Select at least two completed runs', 'error');
      return;
    }
    const response = await compareMutation.post('/api/v2/backtest-comparisons', {
      runIds: Array.from(compareRunIds),
    });
    if (!response?.success) {
      showToast(response?.error || 'Comparison failed', 'error');
      return;
    }
    setLatestComparison(response.data);
    await comparisonsPoll.refresh();
    showToast(response.data.compatible ? 'Comparison created' : 'Runs are contract-incompatible', response.data.compatible ? 'success' : 'error');
  }, [compareRunIds, compareMutation, comparisonsPoll, showToast]);

  const selectRun = useCallback((runId: string, destination: WorkstationTab = 'results') => {
    setSelectedRunId(runId);
    setTradeOffset(0);
    setTab(destination);
  }, []);

  const runV3Backtest = useCallback(async () => {
    const pair = v3Pair.trim();
    if (!pair) {
      showToast('Select a pair for the V3 backtest', 'error');
      return;
    }
    const request = buildBacktestRequest({ engine: v3Engine, pair, style: v3Style });
    const response = await v3RunMutation.post(request.endpoint, {
      ...request.payload,
      persist: true,
    });
    if (!response || response.error) {
      showToast(response?.error || v3RunMutation.error || 'V3 backtest failed', 'error');
      setV3Result(response);
      return;
    }
    setV3Result(response);
    await v3HistoryPoll.refresh();
    showToast(
      `V3 Engine ${v3Engine} · ${pair}: ${response.totalTrades ?? 0} trades · setup ${response.setupTf || '—'} · trigger ${response.triggerTf || '—'}`,
      'success',
    );
  }, [v3Pair, v3Engine, v3Style, v3RunMutation, v3HistoryPoll, showToast]);

  const activeComparison = latestComparison || comparisonsPoll.data?.data?.[0]?.result || null;
  const v3History = Array.isArray(v3HistoryPoll.data) ? v3HistoryPoll.data : [];

  return (
    <div className="space-y-5 p-4 md:p-6">
      <div className="relative overflow-hidden rounded-2xl border border-primary/20 bg-card/70 p-5 backdrop-blur-xl">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_8%_0%,hsl(var(--primary)/0.16),transparent_38%),radial-gradient(circle_at_90%_20%,hsl(var(--info)/0.10),transparent_34%)]" />
        <div className="relative flex flex-col justify-between gap-4 lg:flex-row lg:items-center">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <FlaskConical className="h-5 w-5 text-primary" />
              <h1 className="text-lg font-semibold tracking-[0.08em] text-foreground">A/B RESEARCH WORKSTATION</h1>
              <Badge variant="outline" className="border-emerald-400/30 bg-emerald-400/10 text-[10px] text-emerald-200">V3 LIVE</Badge>
              <Badge variant="outline" className="border-violet-400/30 bg-violet-400/10 text-[10px] text-violet-200">V2 PLATFORM</Badge>
            </div>
            <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
              Engine A/B backtests use the universal TF policy (D1/H4/H4/H1/M15). V3 is the live single-pair runner; Platform tabs keep the immutable multi-symbol v2 research stack.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[10px]">
            <Badge variant="outline" className="border-border/70 bg-black/20 font-mono">POLICY timeframe_policy.v4</Badge>
            <Badge variant="outline" className="border-border/70 bg-black/20 font-mono">PLATFORM {capabilities?.platformVersion || '—'}</Badge>
            <Badge variant="outline" className="border-border/70 bg-black/20 font-mono">SIM {capabilities?.simulatorVersion || '—'}</Badge>
          </div>
        </div>
      </div>

      <Tabs value={tab} onValueChange={(value) => setTab(value as WorkstationTab)} className="space-y-4">
        <TabsList className="grid h-auto w-full grid-cols-6 rounded-xl border border-border/60 bg-card/60 p-1">
          <TabsTrigger value="v3" className="gap-2 py-2.5"><Play className="h-3.5 w-3.5" />V3 Run</TabsTrigger>
          <TabsTrigger value="run" className="gap-2 py-2.5"><SlidersHorizontal className="h-3.5 w-3.5" />Platform</TabsTrigger>
          <TabsTrigger value="jobs" className="gap-2 py-2.5"><Clock3 className="h-3.5 w-3.5" />Jobs</TabsTrigger>
          <TabsTrigger value="results" className="gap-2 py-2.5"><BarChart3 className="h-3.5 w-3.5" />Results</TabsTrigger>
          <TabsTrigger value="compare" className="gap-2 py-2.5"><GitCompareArrows className="h-3.5 w-3.5" />Compare</TabsTrigger>
          <TabsTrigger value="data" className="gap-2 py-2.5"><Database className="h-3.5 w-3.5" />Data</TabsTrigger>
        </TabsList>

        <TabsContent value="v3" className="space-y-4">
          <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
            <Card className="panel-glass">
              <CardHeader>
                <SectionTitle
                  icon={Play}
                  title="Backtest V3 (live scoring policy)"
                  note="Uses /api/v3/backtest · same resolve_timeframe_policy ladder as live Engine A/B (D1/H4/H4/H1/M15)."
                />
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-3">
                  {(['A', 'B'] as const).map((value) => (
                    <button
                      key={value}
                      type="button"
                      onClick={() => setV3Engine(value)}
                      className={cn(
                        'rounded-xl border p-3 text-left transition',
                        v3Engine === value ? 'border-primary/55 bg-primary/12' : 'border-border/60 bg-black/15 hover:border-primary/30',
                      )}
                    >
                      <div className="text-sm font-semibold">Engine {value}</div>
                      <div className="mt-1 text-[10px] text-muted-foreground">
                        {value === 'A' ? 'POST /api/v3/backtest' : 'POST /api/v3/backtest-naked'}
                      </div>
                    </button>
                  ))}
                  <div className="space-y-2">
                    <Label>Style</Label>
                    <Select value={v3Style} onValueChange={setV3Style}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {['intraday', 'swing'].map((style) => (
                          <SelectItem key={style} value={style}>{style}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div className="space-y-2">
                  <Label>Pair (display or symbol)</Label>
                  <Input
                    value={v3Pair}
                    onChange={(event) => setV3Pair(event.target.value)}
                    placeholder="EUR/USD"
                    list="v3-backtest-pairs"
                  />
                  <datalist id="v3-backtest-pairs">
                    {(capabilities?.universe || []).slice(0, 200).map((item) => (
                      <option key={item.symbol} value={item.symbol} />
                    ))}
                  </datalist>
                </div>
                <div className="rounded-xl border border-border/60 bg-black/15 p-3 text-[11px] text-muted-foreground">
                  Fixed policy roles: <span className="font-mono text-foreground">regime D1 · bias H4 · structure H4 · setup H1 · trigger M15</span>
                </div>
                <Button className="w-full" onClick={runV3Backtest} disabled={v3RunMutation.loading}>
                  {v3RunMutation.loading ? (
                    <><RefreshCw className="mr-2 h-4 w-4 animate-spin" />Running V3…</>
                  ) : (
                    <><Play className="mr-2 h-4 w-4" />Run V3 backtest</>
                  )}
                </Button>
                {v3RunMutation.error && (
                  <div className="rounded-lg border border-rose-400/30 bg-rose-400/10 p-3 text-xs text-rose-200">{v3RunMutation.error}</div>
                )}
              </CardContent>
            </Card>

            <Card className="panel-glass">
              <CardHeader>
                <SectionTitle icon={BarChart3} title="Latest V3 result" note="Policy provenance is stamped on every successful run." />
              </CardHeader>
              <CardContent className="space-y-3">
                {!v3Result ? (
                  <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
                    Run a V3 backtest to see trades, SQN, and TF policy fields.
                  </div>
                ) : v3Result.error ? (
                  <div className="rounded-xl border border-rose-400/30 bg-rose-400/10 p-4 text-sm text-rose-200">
                    {v3Result.error}
                    {v3Result.missingPolicyTimeframes?.length ? (
                      <div className="mt-2 font-mono text-xs">Missing: {v3Result.missingPolicyTimeframes.join(', ')}</div>
                    ) : null}
                  </div>
                ) : (
                  <>
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                      <MetricTile label="Trades" value={String(v3Result.totalTrades ?? 0)} />
                      <MetricTile label="SQN" value={metricValue(v3Result.sqn, 2)} />
                      <MetricTile label="Total R" value={metricValue(v3Result.totalR, 2)} />
                      <MetricTile label="Win rate" value={v3Result.winRate == null ? '—' : `${(v3Result.winRate * 100).toFixed(1)}%`} />
                      <MetricTile label="Expectancy" value={metricValue(v3Result.expectancyR, 3)} />
                      <MetricTile label="Wall time" value={formatDuration(v3Result.wallTimeSec)} />
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      <Badge variant="outline" className="font-mono text-[10px]">{v3Result.timeframePolicyVersion || 'policy?'}</Badge>
                      <Badge variant="outline" className="font-mono text-[10px]">struct {v3Result.structureTf || '—'}</Badge>
                      <Badge variant="outline" className="font-mono text-[10px]">setup {v3Result.setupTf || v3Result.entryTimeframe || '—'}</Badge>
                      <Badge variant="outline" className="font-mono text-[10px]">trigger {v3Result.triggerTf || '—'}</Badge>
                      <Badge variant="outline" className="font-mono text-[10px]">m5 {v3Result.m5Policy || '—'}</Badge>
                      {v3Result.validation?.verdict && (
                        <Badge variant="outline" className="text-[10px]">{v3Result.validation.verdict}</Badge>
                      )}
                    </div>
                    {v3Result.policyKey && (
                      <div className="font-mono text-[10px] text-muted-foreground">{v3Result.policyKey}</div>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          </div>

          <Card className="panel-glass">
            <CardHeader>
              <div className="flex items-center justify-between">
                <SectionTitle icon={Database} title="V3 history" note="Rows from backtest_runs_v3 via /api/v3/backtest-history" />
                <Button variant="outline" size="sm" onClick={v3HistoryPoll.refresh}>
                  <RefreshCw className={cn('mr-2 h-3.5 w-3.5', v3HistoryPoll.isRefreshing && 'animate-spin')} />
                  Refresh
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {v3HistoryPoll.loading && !v3History.length ? (
                <Skeleton className="h-40 w-full" />
              ) : !v3History.length ? (
                <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">No V3 runs stored yet.</div>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-border/50">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>When</TableHead>
                        <TableHead>Pair</TableHead>
                        <TableHead>Engine</TableHead>
                        <TableHead>Style</TableHead>
                        <TableHead>Trades</TableHead>
                        <TableHead>SQN</TableHead>
                        <TableHead>Total R</TableHead>
                        <TableHead>Verdict</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {v3History.slice(0, 50).map((row) => (
                        <TableRow key={`${row.id}-${row.pair}-${row.run_date}`}>
                          <TableCell className="text-[10px] text-muted-foreground">{row.run_date?.replace('T', ' ').slice(0, 19) || '—'}</TableCell>
                          <TableCell className="font-medium">{row.pair || row.symbol}</TableCell>
                          <TableCell>{row.engine}</TableCell>
                          <TableCell className="text-[10px] uppercase">{row.style}</TableCell>
                          <TableCell className="font-mono">{row.trades ?? '—'}</TableCell>
                          <TableCell className="font-mono">{row.sqn == null ? '—' : Number(row.sqn).toFixed(2)}</TableCell>
                          <TableCell className="font-mono">{row.total_r == null ? '—' : Number(row.total_r).toFixed(2)}</TableCell>
                          <TableCell className="text-[10px]">{row.verdict || '—'}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="run" className="space-y-4">
          <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
            <Card className="panel-glass">
              <CardHeader><SectionTitle icon={SlidersHorizontal} title="Experiment contract" note="Every setting is frozen into the run hash. Engine outputs are never blended." /></CardHeader>
              <CardContent className="space-y-5">
                <div className="grid gap-3 md:grid-cols-3">
                  {(['A', 'B', 'AB'] as const).map((value) => (
                    <button
                      key={value}
                      type="button"
                      onClick={() => setEngineMode(value)}
                      className={cn(
                        'rounded-xl border p-3 text-left transition',
                        engineMode === value ? 'border-primary/55 bg-primary/12 shadow-[0_0_20px_hsl(var(--primary)/0.10)]' : 'border-border/60 bg-black/15 hover:border-primary/30',
                      )}
                    >
                      <div className="text-sm font-semibold">{value === 'AB' ? 'Engine A + B' : `Engine ${value}`}</div>
                      <div className="mt-1 text-[10px] text-muted-foreground">{value === 'AB' ? 'Two independent result sets' : value === 'A' ? 'Setup evaluator V3' : 'Structure snapshot evaluator'}</div>
                    </button>
                  ))}
                </div>

                <div className="grid gap-4 md:grid-cols-2">
                  {engines.includes('A') && (
                    <div className="space-y-2">
                      <Label>Engine A style</Label>
                      <Select value={styleA} onValueChange={setStyleA}>
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>{['auto', 'intraday', 'swing'].map((style) => <SelectItem key={style} value={style}>{style}</SelectItem>)}</SelectContent>
                      </Select>
                    </div>
                  )}
                  {engines.includes('B') && (
                    <div className="space-y-2">
                      <Label>Engine B style</Label>
                      <Select value={styleB} onValueChange={setStyleB}>
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>{['auto', 'scalp', 'intraday', 'swing'].map((style) => <SelectItem key={style} value={style}>{style}</SelectItem>)}</SelectContent>
                      </Select>
                    </div>
                  )}
                </div>

                <div className="grid gap-4 md:grid-cols-3">
                  <div className="space-y-2"><Label>Start date</Label><Input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></div>
                  <div className="space-y-2"><Label>End date</Label><Input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></div>
                  <div className="space-y-2">
                    <Label>Assurance mode</Label>
                    <Select value={mode} onValueChange={(value) => setMode(value as BacktestV2Mode)}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent><SelectItem value="CERTIFIED">Certified</SelectItem><SelectItem value="EXPLORATORY">Exploratory</SelectItem></SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="rounded-xl border border-border/60 bg-black/15 p-4">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div><div className="text-sm font-medium">Explicit universe</div><div className="text-[10px] text-muted-foreground">{selectedSymbols.size} selected · empty never means all</div></div>
                    <div className="flex gap-2"><Button variant="outline" size="sm" onClick={selectVisible}>Select visible</Button><Button variant="ghost" size="sm" onClick={() => setSelectedSymbols(new Set())}>Clear</Button></div>
                  </div>
                  <div className="grid gap-2 md:grid-cols-[1fr_180px]">
                    <div className="relative"><Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" /><Input value={symbolSearch} onChange={(event) => setSymbolSearch(event.target.value)} placeholder="Search symbol or provider code" className="pl-9" /></div>
                    <Select value={assetFilter} onValueChange={setAssetFilter}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All asset classes</SelectItem>{(capabilities?.assetTypes || []).map((asset) => <SelectItem key={asset} value={asset}>{asset}</SelectItem>)}</SelectContent></Select>
                  </div>
                  <ScrollArea className="mt-3 h-56 rounded-lg border border-border/50">
                    <div className="grid gap-px bg-border/30 sm:grid-cols-2 lg:grid-cols-3">
                      {filteredUniverse.map((item) => {
                        const checked = selectedSymbols.has(item.symbol);
                        return (
                          <button key={`${item.symbol}-${item.assetType}`} type="button" onClick={() => toggleSymbol(item.symbol)} className={cn('flex items-center gap-3 bg-card/90 p-3 text-left hover:bg-accent/60', checked && 'bg-primary/10')}>
                            <Checkbox checked={checked} aria-label={`Select ${item.symbol}`} />
                            <span className="min-w-0"><span className="block truncate text-xs font-semibold">{item.symbol}</span><span className="block truncate text-[9px] uppercase tracking-wider text-muted-foreground">{item.assetType} · {item.providerSymbol}</span></span>
                          </button>
                        );
                      })}
                    </div>
                  </ScrollArea>
                </div>
              </CardContent>
            </Card>

            <div className="space-y-4">
              <Card className="panel-glass">
                <CardHeader><SectionTitle icon={Sigma} title="Validation & cost model" note="Certified defaults are fixed; costs are visible and non-zero." /></CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex items-center justify-between rounded-lg border border-border/50 bg-black/15 p-3"><div><div className="text-xs font-medium">Anchored walk-forward + holdout</div><div className="text-[10px] text-muted-foreground">40% train · 5 × 8% OOS · final 20% locked{mode === 'CERTIFIED' ? ' · required' : ''}</div></div><Switch checked={validationEnabled} onCheckedChange={setValidationEnabled} disabled={mode === 'CERTIFIED'} /></div>
                  <div className="grid grid-cols-3 gap-3">
                    <div className="space-y-1"><Label className="text-[10px]">Spread bps</Label><Input type="number" min="0" step="0.1" value={spreadBps} onChange={(event) => setSpreadBps(Number(event.target.value))} /></div>
                    <div className="space-y-1"><Label className="text-[10px]">Commission / side</Label><Input type="number" min="0" step="0.1" value={commissionBps} onChange={(event) => setCommissionBps(Number(event.target.value))} /></div>
                    <div className="space-y-1"><Label className="text-[10px]">Slippage bps</Label><Input type="number" min="0" step="0.1" value={slippageBps} onChange={(event) => setSlippageBps(Number(event.target.value))} /></div>
                    <div className="space-y-1"><Label className="text-[10px]">Funding / day</Label><Input type="number" min="0" step="0.1" value={fundingBps} onChange={(event) => setFundingBps(Number(event.target.value))} /></div>
                    <div className="space-y-1"><Label className="text-[10px]">Swap / day</Label><Input type="number" min="0" step="0.1" value={swapBps} onChange={(event) => setSwapBps(Number(event.target.value))} /></div>
                  </div>
                  <div className="flex items-center justify-between rounded-lg border border-border/50 bg-black/15 p-3"><div><div className="text-xs font-medium">Portfolio simulation</div><div className="text-[10px] text-muted-foreground">100k · 1% risk · 10 positions · separate A/B ledgers</div></div><Switch checked={portfolioEnabled} onCheckedChange={setPortfolioEnabled} /></div>
                  <div className="grid gap-3 sm:grid-cols-2"><div className="space-y-1"><Label className="text-[10px]">Comparison family</Label><Input value={comparisonFamily} onChange={(event) => setComparisonFamily(event.target.value)} placeholder="Counts every variant" /></div><div className="space-y-1"><Label className="text-[10px]">Variant ID</Label><Input value={variantId} onChange={(event) => setVariantId(event.target.value)} placeholder="e.g. baseline" /></div></div>
                </CardContent>
              </Card>

              <Card className="panel-glass">
                <CardHeader><SectionTitle icon={ShieldCheck} title="Contract preflight" note="Resolves provider identity, timeframe policy, universe, and estimated work before queueing." /></CardHeader>
                <CardContent className="space-y-4">
                  {!preflight ? (
                    <div className="rounded-xl border border-dashed border-border p-6 text-center"><Database className="mx-auto h-7 w-7 text-muted-foreground" /><p className="mt-2 text-xs text-muted-foreground">No preflight snapshot yet.</p></div>
                  ) : (
                    <>
                      <div className="flex items-center justify-between"><AssuranceBadge value={preflight.certification} /><span className="font-mono text-xs text-muted-foreground">{preflight.instruments.length} covered · {preflight.exclusions.length} excluded</span></div>
                      <div className="grid grid-cols-3 gap-2"><MetricTile label="Tasks" value={String(preflight.estimatedTasks)} /><MetricTile label="Series" value={String(preflight.estimatedSeries)} /><MetricTile label="Est. bars" value={preflight.estimatedBars.toLocaleString()} /></div>
                      <p className="text-[10px] text-muted-foreground">Historical bar coverage is certified during PREPARING DATA; any provider exclusions are retained in Results.</p>
                      {preflight.exclusions.length > 0 && <div className="max-h-28 space-y-1 overflow-auto rounded-lg border border-amber-400/20 bg-amber-400/5 p-2">{preflight.exclusions.slice(0, 8).map((row, index) => <div key={`${row.symbol}-${index}`} className="text-[10px] text-amber-200"><AlertTriangle className="mr-1 inline h-3 w-3" />{row.symbol}{row.engine ? ` · ${row.engine}` : ''}: {row.code}</div>)}</div>}
                    </>
                  )}
                  <div className="grid grid-cols-2 gap-2"><Button variant="outline" onClick={runPreflight} disabled={preflightMutation.loading || !explicitUniverseReady}>{preflightMutation.loading ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <ShieldCheck className="mr-2 h-4 w-4" />}Preflight</Button><Button onClick={submitRun} disabled={!preflight?.instruments.length || runMutation.loading}>{runMutation.loading ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}Queue run</Button></div>
                </CardContent>
              </Card>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="jobs" className="space-y-4">
          <Card className="panel-glass">
            <CardHeader><div className="flex items-center justify-between"><SectionTitle icon={Clock3} title="Durable job queue" note="One active job; incomplete symbol tasks recover after restart." /><Button variant="outline" size="sm" onClick={jobsPoll.refresh}><RefreshCw className={cn('mr-2 h-3.5 w-3.5', jobsPoll.isRefreshing && 'animate-spin')} />Refresh</Button></div></CardHeader>
            <CardContent className="space-y-3">
              {!jobs.length && <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">No v2 jobs yet.</div>}
              {jobs.map((run) => {
                const progress = run.progress_total ? (run.progress_completed / run.progress_total) * 100 : 0;
                const terminal = ['COMPLETED', 'FAILED', 'CANCELLED'].includes(run.status);
                const elapsed = jobElapsedSeconds(run);
                const eta = terminal ? null : jobEtaSeconds(run, elapsed);
                return (
                  <button key={run.run_id} type="button" onClick={() => selectRun(run.run_id, terminal ? 'results' : 'jobs')} className={cn('w-full rounded-xl border bg-black/15 p-4 text-left transition hover:border-primary/35', selectedRunId === run.run_id ? 'border-primary/50' : 'border-border/60')}>
                    <div className="flex flex-col justify-between gap-3 lg:flex-row lg:items-center">
                      <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><Badge variant="outline" className={statusTone(run.status)}>{run.status}</Badge><AssuranceBadge value={run.certification} />{run.recovered_count > 0 && <Badge variant="outline" className="border-sky-400/30 text-sky-300"><TimerReset className="mr-1 h-3 w-3" />RECOVERED ×{run.recovered_count}</Badge>}</div><div className="mt-2 truncate font-mono text-xs text-foreground">{run.run_id}</div><div className="mt-1 text-[10px] text-muted-foreground">{run.request.engines.map((engine) => `Engine ${engine} ${run.request.styles[engine]}`).join(' · ')} · {run.request.symbols.length} symbols · {run.request.start_date} → {run.request.end_date}</div></div>
                      <div className="flex items-center gap-2" onClick={(event) => event.stopPropagation()}>{!terminal && <Button variant="destructive" size="sm" onClick={() => cancelRun(run.run_id)} disabled={cancelMutation.loading}><CircleStop className="mr-1 h-3.5 w-3.5" />Cancel</Button>}{run.status === 'FAILED' && <Button variant="outline" size="sm" onClick={() => retryRun(run)}><RefreshCw className="mr-1 h-3.5 w-3.5" />Retry</Button>}{terminal && <Button variant="outline" size="sm" onClick={() => selectRun(run.run_id, 'results')}>Open result</Button>}</div>
                    </div>
                    <div className="mt-3"><div className="mb-1 flex justify-between text-[10px] text-muted-foreground"><span>{run.phase} · elapsed {formatDuration(elapsed)}{eta != null ? ` · ETA ${formatDuration(eta)}` : ''}</span><span>{run.progress_completed}/{run.progress_total}</span></div><Progress value={progress} className="h-1.5" /></div>
                  </button>
                );
              })}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="results" className="space-y-4">
          {!selectedRun ? (
            <Card className="panel-glass"><CardContent className="p-12 text-center text-sm text-muted-foreground">Select a job to inspect its result.</CardContent></Card>
          ) : (
            <>
              <Card className="panel-glass">
                <CardContent className="p-5">
                  <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-center"><div><div className="flex flex-wrap items-center gap-2"><Badge variant="outline" className={statusTone(selectedRun.status)}>{selectedRun.status}</Badge><AssuranceBadge value={result?.certification || selectedRun.certification} />{result?.reused && <Badge variant="outline" className="border-sky-400/30 text-sky-300">REUSED {compactHash(result.reusedFromRunId)}</Badge>}</div><div className="mt-2 font-mono text-sm">{selectedRun.run_id}</div><div className="mt-1 text-[10px] text-muted-foreground">Dataset {compactHash(result?.datasetId || selectedRun.dataset_id)} · Config {compactHash(result?.configSnapshotId || selectedRun.config_snapshot_id)}</div></div><div className="flex gap-2"><Button variant="outline" size="sm" asChild><a href={`/api/v2/backtests/${encodeURIComponent(selectedRun.run_id)}/export?format=csv`}><Download className="mr-2 h-3.5 w-3.5" />CSV</a></Button><Button variant="outline" size="sm" asChild><a href={`/api/v2/backtests/${encodeURIComponent(selectedRun.run_id)}/export?format=json`}><Archive className="mr-2 h-3.5 w-3.5" />Research bundle</a></Button></div></div>
                </CardContent>
              </Card>

              {!result ? (
                <Card className="panel-glass"><CardContent className="p-10 text-center"><Activity className="mx-auto h-7 w-7 animate-pulse text-primary" /><p className="mt-3 text-sm text-muted-foreground">{selectedRun.status === 'FAILED' ? selectedRun.error?.error || 'Run failed' : `Run is ${selectedRun.phase.toLowerCase().replaceAll('_', ' ')}.`}</p></CardContent></Card>
              ) : (
                <>
                  {resultIssues.length > 0 && (
                    <Card className={cn('panel-glass', result.failures.length ? 'border-rose-400/30' : 'border-amber-400/30')}>
                      <CardHeader><SectionTitle icon={AlertTriangle} title="Coverage & task outcomes" note="Excluded or failed work is never presented as a strategy result." /></CardHeader>
                      <CardContent className="space-y-2">
                        {resultIssues.slice(0, 20).map((issue, index) => {
                          const code = issueValue(issue.row, 'code') || 'UNSPECIFIED';
                          const message = issueValue(issue.row, 'message') || issueValue(issue.row, 'error') || 'No additional detail was recorded.';
                          return (
                            <div key={`${issue.kind}-${code}-${index}`} className="rounded-lg border border-border/60 bg-black/20 p-3">
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge variant="outline" className={issue.kind === 'FAILED' ? 'border-rose-400/30 text-rose-300' : 'border-amber-400/30 text-amber-300'}>{issue.kind}</Badge>
                                <span className="font-mono text-[10px] text-foreground">{code}</span>
                                <span className="text-[10px] text-muted-foreground">{issueScope(issue.row)}</span>
                              </div>
                              <p className="mt-1 text-xs text-muted-foreground">{message}</p>
                            </div>
                          );
                        })}
                        {resultIssues.length > 20 && <div className="text-[10px] text-muted-foreground">Showing 20 of {resultIssues.length} outcomes. Export the research bundle for the complete list.</div>}
                      </CardContent>
                    </Card>
                  )}

                  {!hasEvaluatedTasks ? (
                    <Card className="panel-glass border-rose-400/30">
                      <CardContent className="p-8 text-center">
                        <XCircle className="mx-auto h-8 w-8 text-rose-300" />
                        <h2 className="mt-3 text-base font-semibold text-foreground">No strategy result was produced</h2>
                        <p className="mx-auto mt-2 max-w-2xl text-sm text-muted-foreground">No selected Engine A/B task completed point-in-time replay. There are no signals or trades to chart; the coverage and task outcomes above explain why.</p>
                        <div className="mx-auto mt-5 grid max-w-xl grid-cols-3 gap-2"><MetricTile label="Evaluated tasks" value="0" /><MetricTile label="Outcomes" value={String(resultIssues.length)} /><MetricTile label="Data phase" value={formatDuration(result.timing.dataAcquisitionSec)} /></div>
                        <Button variant="outline" className="mt-5" onClick={() => setTab('data')}><Database className="mr-2 h-4 w-4" />Inspect data catalog</Button>
                      </CardContent>
                    </Card>
                  ) : (
                    <>
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                    {Object.entries(result.engines || {}).map(([engine, row]) => <Card key={engine} className="panel-glass"><CardHeader className="pb-2"><CardTitle className="text-sm">Engine {engine}</CardTitle></CardHeader><CardContent className="grid grid-cols-2 gap-2"><MetricTile label="Mean R" value={metricValue(row.metrics.meanR, 3)} tone={row.metrics.meanR >= 0 ? 'text-emerald-300' : 'text-rose-300'} /><MetricTile label="Total R" value={metricValue(row.metrics.totalR)} /><MetricTile label="Win rate" value={`${metricValue(row.metrics.winRate, 1)}%`} /><MetricTile label="Trades" value={String(row.metrics.tradeCount)} /></CardContent></Card>)}
                    <Card className="panel-glass"><CardHeader className="pb-2"><CardTitle className="text-sm">Run timing</CardTitle></CardHeader><CardContent className="grid grid-cols-2 gap-2"><MetricTile label="Data" value={formatDuration(result.timing.dataAcquisitionSec)} /><MetricTile label="Compute" value={formatDuration(result.timing.computeSec)} /><MetricTile label="JIT" value={formatDuration(result.timing.jitCompilationSec)} /><MetricTile label="Peak worker" value={`${(result.peakWorkerMemoryBytes / 1024 ** 2).toFixed(0)} MB`} /></CardContent></Card>
                  </div>

                  {Object.entries(result.engines || {}).map(([engine, row]) => (
                    <div key={`curves-${engine}`} className="grid gap-4 xl:grid-cols-2">
                      <Card className="panel-glass"><CardHeader><SectionTitle icon={BarChart3} title={`Engine ${engine} normalized R`} note="Full independent engine ledger; never blended with the other engine." /></CardHeader><CardContent><EquityAreaChart data={row.equityCurve || []} height={260} valueLabel="Cumulative R" idSuffix={`btv2-r-${engine}`} /></CardContent></Card>
                      <Card className="panel-glass"><CardHeader><SectionTitle icon={Activity} title={`Engine ${engine} underwater`} note="Drawdown from this engine ledger's running R peak." /></CardHeader><CardContent><EquityAreaChart data={row.underwaterCurve || []} dataKey="drawdown" height={260} valueLabel="Drawdown R" baseline={0} idSuffix={`btv2-dd-${engine}`} /></CardContent></Card>
                    </div>
                  ))}

                  <Card className="panel-glass">
                    <CardHeader><SectionTitle icon={Sigma} title="Costs & segment analysis" note="Every cost assumption is frozen; pair, direction, and regime metrics remain engine-specific." /></CardHeader>
                    <CardContent className="space-y-4">
                      <div className="flex flex-wrap gap-1.5">
                        {Object.entries(result.costModel || {}).map(([key, value]) => <Badge key={key} variant="outline" className="border-border/60 bg-black/20 text-[9px] text-muted-foreground">{key.replaceAll('_', ' ')} · {String(value)}</Badge>)}
                      </div>
                      {Object.entries(result.engines || {}).map(([engine, row]) => (
                        <div key={`analysis-${engine}`} className="rounded-xl border border-border/60 bg-black/15 p-3">
                          <div className="text-xs font-semibold">Engine {engine}</div>
                          {(['direction', 'regime', 'symbol'] as const).map((group) => (
                            <div key={group} className="mt-2 flex flex-wrap items-center gap-1.5"><span className="w-16 text-[9px] uppercase tracking-wider text-muted-foreground">{group}</span>{Object.entries(row.analysis?.[group] || {}).slice(0, 12).map(([label, metrics]) => <Badge key={label} variant="outline" className="text-[9px]">{label} · n={metrics.tradeCount} · {metrics.meanR.toFixed(2)}R</Badge>)}</div>
                          ))}
                        </div>
                      ))}
                    </CardContent>
                  </Card>

                  <Card className="panel-glass"><CardHeader><SectionTitle icon={Layers3} title="IS / OOS / holdout assurance" note="Confidence intervals, PSR/DSR, costs, ambiguity, and funnel diagnostics are captured in the main pass." /></CardHeader><CardContent className="space-y-3">{result.tasks.map((task) => {
                    const ci = task.validation.confidenceIntervals?.meanR;
                    const holdout = task.validation.metrics?.HOLDOUT;
                    const oos = task.validation.metrics?.OOS;
                    return <div key={`${task.task.engine}-${task.task.symbol}`} className="rounded-xl border border-border/60 bg-black/15 p-4"><div className="flex flex-wrap items-center justify-between gap-2"><div><span className="font-semibold">Engine {task.task.engine} · {task.task.symbol}</span><span className="ml-2 text-[10px] uppercase text-muted-foreground">{task.task.style} · {task.funnel.executionTimeframe}</span></div><div className="flex gap-2"><Badge variant="outline">PSR {task.validation.psr == null ? '—' : `${(task.validation.psr * 100).toFixed(1)}%`}</Badge><Badge variant="outline">DSR {task.validation.dsr == null ? '—' : `${(task.validation.dsr * 100).toFixed(1)}%`}</Badge></div></div><div className="mt-3 grid gap-2 sm:grid-cols-3 lg:grid-cols-6"><MetricTile label="All mean R" value={metricValue(task.metrics.meanR, 3)} /><MetricTile label="OOS mean R" value={metricValue(oos?.meanR, 3)} /><MetricTile label="Holdout R" value={metricValue(holdout?.meanR, 3)} /><MetricTile label="95% CI" value={ci ? `${ci.lower.toFixed(2)}…${ci.upper.toFixed(2)}` : '—'} /><MetricTile label="Costs R" value={metricValue(task.metrics.totalCostR)} /><MetricTile label="Ambiguity" value={`${(task.simulation.sameBarAmbiguityRate * 100).toFixed(2)}%`} /></div><div className="mt-3 flex flex-wrap gap-1.5">{Object.entries(task.funnel.rejectionFunnel || {}).slice(0, 8).map(([reason, count]) => <Badge key={reason} variant="outline" className="border-border/60 bg-black/20 text-[9px] text-muted-foreground">{reason} · {count}</Badge>)}</div></div>;
                  })}</CardContent></Card>

                  <Card className="panel-glass"><CardHeader><div className="flex items-center justify-between"><SectionTitle icon={Database} title="Trade ledger" note={`${tradePage?.total || 0} trades · adverse stop-first ambiguity policy`} /><div className="flex gap-1"><Button variant="outline" size="icon" disabled={tradeOffset === 0} onClick={() => setTradeOffset(Math.max(0, tradeOffset - 50))}><ChevronLeft className="h-4 w-4" /></Button><Button variant="outline" size="icon" disabled={!tradePage || tradeOffset + 50 >= tradePage.total} onClick={() => setTradeOffset(tradeOffset + 50)}><ChevronRight className="h-4 w-4" /></Button></div></div></CardHeader><CardContent><div className="overflow-x-auto rounded-lg border border-border/50"><Table><TableHeader><TableRow><TableHead>Engine / Symbol</TableHead><TableHead>Direction</TableHead><TableHead>Entry</TableHead><TableHead>Exit</TableHead><TableHead>Gross R</TableHead><TableHead>Cost R</TableHead><TableHead>Net R</TableHead><TableHead>Split</TableHead><TableHead>Exit</TableHead></TableRow></TableHeader><TableBody>{(tradePage?.items || []).map((trade) => <TableRow key={trade.tradeId}><TableCell><div className="font-medium">{trade.engine} · {trade.symbol}</div><div className="text-[9px] text-muted-foreground">{trade.style}</div></TableCell><TableCell><Badge variant="outline" className={trade.direction === 'LONG' ? 'border-emerald-400/30 text-emerald-300' : 'border-rose-400/30 text-rose-300'}>{trade.direction}</Badge></TableCell><TableCell className="font-mono text-xs">{fmtNum(trade.entry_price, 5)}<div className="text-[9px] text-muted-foreground">{new Date(trade.entry_time).toLocaleString()}</div></TableCell><TableCell className="font-mono text-xs">{fmtNum(trade.exit_price, 5)}<div className="text-[9px] text-muted-foreground">{new Date(trade.exit_time).toLocaleString()}</div></TableCell><TableCell className="font-mono">{trade.gross_r.toFixed(2)}</TableCell><TableCell className="font-mono text-amber-300">-{trade.cost_r.toFixed(2)}</TableCell><TableCell className={cn('font-mono font-semibold', trade.net_r >= 0 ? 'text-emerald-300' : 'text-rose-300')}>{trade.net_r.toFixed(2)}</TableCell><TableCell className="text-[10px]">{trade.split}</TableCell><TableCell className="text-[10px]">{trade.exit_reason}{trade.ambiguous_same_bar && <AlertTriangle className="ml-1 inline h-3 w-3 text-amber-300" />}</TableCell></TableRow>)}</TableBody></Table></div></CardContent></Card>
                    </>
                  )}
                </>
              )}
            </>
          )}
        </TabsContent>

        <TabsContent value="compare" className="space-y-4">
          <div className="grid gap-4 xl:grid-cols-[0.8fr_1.2fr]">
            <Card className="panel-glass"><CardHeader><SectionTitle icon={GitCompareArrows} title="Compatible run set" note="Dataset, date, cost, and assurance contracts must match." /></CardHeader><CardContent className="space-y-2">{jobs.filter((run) => run.status === 'COMPLETED' && Boolean(run.result?.tasks?.length)).map((run) => <button key={run.run_id} type="button" onClick={() => setCompareRunIds((current) => { const next = new Set(current); if (next.has(run.run_id)) next.delete(run.run_id); else next.add(run.run_id); return next; })} className={cn('flex w-full items-center gap-3 rounded-lg border p-3 text-left', compareRunIds.has(run.run_id) ? 'border-primary/45 bg-primary/10' : 'border-border/60 bg-black/15')}><Checkbox checked={compareRunIds.has(run.run_id)} /><div className="min-w-0 flex-1"><div className="truncate font-mono text-xs">{run.run_id}</div><div className="text-[9px] text-muted-foreground">{run.request.variant_id || 'unlabelled'} · {compactHash(run.dataset_id)}</div></div><AssuranceBadge value={run.certification} /></button>)}<Button className="mt-3 w-full" onClick={createComparison} disabled={compareRunIds.size < 2 || compareMutation.loading}><GitCompareArrows className="mr-2 h-4 w-4" />Compare {compareRunIds.size} runs</Button></CardContent></Card>
            <Card className="panel-glass"><CardHeader><SectionTitle icon={Sigma} title="Selection-bias report" note="Metric deltas include all declared family trials; no auto-selection or promotion." /></CardHeader><CardContent>{!activeComparison ? <div className="rounded-xl border border-dashed border-border p-12 text-center text-sm text-muted-foreground">Create a comparison to calculate compatibility, DSR, and PBO.</div> : !activeComparison.compatible ? <div className="rounded-xl border border-rose-400/25 bg-rose-400/5 p-5"><div className="flex items-center gap-2 font-medium text-rose-300"><XCircle className="h-4 w-4" />Incompatible contracts</div><div className="mt-3 flex flex-wrap gap-2">{activeComparison.incompatibilityReasons.map((reason) => <Badge key={reason} variant="outline" className="border-rose-400/30 text-rose-200">{reason.replaceAll('_', ' ')}</Badge>)}</div></div> : <div className="space-y-4"><div className="grid grid-cols-3 gap-2"><MetricTile label="Trials" value={String(activeComparison.trialCount || 0)} /><MetricTile label="PBO" value={activeComparison.pbo ? `${(activeComparison.pbo.value * 100).toFixed(1)}%` : '—'} /><MetricTile label="PBO folds" value={String(activeComparison.pbo?.folds || 0)} /></div><div className="space-y-2">{(activeComparison.runs || []).map((row, index) => <div key={row.runId} className="rounded-xl border border-border/60 bg-black/15 p-4"><div className="flex items-center justify-between gap-2"><div><div className="font-mono text-xs">{row.runId}</div><div className="text-[9px] text-muted-foreground">{index === 0 ? 'Baseline' : row.variantId || `Variant ${index}`}</div></div><Badge variant="outline">DSR {row.dsr == null ? '—' : `${(row.dsr * 100).toFixed(1)}%`}</Badge></div><div className="mt-3 grid grid-cols-3 gap-2"><MetricTile label="Mean R" value={metricValue(row.metrics.meanR, 3)} note={`Δ ${metricValue(row.deltasFromBaseline.meanR, 3)}`} /><MetricTile label="Total R" value={metricValue(row.metrics.totalR)} note={`Δ ${metricValue(row.deltasFromBaseline.totalR)}`} /><MetricTile label="95% mean R" value={`${row.confidenceIntervals.meanR.lower.toFixed(2)}…${row.confidenceIntervals.meanR.upper.toFixed(2)}`} /></div></div>)}</div></div>}</CardContent></Card>
          </div>
        </TabsContent>

        <TabsContent value="data" className="space-y-4">
          <Card className="panel-glass"><CardHeader><div className="flex items-center justify-between"><SectionTitle icon={Database} title="Immutable data catalog" note="Content-addressed Parquet snapshots; simulation performs no live fetches." /><Button variant="outline" size="sm" onClick={datasetsPoll.refresh}><RefreshCw className={cn('mr-2 h-3.5 w-3.5', datasetsPoll.isRefreshing && 'animate-spin')} />Refresh</Button></div></CardHeader><CardContent className="space-y-4">{!datasets.length && <div className="rounded-xl border border-dashed border-border p-12 text-center text-sm text-muted-foreground">Datasets are prepared when a run enters PREPARING DATA.</div>}{datasets.map((dataset) => <div key={dataset.datasetId} className="rounded-xl border border-border/60 bg-black/15 p-4"><div className="flex flex-col justify-between gap-3 lg:flex-row lg:items-center"><div><div className="flex items-center gap-2"><AssuranceBadge value={dataset.certification} /><Badge variant="outline" className={dataset.status === 'READY' ? 'border-emerald-400/30 text-emerald-300' : 'border-rose-400/30 text-rose-300'}>{dataset.status}</Badge></div><div className="mt-2 font-mono text-xs">{dataset.datasetId}</div><div className="mt-1 text-[9px] text-muted-foreground">Manifest {compactHash(dataset.manifestHash)} · {dataset.startDate} → {dataset.endDate}</div></div><div className="grid grid-cols-3 gap-2"><MetricTile label="Symbols" value={String(dataset.symbols.length)} /><MetricTile label="Series" value={String(dataset.series.length)} /><MetricTile label="Excluded" value={String(dataset.exclusions.length)} /></div></div><div className="mt-4 overflow-x-auto rounded-lg border border-border/50"><Table><TableHeader><TableRow><TableHead>Provider / Symbol</TableHead><TableHead>TF</TableHead><TableHead>Coverage</TableHead><TableHead>Rows</TableHead><TableHead>Gaps</TableHead><TableHead>Hash</TableHead><TableHead>State</TableHead></TableRow></TableHeader><TableBody>{dataset.series.slice(0, 50).map((series) => <TableRow key={series.series_id}><TableCell><div className="font-medium">{series.provider} · {series.symbol}</div><div className="text-[9px] text-muted-foreground">{series.provider_symbol}</div></TableCell><TableCell className="font-mono">{series.timeframe}</TableCell><TableCell className="text-[10px]">{series.first_timestamp?.slice(0, 10) || '—'} → {series.last_timestamp?.slice(0, 10) || '—'}</TableCell><TableCell className="font-mono">{series.row_count.toLocaleString()}</TableCell><TableCell className={series.gap_count ? 'text-amber-300' : 'text-emerald-300'}>{series.gap_count}</TableCell><TableCell className="font-mono text-[10px]">{compactHash(series.series_id, 10)}</TableCell><TableCell>{series.quality_state === 'PASS' ? <CheckCircle2 className="h-4 w-4 text-emerald-300" /> : <AlertTriangle className="h-4 w-4 text-amber-300" />}</TableCell></TableRow>)}</TableBody></Table></div></div>)}</CardContent></Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default memo(BacktestPanel);
