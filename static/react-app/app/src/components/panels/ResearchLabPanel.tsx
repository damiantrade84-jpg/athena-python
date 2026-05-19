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
import { Microscope, Play, Rocket, History, ListChecks, ChevronDown, ChevronUp, AlertTriangle } from 'lucide-react';
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
  status?: 'queued' | 'running' | 'reporting' | 'complete' | 'failed' | 'partial' | string;
  message?: string;
  results_count?: number;
  report_errors?: string[];
  traceback_tail?: string;
  error?: string;
  traceback?: string;
  mode?: string;
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

interface TrustLegendItem {
  tier: string;
  label: string;
  meaning: string;
}

interface TrustContext {
  legend?: TrustLegendItem[];
  tier_counts?: Record<string, number>;
  fidelity_counts?: Record<string, number>;
  validation_run_count?: number;
  disclaimer?: string;
}

interface RankedRow {
  symbol?: string;
  timeframe?: string;
  family?: string;
  strategy?: string;
  strategy_name?: string;
  direction?: string;
  status?: string;
  trades?: number;
  trade_count?: number;
  win_rate?: number;
  profit_factor?: number;
  expectancy?: number;
  sqn?: number;
  sharpe?: number;
  max_drawdown?: number;
  recommendation?: string;
  engine?: string;
  engine_component?: string;
  implementation_verdict?: string;
  implementation_blockers?: string;
  engine_fidelity?: string;
  fidelity_note?: string;
  trust_tier?: string;
  trust_summary?: string;
  oos_return?: number;
  robustness_score?: number;
  [k: string]: unknown;
}

interface RankedResponse {
  run_id?: string;
  ranked?: RankedRow[];
  recommendations?: Array<Record<string, unknown>>;
  automated_next_tests?: Array<Record<string, unknown>>;
  operator_summary?: OperatorSummary;
  trust_context?: TrustContext;
  validation_run_count?: number;
  note?: string;
  error?: string;
}

interface OperatorSummary {
  headline?: string;
  decision?: string;
  source?: string;
  source_of_truth?: string;
  use_now?: Array<Record<string, unknown>>;
  research_candidates?: Array<Record<string, unknown>>;
  blocked_candidates?: Array<Record<string, unknown>>;
  candidate_groups?: Array<Record<string, unknown>>;
  keep?: Array<Record<string, unknown>>;
  remove_or_demote?: Array<Record<string, unknown>>;
  retest?: Array<Record<string, unknown>>;
  use_now_trusted?: Array<Record<string, unknown>>;
  screen_only?: Array<Record<string, unknown>>;
  reject_trusted?: Array<Record<string, unknown>>;
  retest_trusted?: Array<Record<string, unknown>>;
  trust_context?: TrustContext;
  warnings?: string[];
  next_step?: string;
  implementation_ready_count?: number;
  ready_use_add_count?: number;
  total_candidate_count?: number;
}

interface SessionState {
  session_id?: string;
  status?: string;
  current_phase?: string;
  progress_pct?: number;
  discovery_run_id?: string;
  validation_run_ids?: string[];
  final_verdict?: string;
  final_summary?: string;
  engine_comparison?: Record<string, unknown>;
  trust_context?: TrustContext;
  scoped_rows?: RankedRow[];
  error?: string;
}

interface StartSessionResponse {
  session_id?: string;
  status?: string;
  error?: string;
}

interface StartRunResponse {
  run_id?: string;
  mode?: string;
  status?: string;
  error?: string;
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

const SESSION_TERMINAL = new Set(['COMPLETE', 'FAILED', 'CANCELLED']);

function statusBadge(status?: string): string {
  switch ((status || '').toLowerCase()) {
    case 'complete': return 'badge-long';
    case 'partial': return 'badge-neutral';
    case 'reporting': return 'badge-neutral';
    case 'running':
    case 'queued': return 'badge-neutral';
    case 'failed': return 'badge-short';
    default: return 'badge-neutral';
  }
}

function trustTierBadge(tier?: string): string {
  switch ((tier || '').toUpperCase()) {
    case 'USE_NOW': return 'badge-long';
    case 'SCREEN_ONLY': return 'badge-neutral';
    case 'RETEST': return 'badge-neutral';
    case 'REJECT': return 'badge-short';
    default: return 'badge-neutral';
  }
}

function fidelityBadge(fidelity?: string): string {
  switch ((fidelity || '').toUpperCase()) {
    case 'LIVE_ALIGNED_A':
    case 'LIVE_ALIGNED_B': return 'badge-long';
    case 'PROXY_ENGINE_B':
    case 'PROXY_ENGINE_D': return 'badge-short';
    default: return 'badge-neutral';
  }
}

function recommendationBadge(recommendation?: string): string {
  switch ((recommendation || '').toUpperCase()) {
    case 'ADD': return 'badge-long';
    case 'KEEP': return 'badge-neutral';
    case 'REMOVE_OR_DEMOTE':
    case 'REJECT': return 'badge-short';
    case 'RETEST':
    case 'WATCHLIST_ONLY': return 'badge-neutral';
    default: return 'badge-neutral';
  }
}

function sessionVerdictBadge(verdict?: string): string {
  switch ((verdict || '').toUpperCase()) {
    case 'IMPLEMENTATION_CANDIDATE': return 'badge-long';
    case 'VALIDATION_CANDIDATE': return 'badge-neutral';
    case 'WEAK_EDGE': return 'badge-neutral';
    case 'REJECTED': return 'badge-short';
    default: return 'badge-neutral';
  }
}

function textValue(row: Record<string, unknown>, key: string, fallback = '—'): string {
  const value = row[key];
  if (value === null || value === undefined || value === '') return fallback;
  return String(value);
}

function numberValue(row: Record<string, unknown>, key: string): number | null {
  const value = row[key];
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function TrustBanner({ trustContext }: { trustContext?: TrustContext }) {
  const [open, setOpen] = useState(false);
  if (!trustContext) return null;

  const counts = trustContext.tier_counts || {};
  const tiers = ['USE_NOW', 'SCREEN_ONLY', 'RETEST', 'REJECT'] as const;

  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-4 space-y-3">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2 flex-wrap">
            {tiers.map((tier) => (
              <Badge key={tier} className={`${trustTierBadge(tier)} text-[10px]`}>
                {tier.replace('_', ' ')} ({counts[tier] ?? 0})
              </Badge>
            ))}
            {(trustContext.validation_run_count ?? 0) > 0 && (
              <Badge className="badge-neutral text-[10px]">
                Validation runs: {trustContext.validation_run_count}
              </Badge>
            )}
          </div>
          <Button variant="ghost" size="sm" className="h-7 text-[10px] gap-1" onClick={() => setOpen(!open)}>
            How to read these results
            {open ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </Button>
        </div>
        {open && (
          <div className="space-y-2 text-[11px] text-muted-foreground border-t border-border/40 pt-3">
            {trustContext.disclaimer && <p>{trustContext.disclaimer}</p>}
            {(trustContext.legend || []).map((item) => (
              <div key={item.tier}>
                <span className="font-semibold text-foreground">{item.label}</span>
                {' — '}
                {item.meaning}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ActionCard({
  title,
  rows,
  accentClass,
  emptyText,
}: {
  title: string;
  rows: Array<Record<string, unknown>>;
  accentClass: string;
  emptyText: string;
}) {
  return (
    <Card className={`border-border/60 bg-card/50 ${accentClass}`}>
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-semibold uppercase tracking-wider">{title} ({rows.length})</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">{emptyText}</p>
        ) : (
          <ScrollArea className="h-[180px]">
            <div className="space-y-1">
              {rows.slice(0, 30).map((row, i) => (
                <div
                  key={i}
                  className="flex items-start justify-between gap-2 border-b border-border/20 py-1 text-[11px]"
                  title={textValue(row, 'trust_summary') || textValue(row, 'fidelity_note')}
                >
                  <span>
                    {textValue(row, 'strategy_name')} · {textValue(row, 'symbol')} · {textValue(row, 'timeframe')}
                  </span>
                  <Badge className={`${fidelityBadge(textValue(row, 'engine_fidelity'))} text-[9px] shrink-0`}>
                    {textValue(row, 'engine_fidelity', '—').replace(/_/g, ' ')}
                  </Badge>
                </div>
              ))}
            </div>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  );
}

export default function ResearchLabPanel() {
  const { showToast } = useStore();

  const [activeTab, setActiveTab] = useState<'autopilot' | 'manual' | 'runs'>('autopilot');

  const [marketGroup, setMarketGroup] = useState('crypto');
  const [tradingStyle, setTradingStyle] = useState('intra');
  const [researchDepth, setResearchDepth] = useState('standard');

  const [mode, setMode] = useState('tiny');
  const [direction, setDirection] = useState('both');
  const [symbols, setSymbols] = useState('');
  const [timeframes, setTimeframes] = useState('');
  const [families, setFamilies] = useState('');
  const [strategies, setStrategies] = useState('');

  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<RunStatusResponse | null>(null);
  const [ranked, setRanked] = useState<RankedResponse | null>(null);

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionState, setSessionState] = useState<SessionState | null>(null);
  const [sessionStarting, setSessionStarting] = useState(false);

  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sessionPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const { data: runsData, loading: runsLoading, error: runsError, refresh: refreshRuns } =
    useApiPoll<RunsResponse>('/api/research-lab/runs', 0);

  const { post: postManualRun, loading: manualStarting } = useApiPost<StartRunResponse>();

  const trustContext = ranked?.trust_context || ranked?.operator_summary?.trust_context;

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const stopSessionPolling = useCallback(() => {
    if (sessionPollRef.current) {
      clearInterval(sessionPollRef.current);
      sessionPollRef.current = null;
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

  const pollRun = useCallback((runId: string) => {
    stopPolling();
    pollTimerRef.current = setInterval(async () => {
      try {
        const res = await fetch(`/api/research-lab/run/${runId}`);
        const json = (await res.json()) as RunStatusResponse;
        setRunStatus(json);
        if (json.status === 'reporting') {
          setRunStatus(json);
          await fetchRanked(runId);
        } else if (json.status === 'complete' || json.status === 'partial') {
          stopPolling();
          await fetchRanked(runId);
          refreshRuns();
          if (json.status === 'partial') {
            showToast(
              json.error || `Run ${runId} finished with partial reports — try Refresh ranked`,
              'error',
            );
          } else {
            showToast(`Run ${runId} complete — ${json.results_count ?? json.summary?.total ?? '?'} results`, 'success');
          }
        } else if (json.status === 'failed') {
          stopPolling();
          refreshRuns();
          showToast(`Run ${runId} failed: ${json.error || 'unknown'}`, 'error');
        }
      } catch (e) {
        console.warn('[research-lab] poll error', e);
      }
    }, 3000);
  }, [stopPolling, fetchRanked, refreshRuns, showToast]);

  const pollSession = useCallback((sid: string) => {
    stopSessionPolling();
    sessionPollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`/api/research-lab/session-autopilot/status/${sid}`);
        const json = (await res.json()) as SessionState;
        setSessionState(json);
        if (SESSION_TERMINAL.has(String(json.status || '').toUpperCase())) {
          stopSessionPolling();
          if (String(json.status).toUpperCase() === 'COMPLETE') {
            const rres = await fetch(`/api/research-lab/session-autopilot/result/${sid}`);
            const rdata = (await rres.json()) as SessionState;
            setSessionState(rdata);
            const discId = rdata.discovery_run_id;
            if (discId) {
              setCurrentRunId(discId);
              await fetchRanked(discId);
            }
            showToast(`Session complete — verdict: ${rdata.final_verdict || 'unknown'}`, 'success');
          } else if (String(json.status).toUpperCase() === 'FAILED') {
            showToast('Session autopilot failed', 'error');
          }
          refreshRuns();
        }
      } catch (e) {
        console.warn('[research-lab] session poll error', e);
      }
    }, 3000);
  }, [stopSessionPolling, fetchRanked, refreshRuns, showToast]);

  useEffect(() => () => {
    stopPolling();
    stopSessionPolling();
  }, [stopPolling, stopSessionPolling]);

  const startSessionAutopilot = useCallback(async () => {
    setRanked(null);
    setRunStatus(null);
    setSessionState(null);
    setSessionStarting(true);
    try {
      const res = await fetch('/api/research-lab/session-autopilot/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          market_group: marketGroup,
          trading_style: tradingStyle,
          research_depth: researchDepth,
        }),
      });
      const data = (await res.json()) as StartSessionResponse;
      if (data.error || !data.session_id) {
        showToast(`Session failed: ${data.error || 'unknown'}`, 'error');
        return;
      }
      setSessionId(data.session_id);
      setSessionState(data as SessionState);
      showToast(`Session autopilot started: ${data.session_id}`, 'info');
      pollSession(data.session_id);
    } catch (e) {
      showToast(`Session failed: ${(e as Error).message}`, 'error');
    } finally {
      setSessionStarting(false);
    }
  }, [marketGroup, tradingStyle, researchDepth, showToast, pollSession]);

  const stopSession = useCallback(async () => {
    if (!sessionId) return;
    try {
      await fetch(`/api/research-lab/session-autopilot/stop/${sessionId}`, { method: 'POST' });
      stopSessionPolling();
      showToast('Session stop requested', 'info');
    } catch (e) {
      showToast(`Stop failed: ${(e as Error).message}`, 'error');
    }
  }, [sessionId, stopSessionPolling, showToast]);

  const startManual = useCallback(async () => {
    setRanked(null);
    setRunStatus(null);
    const payload: Record<string, unknown> = {
      mode,
      direction,
      run_ai_review: false,
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
  }, [postManualRun, mode, direction, symbols, timeframes, families, strategies, showToast, pollRun]);

  const openRun = useCallback(async (runId: string) => {
    stopPolling();
    setCurrentRunId(runId);
    setRunStatus(null);
    setRanked(null);
    try {
      const res = await fetch(`/api/research-lab/run/${runId}`);
      const json = (await res.json()) as RunStatusResponse;
      setRunStatus(json);
      if (json.status === 'complete' || json.status === 'partial' || json.status === 'reporting') {
        await fetchRanked(runId);
      }
      if (json.status === 'running' || json.status === 'queued' || json.status === 'reporting') {
        pollRun(runId);
      }
    } catch (e) {
      showToast(`Failed to open run: ${(e as Error).message}`, 'error');
    }
  }, [stopPolling, fetchRanked, pollRun, showToast]);

  const sortedRuns = useMemo(() => (runsData?.runs || []).slice(), [runsData]);

  const op = ranked?.operator_summary;
  const useNowRows = op?.use_now_trusted || [];
  const screenRows = op?.screen_only || [];
  const retestRows = op?.retest_trusted || op?.retest || [];
  const rejectRows = op?.reject_trusted?.length
    ? op.reject_trusted
    : op?.remove_or_demote || [];

  const sessionVerdictWarning = sessionState?.final_verdict === 'IMPLEMENTATION_CANDIDATE'
    && useNowRows.length === 0
    && ranked !== null;

  return (
    <div className="space-y-5">
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-4">
          <div className="flex items-center gap-2 mb-2">
            <Microscope className="w-4 h-4 text-primary" />
            <h3 className="text-sm font-semibold">Research Lab</h3>
          </div>
          <p className="text-xs text-muted-foreground">
            Strategy discovery via <span className="font-mono">athena_research</span>. Discovery uses simplified
            proxies — trust tiers show what is live-aligned vs screen-only. Session autopilot runs discovery
            plus validation child runs before recommending actions.
          </p>
        </CardContent>
      </Card>

      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as typeof activeTab)}>
        <TabsList>
          <TabsTrigger value="autopilot" className="text-xs">Session Autopilot</TabsTrigger>
          <TabsTrigger value="manual" className="text-xs">Manual Run</TabsTrigger>
          <TabsTrigger value="runs" className="text-xs">Run History</TabsTrigger>
        </TabsList>

        <TabsContent value="autopilot" className="mt-3 space-y-3">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <Rocket className="w-4 h-4 text-primary" />
                One-Click Session Autopilot
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
                <Button size="sm" className="h-8 gap-1 text-xs" onClick={startSessionAutopilot} disabled={sessionStarting}>
                  <Play className="w-3 h-3" />
                  {sessionStarting ? 'Starting…' : 'Run Session Autopilot'}
                </Button>
                {sessionId && !SESSION_TERMINAL.has(String(sessionState?.status || '').toUpperCase()) && (
                  <Button size="sm" variant="outline" className="h-8 text-xs" onClick={stopSession}>
                    Stop
                  </Button>
                )}
              </div>
              <p className="text-[10px] text-muted-foreground">
                Runs discovery → validation child runs → AI review. Results use trust tiers — not live engine parity for proxies.
              </p>
            </CardContent>
          </Card>

          {sessionState && (
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider">
                  Session · <span className="font-mono">{sessionState.session_id}</span>
                  {sessionState.final_verdict && (
                    <Badge className={`${sessionVerdictBadge(sessionState.final_verdict)} text-[10px]`}>
                      {sessionState.final_verdict.replace(/_/g, ' ')}
                    </Badge>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-[11px]">
                <div className="flex flex-wrap gap-3 text-muted-foreground">
                  <span>Phase: <strong className="text-foreground">{sessionState.current_phase || sessionState.status}</strong></span>
                  {sessionState.progress_pct != null && <span>Progress: {sessionState.progress_pct}%</span>}
                  {sessionState.discovery_run_id && <span>Discovery: <span className="font-mono">{sessionState.discovery_run_id}</span></span>}
                  {(sessionState.validation_run_ids || []).length > 0 && (
                    <span>Validation runs: {(sessionState.validation_run_ids || []).length}</span>
                  )}
                </div>
                {sessionState.final_summary && (
                  <p className="text-muted-foreground">{sessionState.final_summary}</p>
                )}
                {sessionVerdictWarning && (
                  <div className="flex items-start gap-2 p-2 rounded border border-warning/40 bg-warning/10 text-warning">
                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                    <span>
                      Session verdict is IMPLEMENTATION_CANDIDATE but no USE_NOW trusted rows found.
                      Proxy or discovery-only strategies may have passed — check Screen Only card.
                    </span>
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="manual" className="mt-3">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
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
                  <Input value={symbols} onChange={(e) => setSymbols(e.target.value)} className="h-8 text-xs font-mono" placeholder="Symbols (comma-separated)" />
                  <Input value={timeframes} onChange={(e) => setTimeframes(e.target.value)} className="h-8 text-xs font-mono" placeholder="Timeframes (e.g. M15, H1)" />
                  <Input value={families} onChange={(e) => setFamilies(e.target.value)} className="h-8 text-xs font-mono" placeholder="Families" />
                  <Input value={strategies} onChange={(e) => setStrategies(e.target.value)} className="h-8 text-xs font-mono" placeholder="Strategies" />
                </div>
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
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
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

      {currentRunId && (
        <>
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center justify-between uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <span>Run · <span className="font-mono text-xs">{currentRunId}</span></span>
                <Badge className={`${statusBadge(runStatus?.status)} text-[10px]`}>{runStatus?.status || '—'}</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {runStatus?.error && (
                <div className={`p-2 rounded border text-[11px] ${
                  runStatus.status === 'partial'
                    ? 'bg-warning/10 border-warning/40 text-warning'
                    : 'bg-short/10 border-short/40 text-short'
                }`}>
                  {runStatus.error}
                  {runStatus.results_count != null && runStatus.results_count > 0 && (
                    <p className="mt-1 text-[10px] opacity-90">
                      Evaluations on disk: {runStatus.results_count}. If ranked data loads below, you can still review results.
                    </p>
                  )}
                </div>
              )}
              {runStatus?.message && (
                <p className="text-[11px] text-muted-foreground">{runStatus.message}</p>
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
                {(runStatus?.status === 'complete' || runStatus?.status === 'reporting' || runStatus?.status === 'partial') && (
                  <>
                    <Button size="sm" variant="outline" className="h-7 gap-1 text-[10px]" onClick={() => fetchRanked(currentRunId)}>
                      Refresh ranked
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

          {trustContext && <TrustBanner trustContext={trustContext} />}

          {op && (
            <>
              <Card className="border-border/60 bg-card/50">
                <CardHeader className="pb-2">
                  <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider">
                    Decision Summary
                    <Badge className={`text-[10px] ${op.decision === 'USE_ADD_CANDIDATE' ? 'badge-long' : op.decision === 'REMOVE_OR_DEMOTE' ? 'badge-short' : 'badge-neutral'}`}>
                      {op.decision || 'NEEDS_MORE_DATA'}
                    </Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-xs">
                  <p className="text-sm font-medium">{op.headline}</p>
                  {op.next_step && <p className="text-[11px] text-muted-foreground">{op.next_step}</p>}
                  {(op.warnings || []).length > 0 && (
                    <div className="rounded border border-warning/40 bg-warning/10 p-2">
                      {(op.warnings || []).map((w, i) => <p key={i} className="text-[11px]">{w}</p>)}
                    </div>
                  )}
                </CardContent>
              </Card>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <ActionCard
                  title="Works — paper review"
                  rows={useNowRows}
                  accentClass="border-long/30"
                  emptyText="No live-aligned USE_NOW rows. Check Screen Only for proxy candidates."
                />
                <ActionCard
                  title="Screen only — not live parity"
                  rows={screenRows}
                  accentClass="border-warning/30"
                  emptyText="No proxy or discovery-only rows."
                />
                <ActionCard
                  title="Retest"
                  rows={retestRows}
                  accentClass=""
                  emptyText="Nothing flagged for retest."
                />
                <ActionCard
                  title="Do not use"
                  rows={rejectRows}
                  accentClass="border-short/30"
                  emptyText="No rejected rows."
                />
              </div>
            </>
          )}

          {ranked && (ranked.ranked || []).length > 0 && (
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold uppercase tracking-wider">Ranked Strategies (top 50)</CardTitle>
              </CardHeader>
              <CardContent>
                <ScrollArea className="h-[420px]">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="border-b border-border/40">
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Trust</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Fidelity</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Symbol</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">TF</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Strategy</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Status</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Ready</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Trades</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">PF</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">OOS</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(ranked.ranked || []).map((row, i) => (
                        <tr
                          key={i}
                          className="border-b border-border/20 hover:bg-muted/30"
                          title={[row.trust_summary, row.fidelity_note, row.implementation_blockers].filter(Boolean).join(' · ')}
                        >
                          <td className="py-2">
                            <Badge className={`text-[10px] ${trustTierBadge(row.trust_tier)}`}>
                              {(row.trust_tier || '—').replace(/_/g, ' ')}
                            </Badge>
                          </td>
                          <td className="py-2">
                            <Badge className={`text-[9px] ${fidelityBadge(row.engine_fidelity)}`}>
                              {(row.engine_fidelity || '—').replace(/_/g, ' ')}
                            </Badge>
                          </td>
                          <td className="py-2 text-[11px] font-mono">{row.symbol || '—'}</td>
                          <td className="py-2 text-[10px]">{row.timeframe || '—'}</td>
                          <td className="py-2 text-[10px]">{row.strategy || row.strategy_name || '—'}</td>
                          <td className="py-2">
                            <Badge className={`text-[10px] ${row.status === 'STRONG_CANDIDATE' ? 'badge-long' : 'badge-neutral'}`}>
                              {row.status || '—'}
                            </Badge>
                          </td>
                          <td className="py-2">
                            <Badge className={`text-[10px] ${row.implementation_verdict === 'IMPLEMENTATION_READY' ? 'badge-long' : 'badge-short'}`}>
                              {row.implementation_verdict || '—'}
                            </Badge>
                          </td>
                          <td className="py-2 text-[10px] font-mono text-right">{row.trades ?? row.trade_count ?? '—'}</td>
                          <td className="py-2 text-[10px] font-mono text-right">{row.profit_factor != null ? fmtNum(row.profit_factor, 2) : '—'}</td>
                          <td className="py-2 text-[10px] font-mono text-right">{row.oos_return != null ? fmtNum(row.oos_return, 3) : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </ScrollArea>
              </CardContent>
            </Card>
          )}

          {ranked && (ranked.recommendations || []).length > 0 && (
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold uppercase tracking-wider">Keep / Add / Remove / Retest</CardTitle>
              </CardHeader>
              <CardContent>
                <ScrollArea className="h-[360px]">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="border-b border-border/40">
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Trust</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Action</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Fidelity</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Engine</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Strategy</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Symbol</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">TF</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Ready</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">PF</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Robust</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(ranked.recommendations || []).map((row, i) => (
                        <tr
                          key={i}
                          className="border-b border-border/20 hover:bg-muted/30"
                          title={textValue(row, 'trust_summary') || textValue(row, 'fidelity_note')}
                        >
                          <td className="py-2">
                            <Badge className={`text-[10px] ${trustTierBadge(textValue(row, 'trust_tier'))}`}>
                              {textValue(row, 'trust_tier', '—').replace(/_/g, ' ')}
                            </Badge>
                          </td>
                          <td className="py-2">
                            <Badge className={`text-[10px] ${recommendationBadge(textValue(row, 'recommendation'))}`}>
                              {textValue(row, 'recommendation')}
                            </Badge>
                          </td>
                          <td className="py-2">
                            <Badge className={`text-[9px] ${fidelityBadge(textValue(row, 'engine_fidelity'))}`}>
                              {textValue(row, 'engine_fidelity', '—').replace(/_/g, ' ')}
                            </Badge>
                          </td>
                          <td className="py-2 text-[10px]">{textValue(row, 'engine')}</td>
                          <td className="py-2 text-[10px]">{textValue(row, 'strategy_name')}</td>
                          <td className="py-2 text-[10px] font-mono">{textValue(row, 'symbol')}</td>
                          <td className="py-2 text-[10px]">{textValue(row, 'timeframe')}</td>
                          <td className="py-2">
                            <Badge className={`text-[10px] ${textValue(row, 'implementation_verdict') === 'IMPLEMENTATION_READY' ? 'badge-long' : 'badge-short'}`}>
                              {textValue(row, 'implementation_verdict')}
                            </Badge>
                          </td>
                          <td className="py-2 text-[10px] font-mono text-right">
                            {numberValue(row, 'profit_factor') != null ? fmtNum(numberValue(row, 'profit_factor') as number, 2) : '—'}
                          </td>
                          <td className="py-2 text-[10px] font-mono text-right">
                            {numberValue(row, 'robustness_score') != null ? fmtNum(numberValue(row, 'robustness_score') as number, 2) : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </ScrollArea>
              </CardContent>
            </Card>
          )}

          {ranked?.note && (!ranked.ranked || ranked.ranked.length === 0) && (
            <div className="p-3 rounded-md bg-muted/20 text-[11px] text-muted-foreground">{ranked.note}</div>
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
