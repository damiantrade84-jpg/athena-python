import { useCallback, useMemo, useState } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Input } from '@/components/ui/input';
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
import { Cpu, Radar, Activity, Shield, AlertTriangle, Copy, ClipboardList, FlaskConical, Play, ChevronDown, ChevronRight } from 'lucide-react';
import { fmtNum, toNum } from '@/lib/utils';
import { aseTradePlanText, fmtAsePrice } from '@/components/athena/ASEFillWatcher';
import type { ASEBacktestHorizon } from '@/lib/backtestPayload';
import type {
  ASEBacktestResponse,
  ASEBatchBacktestResponse,
  ASEExecutedTrade,
  ASEExecutedTradesResponse,
  ASEExecuteResponse,
  ASEHealthResponse,
  ASEScanResponse,
  ASEShadowSummary,
  ASESignalRow,
} from '@/types/athena';

const CYAN = 'hsl(185 85% 45%)';
const CYAN_DIM = 'hsl(185 55% 28%)';

const FAMILY_OPTIONS = [
  { value: 'all', label: 'All families' },
  { value: 'forex', label: 'Forex' },
  { value: 'crypto', label: 'Crypto' },
  { value: 'commodity', label: 'Commodity' },
  { value: 'equity', label: 'Equity' },
  { value: 'index_etf', label: 'Index / ETF' },
];

function statusBadgeClass(status?: string): string {
  switch ((status || '').toUpperCase()) {
    case 'TRADE': return 'badge-long';
    case 'WATCH': return 'badge-neutral';
    case 'ERROR': return 'badge-short';
    default: return 'badge-neutral';
  }
}

function triangularBadgeClass(label?: string): string {
  switch ((label || '').toUpperCase()) {
    case 'CONSISTENT': return 'border-green-500/50 text-green-400';
    case 'PARTIALLY_CONSISTENT': return 'border-amber-500/50 text-amber-400';
    case 'CONTRADICTORY': return 'border-red-500/50 text-red-400';
    default: return 'border-slate-500/50 text-slate-400';
  }
}

function pipelineStageClass(status?: string): string {
  const normalized = (status || '').toLowerCase();
  if (normalized === 'passed' || normalized === 'filled') return 'text-green-400';
  if (normalized === 'blocked' || normalized === 'failed' || normalized === 'rejected') return 'text-red-400';
  return 'text-muted-foreground';
}

function signalKey(row: ASESignalRow): string {
  return `${row.instrument || ''}:${row.horizon || ''}:${row.decisionTimeMs || 0}`;
}

function executionTicket(result?: ASEExecuteResponse): string {
  const broker = result?.execution?.result || {};
  return String(
    broker.ticket
    || broker.order_ticket
    || broker.orderId
    || broker.order_id
    || '',
  );
}

function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border px-2 py-1.5 min-w-[88px]" style={{ borderColor: CYAN_DIM, background: 'hsl(200 30% 6%)' }}>
      <div className="text-[9px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="font-mono text-[12px] truncate">{value}</div>
    </div>
  );
}

function SignalCard({
  row,
  onExecute,
  executing,
  outcome,
}: {
  row: ASESignalRow;
  onExecute: (row: ASESignalRow) => void;
  executing: boolean;
  outcome?: ASEExecuteResponse;
}) {
  const [expanded, setExpanded] = useState(false);
  const dq = row.dataQuality || {};
  const health = row.modelHealth || {};
  const monitor = (health.monitor || {}) as Record<string, unknown>;
  const drift = toNum(health.driftScore, 0);
  const pCal = toNum(row.probabilityPositive, 0);
  const retQ = row.returnQ || {};
  const q10 = toNum(retQ.q10 ?? retQ['0.1'], NaN);
  const q90 = toNum(retQ.q90 ?? retQ['0.9'], NaN);
  const blocker = String(dq.blocker || health.blocker || '');
  const modelsMissing = dq.artifactsPresent === false || health.errorReason === 'artifact_missing';
  const fxContext = row.fxContext || null;
  const triangularLabel = row.triangular?.label;

  return (
    <div
      className="rounded-lg border p-3 relative overflow-hidden font-mono"
      style={{ borderColor: CYAN_DIM, background: 'hsl(200 30% 6%)' }}
    >
      <div className="flex items-start justify-between gap-2 relative">
        <div>
          <div className="font-bold text-sm" style={{ color: CYAN }}>
            {row.instrument || '—'}
          </div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground mt-0.5">
            {row.modelFamily} · {row.horizon}
          </div>
        </div>
        <div className="flex flex-wrap gap-1 justify-end">
          <Badge className={statusBadgeClass(row.decisionStatus)}>{row.decisionStatus}</Badge>
          {row.direction && row.direction !== 'NONE' && (
            <Badge variant="outline">{row.direction}</Badge>
          )}
          {triangularLabel && (
            <Badge variant="outline" className={`text-[9px] ${triangularBadgeClass(triangularLabel)}`}>
              {triangularLabel.replace('PARTIALLY_CONSISTENT', 'PARTIAL').replace('INSUFFICIENT_DATA', 'INSUFFICIENT')}
            </Badge>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 mt-3 text-xs">
        <div>
          <div className="text-muted-foreground text-[10px]">E[net R]</div>
          <div className={toNum(row.expectedNetR) >= 0 ? 'text-long' : 'text-short'}>
            {fmtNum(row.expectedNetR, 3)}
          </div>
        </div>
        <div>
          <div className="text-muted-foreground text-[10px]">P(cal)</div>
          <div>{fmtNum(pCal * 100, 1)}%</div>
          {!Number.isNaN(q10) && !Number.isNaN(q90) && (
            <div className="text-[10px] text-muted-foreground">
              q10–q90: {fmtNum(q10, 2)} / {fmtNum(q90, 2)} R
            </div>
          )}
        </div>
        <div>
          <div className="text-muted-foreground text-[10px]">Strength</div>
          <div>{row.signalStrength ?? 0}/100</div>
        </div>
      </div>

      {(row.entryReference || row.sl) && (
        <div className="grid grid-cols-4 gap-1 mt-2 text-[10px] text-muted-foreground">
          <span>entry {fmtNum(row.entryReference, 5)}</span>
          <span>SL {fmtNum(row.sl, 5)}</span>
          <span>TP1 {fmtNum(row.tp1, 5)}</span>
          <span>TP2 {fmtNum(row.tp2, 5)}</span>
        </div>
      )}

      <div className="flex flex-wrap gap-2 mt-2 text-[10px] text-muted-foreground">
        <span>route: {String(dq.route || 'core')}</span>
        <span>v: {row.modelVersion || '—'}</span>
        {blocker && (
          <span className="text-amber-400" title={blocker}>
            blocker: {blocker}
          </span>
        )}
        {!blocker && modelsMissing && (
          <span className="text-amber-400">models: run train-all</span>
        )}
        {drift > 0.15 && (
          <span className="text-amber-400 inline-flex items-center gap-0.5">
            <AlertTriangle className="h-3 w-3" /> drift {fmtNum(drift, 2)}
          </span>
        )}
        {Boolean(monitor.watchMax) && <span className="text-amber-400">WATCH-max</span>}
      </div>

      <button
        type="button"
        className="mt-2 inline-flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground hover:text-foreground"
        onClick={() => setExpanded((current) => !current)}
        aria-expanded={expanded}
      >
        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        Factors & context
      </button>

      {expanded && (
        <div className="mt-2 space-y-2 border-t pt-2" style={{ borderColor: CYAN_DIM }}>
          <div className="space-y-1">
            {(row.primarySignals || []).length === 0 && (
              <div className="text-[10px] text-muted-foreground">No layer-1 contributions recorded.</div>
            )}
            {(row.primarySignals || []).map((signal) => {
              const strength = Math.max(0, Math.min(1, Math.abs(toNum(signal.rawStrength, 0))));
              return (
                <div key={`${signal.name}-${signal.direction}`} className="grid grid-cols-[72px_16px_1fr_40px] items-center gap-1 text-[10px]">
                  <span className="truncate">{signal.name}</span>
                  <span className={signal.direction > 0 ? 'text-long' : signal.direction < 0 ? 'text-short' : 'text-muted-foreground'}>
                    {signal.direction > 0 ? '↑' : signal.direction < 0 ? '↓' : '→'}
                  </span>
                  <span className="h-1.5 rounded bg-slate-800 overflow-hidden">
                    <span
                      className="block h-full rounded"
                      style={{ width: `${strength * 100}%`, background: signal.direction < 0 ? 'hsl(0 70% 50%)' : CYAN }}
                    />
                  </span>
                  <span className="text-right text-muted-foreground">{fmtNum(strength, 2)}</span>
                </div>
              );
            })}
          </div>

          <div className="flex flex-wrap gap-1 text-[10px]">
            {fxContext ? (
              <>
                <Badge variant="outline" className={toNum(fxContext.bias) >= 0 ? 'text-long' : 'text-short'}>
                  FX bias {fmtNum(fxContext.bias, 2)}
                </Badge>
                <Badge variant="outline">carry {fxContext.carry_diff == null ? '—' : fmtNum(fxContext.carry_diff, 2)}</Badge>
                <Badge variant="outline">trend {fmtNum(fxContext.trend_diff, 2)}</Badge>
                <Badge variant="outline" className={fxContext.freshness === 'degraded' ? 'text-amber-400' : 'text-muted-foreground'}>
                  {fxContext.freshness || 'unknown'}
                </Badge>
              </>
            ) : (
              <span className="text-muted-foreground">FX context not applicable / not recorded.</span>
            )}
          </div>
        </div>
      )}

      {row.decisionStatus === 'TRADE' && (
        <div className="mt-3 flex items-center justify-between gap-2 border-t pt-2" style={{ borderColor: CYAN_DIM }}>
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-[10px] uppercase tracking-wide"
            style={{ borderColor: CYAN, color: CYAN }}
            onClick={() => onExecute(row)}
            disabled={executing}
          >
            {executing ? 'Executing...' : 'Execute Demo'}
          </Button>
          {outcome && (
            <div className="text-right text-[10px]">
              <div className={outcome.executed ? 'text-long' : 'text-amber-400'}>
                {outcome.executed
                  ? `Filled${executionTicket(outcome) ? ` #${executionTicket(outcome)}` : ''}`
                  : `Blocked: ${outcome.reason || outcome.error || 'unknown'}`}
              </div>
              <div className="text-muted-foreground">
                gate → guardian → risk → {outcome.executed ? 'fill' : 'blocked'}
                {outcome.execution?.approval_volume != null && ` · approved ${fmtNum(outcome.execution.approval_volume, 2)}`}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ASEPanel() {
  const { showToast } = useStore();
  const [family, setFamily] = useState('all');
  const [horizon, setHorizon] = useState<'intraday' | 'swing' | 'both'>('both');
  const [pairFilter, setPairFilter] = useState('');
  const [scanResult, setScanResult] = useState<ASEScanResponse | null>(null);
  const [confirmSignal, setConfirmSignal] = useState<ASESignalRow | null>(null);
  const [executionResults, setExecutionResults] = useState<Record<string, ASEExecuteResponse>>({});
  const [backtestSymbol, setBacktestSymbol] = useState('BTCUSDT');
  const [backtestHorizon, setBacktestHorizon] = useState<ASEBacktestHorizon>('both');
  const [backtestLookbackDays, setBacktestLookbackDays] = useState(365);
  const [backtestFamily, setBacktestFamily] = useState('all');
  const [backtestResult, setBacktestResult] = useState<ASEBacktestResponse | null>(null);
  const [backtestBatch, setBacktestBatch] = useState<ASEBatchBacktestResponse | null>(null);

  const healthUrl = `/api/ase-health?horizon=${horizon === 'both' ? 'intraday' : horizon}`;
  const { data: health } = useApiPoll<ASEHealthResponse>(healthUrl, 60000);

  const { data: summary, refresh: refreshSummary } = useApiPoll<ASEShadowSummary>(
    '/api/ase-journal-summary',
    30000,
  );
  const { data: executedTrades, refresh: refreshExecuted } = useApiPoll<ASEExecutedTradesResponse>(
    '/api/ase-executed-trades?limit=50',
    30000,
  );
  const { post: postScan, loading: scanning, error: scanError } = useApiPost<ASEScanResponse>();
  const { post: postExecute, loading: executing, error: executeError } = useApiPost<ASEExecuteResponse>();
  const { post: postBacktest, loading: backtesting, error: backtestError } = useApiPost<ASEBacktestResponse>();
  const { post: postBacktestAll, loading: backtestingAll, error: backtestAllError } = useApiPost<ASEBatchBacktestResponse>();

  const runScan = useCallback(async () => {
    const tokens = pairFilter.split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean);
    const body: Record<string, unknown> = {
      horizon,
      writeJournal: true,
      executeTrades: false,
    };
    if (family !== 'all') body.family = family;
    if (tokens.length) body.symbols = tokens;

    const result = await postScan('/api/ase-scan', body);
    if (!result || result.error) {
      showToast(`ASE scan failed: ${result?.error || 'unknown'}`, 'error');
      return;
    }
    setScanResult(result);
    await refreshSummary();
    const tradeN = (result.signals || []).filter((s) => s.decisionStatus === 'TRADE').length;
    const watchN = (result.signals || []).filter((s) => s.decisionStatus === 'WATCH').length;
    showToast(`ASE scan: ${tradeN} TRADE · ${watchN} WATCH · ${result.candidateCount ?? result.signalCount ?? 0} rows`, 'success');
  }, [family, horizon, pairFilter, postScan, refreshSummary, showToast]);

  const confirmExecute = useCallback(async () => {
    if (!confirmSignal?.instrument || !confirmSignal.horizon) {
      showToast('ASE execution failed: signal identity is missing', 'error');
      return;
    }
    const key = signalKey(confirmSignal);
    const result = await postExecute('/api/ase-execute', {
      instrument: confirmSignal.instrument,
      horizon: confirmSignal.horizon,
    });
    if (!result) {
      showToast('ASE execution failed before a result was returned', 'error');
      return;
    }
    setExecutionResults((current) => ({ ...current, [key]: result }));
    await refreshSummary();
    await refreshExecuted();
    if (result.executed) {
      const ticket = executionTicket(result);
      showToast(
        `ASE demo executed ${confirmSignal.direction || ''} ${confirmSignal.instrument}${ticket ? ` - ticket ${ticket}` : ''}`,
        'success',
      );
    } else {
      showToast(`ASE blocked ${confirmSignal.instrument}: ${result.reason || result.error || 'unknown'}`, 'error');
    }
    setConfirmSignal(null);
  }, [confirmSignal, postExecute, refreshSummary, refreshExecuted, showToast]);

  const copyTradePlan = useCallback(async (trade: ASEExecutedTrade) => {
    try {
      await navigator.clipboard.writeText(aseTradePlanText(trade));
      showToast('Trade plan copied', 'success');
    } catch {
      showToast('Copy failed — clipboard unavailable', 'error');
    }
  }, [showToast]);

  const runSingleBacktest = useCallback(async () => {
    const symbol = backtestSymbol.trim();
    if (!symbol) {
      showToast('ASE backtest requires a symbol', 'error');
      return;
    }
    const result = await postBacktest('/api/backtest-ase', {
      pair: symbol,
      horizon: backtestHorizon,
      lookbackDays: backtestLookbackDays,
      validation_mode: 'standard',
    });
    if (!result) {
      showToast('ASE backtest failed before a result was returned', 'error');
      return;
    }
    setBacktestResult(result);
    setBacktestBatch(null);
    if (result.error) {
      showToast(`ASE backtest: ${result.error}`, 'error');
      return;
    }
    showToast(`ASE backtest ${symbol}: ${result.totalTrades ?? 0} trades · SQN ${fmtNum(result.sqn, 2)}`, 'success');
  }, [backtestSymbol, backtestHorizon, backtestLookbackDays, postBacktest, showToast]);

  const runFamilyBacktest = useCallback(async () => {
    const body: Record<string, unknown> = {
      horizon: backtestHorizon,
      lookbackDays: backtestLookbackDays,
      validation_mode: 'standard',
    };
    if (backtestFamily !== 'all') body.family = backtestFamily;
    const result = await postBacktestAll('/api/backtest-ase-all', body);
    if (!result) {
      showToast('ASE universe backtest failed before a result was returned', 'error');
      return;
    }
    setBacktestBatch(result);
    setBacktestResult(null);
    if (result.error) {
      showToast(`ASE universe backtest: ${result.error}`, 'error');
      return;
    }
    showToast(`ASE universe backtest: ${result.results?.length ?? 0}/${result.totalPairs ?? 0} pairs OK`, 'success');
  }, [backtestFamily, backtestHorizon, backtestLookbackDays, postBacktestAll, showToast]);

  const signals = useMemo(() => {
    const rows = scanResult?.signals || [];
    return [...rows].sort((a, b) => toNum(b.expectedNetR) - toNum(a.expectedNetR));
  }, [scanResult]);

  const activeSignals = signals.filter((s) => s.decisionStatus === 'TRADE' || s.decisionStatus === 'WATCH');
  const executedRows = executedTrades?.trades || [];
  const diagnostics = scanResult?.diagnostics;
  const artifactFamilies = diagnostics?.familiesWithArtifacts?.length
    ?? health?.familiesWithArtifacts?.length
    ?? 0;
  const showArtifactsBanner = artifactFamilies < 5
    || (health?.blockers || []).includes('no_frozen_artifacts');
  const trainingFamilies = (summary as ASEShadowSummary & { trainingReport?: { families?: Array<Record<string, string>> } })?.trainingReport?.families || [];
  const provenanceRows = useMemo(() => (health?.modelProvenance || []).filter((row) => (
    (family === 'all' || row.family === family)
    && (horizon === 'both' || row.horizon === horizon)
  )), [family, health?.modelProvenance, horizon]);

  return (
    <div className="h-full min-h-0 min-w-0 overflow-x-hidden flex flex-col gap-3 p-3" style={{ background: 'hsl(200 35% 4%)', fontFamily: '"IBM Plex Mono", monospace' }}>
      <div
        className="shrink-0 rounded-lg border px-4 py-3 flex items-center justify-between"
        style={{ borderColor: CYAN_DIM, background: 'linear-gradient(135deg, hsl(195 40% 8%), hsl(200 35% 5%))' }}
      >
        <div className="flex items-center gap-3">
          <Cpu className="h-6 w-6" style={{ color: CYAN }} />
          <div>
            <h1 className="text-lg font-semibold tracking-wide" style={{ color: CYAN }}>
              ASE Command Centre
            </h1>
            <p className="text-[11px] text-muted-foreground">
              Adaptive Specialist Engine · demo execution · risk_engine sizing
            </p>
          </div>
        </div>
        <Badge variant="outline" className="uppercase tracking-widest text-[10px]" style={{ borderColor: CYAN, color: CYAN }}>
          OPERATIONAL
        </Badge>
      </div>

      {scanError && <ErrorBanner message={scanError} />}
      {executeError && <ErrorBanner message={executeError} />}
      {backtestError && <ErrorBanner message={backtestError} />}
      {backtestAllError && <ErrorBanner message={backtestAllError} />}

      {provenanceRows.length > 0 && (
        <div className="shrink-0 min-w-0 w-full max-w-full overflow-hidden rounded-lg border px-3 py-2" style={{ borderColor: CYAN_DIM, background: 'hsl(200 30% 6%)' }}>
          <div className="text-[9px] uppercase tracking-wider text-muted-foreground mb-1">Frozen model provenance</div>
          <div className="grid min-w-0 grid-cols-1 gap-1.5 font-mono text-[10px] md:grid-cols-2 2xl:grid-cols-5">
            {provenanceRows.map((row) => (
              <div
                key={`${row.family}-${row.horizon}`}
                className="min-w-0 rounded border px-2 py-1"
                style={{ borderColor: 'hsl(185 40% 16%)' }}
              >
                <div className="flex min-w-0 items-center gap-1.5">
                  <span className="truncate" style={{ color: CYAN }}>{row.family}/{row.horizon}</span>
                  <span className="shrink-0">v:{row.version || '—'}</span>
                  {row.available === false ? (
                    <span className="shrink-0 text-amber-400">FLAT-only</span>
                  ) : row.runtimeEligible === false ? (
                    <span className="shrink-0 text-amber-400">RESEARCH-only</span>
                  ) : (
                    <span className="shrink-0">thr:{fmtNum(row.threshold, 3)}</span>
                  )}
                  {row.thresholdFallback && <span className="shrink-0 text-amber-400">fallback</span>}
                </div>
                <div
                  className="truncate text-muted-foreground"
                  title={`${row.thresholdSource || 'source not recorded'} · dataset ${row.datasetHash || 'not recorded'}${row.promotionFailures?.length ? ` · gate failures: ${row.promotionFailures.join(', ')}` : ''}`}
                >
                  {row.thresholdSource || 'source not recorded'} · ds:{row.datasetHash ? row.datasetHash.slice(0, 8) : '—'}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {showArtifactsBanner && (
        <div
          className="shrink-0 rounded-lg border px-3 py-2 text-xs flex items-start gap-2"
          style={{ borderColor: CYAN_DIM, background: 'hsl(200 30% 8%)', color: 'hsl(185 70% 70%)' }}
        >
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>
            Layer 2 models missing for most pairs ({artifactFamilies}/5 families trained).
            Signals show <code className="font-mono">model_not_trained</code> until you run:{' '}
            <code className="font-mono">py ase_cli.py ingest --sources eodhd,mt5,binance,dukascopy,cot,fred</code>,{' '}
            optionally <code className="font-mono">py ase_cli.py ingest --sources bybit</code> for crypto derivatives,{' '}
            <code className="font-mono">py -m athena_research.ase.run_phase1</code>,{' '}
            <code className="font-mono">py ase_cli.py train-all</code>.
          </span>
        </div>
      )}

      <div className="grid min-w-0 grid-cols-1 lg:grid-cols-4 gap-3 shrink-0">
        <Card className="min-w-0 lg:col-span-3 border-0" style={{ background: 'hsl(200 30% 7%)', border: `1px solid ${CYAN_DIM}` }}>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2" style={{ color: CYAN }}>
              <Radar className="h-4 w-4" /> Live Scan
            </CardTitle>
          </CardHeader>
          <CardContent className="grid min-w-0 grid-cols-1 gap-2 items-end sm:grid-cols-[140px_120px_minmax(180px,1fr)_auto]">
            <Select value={family} onValueChange={setFamily}>
              <SelectTrigger className="h-8 w-full text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {FAMILY_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={horizon} onValueChange={(v) => setHorizon(v as 'intraday' | 'swing' | 'both')}>
              <SelectTrigger className="h-8 w-full text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="both">Both horizons</SelectItem>
                <SelectItem value="intraday">Intraday H1</SelectItem>
                <SelectItem value="swing">Swing D1</SelectItem>
              </SelectContent>
            </Select>
            <Input
              className="h-8 min-w-0 w-full text-xs"
              placeholder="Optional symbols: EURUSD, BTCUSDT"
              value={pairFilter}
              onChange={(e) => setPairFilter(e.target.value)}
            />
            <Button
              size="sm"
              onClick={runScan}
              disabled={scanning}
              className="h-8 w-full whitespace-nowrap sm:w-auto"
              style={{ background: CYAN, color: 'hsl(200 40% 6%)' }}
            >
              {scanning ? 'Scanning…' : 'Run ASE Scan'}
            </Button>
          </CardContent>
        </Card>

        <Card className="min-w-0 border-0" style={{ background: 'hsl(200 30% 7%)', border: `1px solid ${CYAN_DIM}` }}>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2" style={{ color: CYAN }}>
              <Shield className="h-4 w-4" /> Trade Journal
            </CardTitle>
          </CardHeader>
          <CardContent className="text-xs space-y-1">
            <div>rows: {summary?.journal?.totalRows ?? '—'}</div>
            <div>realized R: {fmtNum((summary?.journal as { realizedNetR?: number })?.realizedNetR, 3)}</div>
            <div>reconciled: {summary?.journal?.reconciled ?? '—'}</div>
          </CardContent>
        </Card>
      </div>

      <Card className="shrink-0 border-0" style={{ background: 'hsl(200 30% 7%)', border: `1px solid ${CYAN_DIM}` }}>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2" style={{ color: CYAN }}>
            <FlaskConical className="h-4 w-4" /> ASE Backtest
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2 items-end">
            <Input
              className="h-8 text-xs w-[160px]"
              value={backtestSymbol}
              onChange={(e) => setBacktestSymbol(e.target.value)}
              placeholder="BTCUSDT"
            />
            <Select value={backtestHorizon} onValueChange={(v) => setBacktestHorizon(v as ASEBacktestHorizon)}>
              <SelectTrigger className="w-[126px] h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="both">Both horizons</SelectItem>
                <SelectItem value="intraday">Intraday H1</SelectItem>
                <SelectItem value="swing">Swing D1</SelectItem>
              </SelectContent>
            </Select>
            <Input
              type="number"
              min={30}
              step={30}
              className="h-8 text-xs w-[104px]"
              value={backtestLookbackDays}
              onChange={(e) => setBacktestLookbackDays(Number(e.target.value) || 365)}
              aria-label="ASE backtest lookback days"
            />
            <Button
              size="sm"
              variant="outline"
              onClick={runSingleBacktest}
              disabled={backtesting}
              className="h-8 gap-1 text-xs"
              style={{ borderColor: CYAN, color: CYAN }}
            >
              <Play className="h-3 w-3" />
              {backtesting ? 'Backtesting...' : 'Run symbol'}
            </Button>
            <Select value={backtestFamily} onValueChange={setBacktestFamily}>
              <SelectTrigger className="w-[140px] h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {FAMILY_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              size="sm"
              onClick={runFamilyBacktest}
              disabled={backtestingAll}
              className="h-8 gap-1 text-xs"
              style={{ background: CYAN, color: 'hsl(200 40% 6%)' }}
            >
              <Play className="h-3 w-3" />
              {backtestingAll ? 'Backtesting...' : 'Run universe'}
            </Button>
          </div>

          {backtestResult && (
            <div className="grid grid-cols-2 md:grid-cols-6 gap-2 text-xs">
              <MiniStat label="pair" value={backtestResult.pair || backtestResult.symbol || backtestSymbol} />
              <MiniStat label="trades" value={backtestResult.totalTrades ?? backtestResult.aseDiagnostics?.tradeCount ?? 0} />
              <MiniStat label="WR" value={backtestResult.winRate != null ? `${fmtNum(backtestResult.winRate, 1)}%` : '—'} />
              <MiniStat label="SQN" value={backtestResult.sqn != null ? fmtNum(backtestResult.sqn, 2) : '—'} />
              <MiniStat label="candidates" value={backtestResult.aseDiagnostics?.candidateCount ?? '—'} />
              <MiniStat label="lookback" value={backtestResult.aseDiagnostics?.lookbackDays != null ? `${backtestResult.aseDiagnostics.lookbackDays}d` : `${backtestLookbackDays}d`} />
              {backtestResult.error && (
                <div className="col-span-full text-[11px] text-amber-400">
                  {backtestResult.error}
                </div>
              )}
              {backtestResult.aseDiagnostics?.nonTradeReasons && (
                <div className="col-span-full flex flex-wrap gap-1 text-[10px] font-mono">
                  {Object.entries(backtestResult.aseDiagnostics.nonTradeReasons).map(([reason, count]) => (
                    <Badge key={reason} variant="outline" className="text-amber-300">
                      {reason}:{count}
                    </Badge>
                  ))}
                </div>
              )}
              {(backtestResult.trades || []).some((trade) => 'swapR' in trade || 'totalCostR' in trade) && (
                <div className="col-span-full overflow-x-auto rounded border" style={{ borderColor: CYAN_DIM }}>
                  <table className="w-full text-[10px] font-mono">
                    <thead className="text-muted-foreground">
                      <tr>
                        <th className="text-left px-2 py-1">Trade</th>
                        <th className="text-right px-2 py-1">Gross R</th>
                        <th className="text-right px-2 py-1">Entry cost R</th>
                        <th className="text-right px-2 py-1">Swap R</th>
                        <th className="text-right px-2 py-1">Total cost R</th>
                        <th className="text-right px-2 py-1">Net R</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(backtestResult.trades || []).slice(-5).reverse().map((trade, index) => (
                        <tr key={`${String(trade.instrument || trade.date || index)}-${index}`} className="border-t" style={{ borderColor: CYAN_DIM }}>
                          <td className="px-2 py-1">{String(trade.instrument || trade.date || `#${index + 1}`)}</td>
                          <td className="text-right px-2 py-1">{fmtNum(trade.grossR, 3)}</td>
                          <td className="text-right px-2 py-1">{fmtNum(trade.costR, 3)}</td>
                          <td className="text-right px-2 py-1">{fmtNum(trade.swapR, 3)}</td>
                          <td className="text-right px-2 py-1">{fmtNum(trade.totalCostR, 3)}</td>
                          <td className={`text-right px-2 py-1 ${toNum(trade.resultR) >= 0 ? 'text-long' : 'text-short'}`}>{fmtNum(trade.resultR, 3)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {backtestBatch && (
            <div className="space-y-2 text-xs">
              <div className="flex flex-wrap gap-2">
                <MiniStat label="family" value={backtestBatch.family || backtestFamily} />
                <MiniStat label="pairs" value={backtestBatch.totalPairs ?? '—'} />
                <MiniStat label="OK" value={backtestBatch.results?.length ?? 0} />
                <MiniStat label="errors" value={backtestBatch.errors?.length ?? 0} />
              </div>
              {(backtestBatch.results || []).slice(0, 5).map((row) => (
                <div key={`${row.pair}-${row.symbol}`} className="flex items-center gap-3 text-[11px] font-mono border-t pt-1" style={{ borderColor: CYAN_DIM }}>
                  <span className="w-24 truncate" style={{ color: CYAN }}>{row.pair || row.symbol || '—'}</span>
                  <span>trades {row.totalTrades ?? 0}</span>
                  <span>WR {row.winRate != null ? `${fmtNum(row.winRate, 1)}%` : '—'}</span>
                  <span>SQN {row.sqn != null ? fmtNum(row.sqn, 2) : '—'}</span>
                </div>
              ))}
              {(backtestBatch.errors || []).slice(0, 3).map((row) => (
                <div key={`${row.pair}-${row.error}`} className="text-[10px] text-amber-400 font-mono">
                  {row.pair}: {row.error}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {trainingFamilies.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 shrink-0">
          {trainingFamilies.map((f) => (
            <Card key={`${f.family}-${f.horizon}`} className="border-0 p-2 text-[10px]" style={{ border: `1px solid ${CYAN_DIM}`, background: 'hsl(200 30% 7%)' }}>
              <div style={{ color: CYAN }}>{f.family} / {f.horizon}</div>
              <div>E[R] eval: {f.expectancy}</div>
              <div>thr: {f.threshold} · n={f.evalTrades}</div>
            </Card>
          ))}
        </div>
      )}

      {executedRows.length > 0 && (
        <Card className="shrink-0 border-0" style={{ background: 'hsl(200 30% 6%)', border: `1px solid ${CYAN_DIM}` }}>
          <CardHeader className="pt-3 pb-1 px-4">
            <CardTitle className="text-[11px] uppercase tracking-wider flex items-center justify-between" style={{ color: CYAN }}>
              <span className="flex items-center gap-2">
                <ClipboardList className="h-3.5 w-3.5" />
                Executed fills — {executedTrades?.totalExecuted ?? executedRows.length} demo trades
              </span>
              <span className="text-[10px] font-normal normal-case tracking-normal text-muted-foreground">
                mirror manually on funded account
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-3 pt-1">
            <ScrollArea className="max-h-52">
              <div className="space-y-px">
                {executedRows.map((trade) => (
                  <div
                    key={`${trade.instrument}-${trade.decisionTimeMs}-${trade.horizon}`}
                    className="flex items-center gap-3 py-1.5 rounded text-[11px] font-mono"
                    style={{ borderBottom: `1px solid hsl(185 40% 12%)` }}
                  >
                    <span className="text-[10px] text-muted-foreground whitespace-nowrap w-24 shrink-0">
                      {trade.decisionTimeMs
                        ? new Date(trade.decisionTimeMs).toLocaleString([], {
                            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                          })
                        : '—'}
                    </span>
                    <span className="font-semibold w-20 shrink-0 truncate" style={{ color: CYAN }}>
                      {trade.instrument || '—'}
                    </span>
                    <Badge
                      className={`text-[9px] px-1.5 py-0 h-4 shrink-0 ${trade.direction === 'LONG' ? 'badge-long' : 'badge-short'}`}
                    >
                      {trade.direction || '—'}
                    </Badge>
                    <div className="flex items-center gap-3 flex-1 min-w-0 text-[10px]">
                      <span className="text-muted-foreground">
                        @ <span className="text-foreground">{fmtAsePrice(trade.entryReference)}</span>
                      </span>
                      <span className="text-muted-foreground hidden sm:inline">
                        SL <span className="text-red-400">{fmtAsePrice(trade.sl)}</span>
                      </span>
                      <span className="text-muted-foreground">
                        TP <span className="text-green-400">{fmtAsePrice(trade.tp1)}</span>
                      </span>
                      <span className="text-muted-foreground hidden lg:inline">
                        RR <span className="text-foreground">{fmtNum(trade.rr1, 2)}</span>
                      </span>
                    </div>
                    <div className="hidden lg:flex items-center gap-1 text-[9px] shrink-0" title={trade.executionReason || undefined}>
                      {(['gate', 'guardian', 'risk', 'fill'] as const).map((stage, index) => (
                        <span key={stage} className={pipelineStageClass(trade.pipeline?.[stage])}>
                          {index > 0 && <span className="text-muted-foreground mr-1">→</span>}
                          {stage}:{trade.pipeline?.[stage] || '—'}
                        </span>
                      ))}
                      {trade.approvalVolume != null && (
                        <span className="text-muted-foreground">vol {fmtNum(trade.approvalVolume, 2)}</span>
                      )}
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      {(trade.brokerFill?.ticket || trade.orderId) && (
                        <span className="text-[9px] text-muted-foreground hidden xl:inline">
                          #{trade.brokerFill?.ticket || trade.orderId}
                        </span>
                      )}
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-5 w-6 p-0 opacity-40 hover:opacity-100 transition-opacity"
                        title="Copy trade plan"
                        onClick={() => void copyTradePlan(trade)}
                      >
                        <Copy className="h-3 w-3" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>
      )}

      <Card className="flex-1 min-h-0 border-0 flex flex-col" style={{ background: 'hsl(200 30% 6%)', border: `1px solid ${CYAN_DIM}` }}>
        <CardHeader className="pb-2 shrink-0">
          <CardTitle className="text-sm flex items-center justify-between" style={{ color: CYAN }}>
            <span className="flex items-center gap-2">
              <Activity className="h-4 w-4" />
              Signals ({activeSignals.length} active / {signals.length} total)
            </span>
            {health && (
              <span className="text-[10px] font-normal text-muted-foreground">
                PTIS {health.ptisSeriesCount ?? '—'} · artifacts {health.familiesWithArtifacts?.length ?? 0}
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex-1 min-h-0 p-0">
          <ScrollArea className="h-full px-4 pb-4">
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {signals.length === 0 && (
                <p className="text-sm text-muted-foreground col-span-full py-8 text-center">
                  Run an ASE scan to populate live signals for all instruments.
                  {diagnostics?.familiesWithArtifacts?.length === 0 && (
                    <> Train models: <code>py ase_cli.py train-all</code></>
                  )}
                </p>
              )}
              {signals.map((row) => (
                <SignalCard
                  key={`${row.instrument}-${row.decisionTimeMs}-${row.horizon}`}
                  row={row}
                  onExecute={setConfirmSignal}
                  executing={executing && signalKey(confirmSignal || {}) === signalKey(row)}
                  outcome={executionResults[signalKey(row)]}
                />
              ))}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>

      <AlertDialog
        open={!!confirmSignal}
        onOpenChange={(open) => {
          if (!open && !executing) setConfirmSignal(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm ASE Demo Execution</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-xs">
                <div>
                  Execute <b>{confirmSignal?.direction}</b> {confirmSignal?.instrument} on{' '}
                  <b>{confirmSignal?.horizon}</b>?
                </div>
                <div className="font-mono">
                  Displayed reference: {fmtNum(confirmSignal?.entryReference, 5)} ·
                  SL: {fmtNum(confirmSignal?.sl, 5)} ·
                  TP1: {fmtNum(confirmSignal?.tp1, 5)}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  The server will re-scan this symbol and execute only if it is still TRADE.
                  Demo-account, freshness, guardian, risk, and broker gates remain mandatory.
                </div>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={executing}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void confirmExecute()} disabled={executing}>
              {executing ? 'Executing...' : 'Confirm Demo Execute'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
