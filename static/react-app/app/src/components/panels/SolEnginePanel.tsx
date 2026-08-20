import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Crosshair,
  DatabaseZap,
  FlaskConical,
  Gauge,
  Orbit,
  Play,
  RefreshCw,
  ScanSearch,
  ShieldCheck,
  Sparkles,
  XCircle,
} from 'lucide-react';

import { useStore } from '@/hooks/useStore';
import apiClient from '@/lib/apiClient';
import { cn } from '@/lib/utils';
import { LiveQuoteChip } from '@/components/shared';
import {
  solCanAttest,
  solComponentLabel,
  solDecisionClass,
  solPreferredMode,
  solPrice,
  solScanProgress,
  solSetupLabel,
  type SolAccounts,
  type SolCapabilities,
  type SolDecision,
  type SolExecutionMode,
  type SolExecutionRecord,
  type SolHealth,
  type SolPreview,
  type SolReplayResult,
  type SolScanState,
  type SolSignal,
} from '@/lib/solEngine';
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
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

const ASSET_TYPES = [
  ['forex', 'FX'],
  ['crypto', 'Crypto'],
  ['commodity', 'Metals & energy'],
  ['index', 'Indices'],
  ['stock', 'Stocks'],
  ['etf', 'ETFs'],
  ['etf_bond', 'Bond ETFs'],
] as const;

const DECISIONS: SolDecision[] = ['READY', 'WATCH', 'BLOCKED'];

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error || 'Unknown error');
}

function shortTime(value?: string | null): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function makeIdempotencyKey(signalId: string): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `sol-ui:${signalId}:${random}`;
}

function Metric({ label, value, note }: { label: string; value: string | number; note?: string }) {
  return (
    <div className="surface-inset min-w-0 px-3 py-2.5">
      <div className="label">{label}</div>
      <div className="readout mt-1 truncate text-lg font-semibold text-foreground">{value}</div>
      {note ? <div className="mt-1 truncate text-[10px] text-muted-foreground">{note}</div> : null}
    </div>
  );
}

function DecisionIcon({ decision }: { decision: SolDecision }) {
  if (decision === 'READY') return <CheckCircle2 className="h-3.5 w-3.5" />;
  if (decision === 'WATCH') return <Clock3 className="h-3.5 w-3.5" />;
  return <XCircle className="h-3.5 w-3.5" />;
}

function ProvenanceCard({ timeframe, meta }: { timeframe: string; meta: Record<string, unknown> }) {
  const provider = typeof meta.provider === 'string' ? meta.provider : 'unknown';
  const bars = typeof meta.bars === 'number' && Number.isFinite(meta.bars) ? meta.bars : 0;
  const formingDropped = typeof meta.formingBarsDropped === 'number' && Number.isFinite(meta.formingBarsDropped)
    ? meta.formingBarsDropped
    : 0;
  const postAsOfDropped = typeof meta.postAsOfBarsDropped === 'number' && Number.isFinite(meta.postAsOfBarsDropped)
    ? meta.postAsOfBarsDropped
    : 0;
  const futureDropped = typeof meta.futureBarsDropped === 'number' && Number.isFinite(meta.futureBarsDropped)
    ? meta.futureBarsDropped
    : 0;
  const lastClosedAt = typeof meta.lastClosedAt === 'string' ? meta.lastClosedAt : null;
  const volumeSources = Array.isArray(meta.volumeSources)
    ? meta.volumeSources.filter((value): value is string => typeof value === 'string').join(', ')
    : '';
  return (
    <div className="surface-inset min-w-0 px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="readout text-xs font-semibold text-foreground">{timeframe}</span>
        <span className="truncate text-[9px] uppercase tracking-wide text-warning" title={provider}>{provider}</span>
      </div>
      <div className="mt-1 text-[10px] text-muted-foreground">
        {bars} closed · {formingDropped} forming dropped{postAsOfDropped ? ` · ${postAsOfDropped} post-snapshot` : ''}
      </div>
      {futureDropped ? <div className="mt-0.5 text-[9px] font-medium text-short">{futureDropped} future-dated rejected</div> : null}
      <div className="mt-0.5 truncate text-[9px] text-muted-foreground" title={lastClosedAt || undefined}>
        close {shortTime(lastClosedAt)}{volumeSources ? ` · ${volumeSources}` : ''}
      </div>
    </div>
  );
}

function SignalRow({ signal, selected, onSelect }: { signal: SolSignal; selected: boolean; onSelect: () => void }) {
  const scoreAngle = `${Math.max(0, Math.min(100, signal.score)) * 3.6}deg`;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        'group w-full rounded-xl border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        selected ? 'border-warning/50 bg-warning/[0.06]' : 'border-border/70 bg-card/50 hover:border-border hover:bg-card/80',
      )}
    >
      <div className="flex items-start gap-3">
        <div
          className="relative grid h-12 w-12 shrink-0 place-items-center rounded-full"
          style={{ background: `conic-gradient(#F6C453 ${scoreAngle}, hsl(var(--muted)) 0deg)` }}
          aria-label={`Score ${signal.score.toFixed(1)} out of 100`}
        >
          <div className="absolute inset-[4px] rounded-full bg-card" />
          <span className="readout relative text-xs font-semibold">{signal.score.toFixed(0)}</span>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="readout text-sm font-semibold text-foreground">{signal.pair}</span>
            <span className={cn('inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium', solDecisionClass(signal.decision))}>
              <DecisionIcon decision={signal.decision} />
              {signal.decision}
            </span>
            <span className={cn('text-[10px] font-semibold', signal.direction === 'LONG' ? 'text-long' : signal.direction === 'SHORT' ? 'text-short' : 'text-muted-foreground')}>
              {signal.direction}
            </span>
          </div>
          <div className="mt-1 text-xs text-muted-foreground">{solSetupLabel(signal.setup)}</div>
          <LiveQuoteChip compact pair={signal.pair} symbol={signal.symbol} type={signal.assetType} className="mt-1" />
          <div className="mt-2 grid grid-cols-3 gap-2 text-[10px]">
            <div><span className="text-muted-foreground">Entry </span><span className="readout">{solPrice(signal.entry)}</span></div>
            <div><span className="text-muted-foreground">RR </span><span className="readout">{signal.rr?.toFixed(2) ?? '—'}</span></div>
            <div><span className="text-muted-foreground">Venue </span><span className="uppercase">{signal.venue}</span></div>
          </div>
        </div>
      </div>
    </button>
  );
}

export default function SolEnginePanel() {
  const { showToast } = useStore();
  const [health, setHealth] = useState<SolHealth | null>(null);
  const [accounts, setAccounts] = useState<SolAccounts | null>(null);
  const [scan, setScan] = useState<SolScanState | null>(null);
  const [signals, setSignals] = useState<SolSignal[]>([]);
  const [executions, setExecutions] = useState<SolExecutionRecord[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedAssets, setSelectedAssets] = useState<Set<string>>(() => new Set(ASSET_TYPES.map(([id]) => id)));
  const [selectedDecisions, setSelectedDecisions] = useState<Set<SolDecision>>(() => new Set(['READY', 'WATCH']));
  const [mode, setMode] = useState<SolExecutionMode>('paper');
  const [preview, setPreview] = useState<SolPreview | null>(null);
  const [replay, setReplay] = useState<SolReplayResult | null>(null);
  const [confirmSignal, setConfirmSignal] = useState<SolSignal | null>(null);
  const [loading, setLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const mounted = useRef(true);

  const capabilities: SolCapabilities | null = health?.brokerCapabilities ?? accounts?.brokerCapabilities ?? null;
  const selected = useMemo(
    () => signals.find((signal) => signal.signalId === selectedId) ?? signals[0] ?? null,
    [signals, selectedId],
  );
  const progress = solScanProgress(scan);

  const loadSignals = useCallback(async (decisions: Set<SolDecision>) => {
    const query = new URLSearchParams({ decisions: Array.from(decisions).join(','), limit: '300' });
    const response = await apiClient.get<{ signals: SolSignal[] }>(`/api/sol/signals?${query.toString()}`);
    if (!mounted.current) return;
    const rows = Array.isArray(response.signals) ? response.signals : [];
    setSignals(rows);
    setSelectedId((current) => (current && rows.some((row) => row.signalId === current) ? current : rows[0]?.signalId ?? null));
  }, []);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    const [healthResult, accountResult, scanResult, signalResult, executionResult] = await Promise.allSettled([
      apiClient.get<SolHealth>('/api/sol/health'),
      apiClient.get<SolAccounts>('/api/sol/accounts'),
      apiClient.get<SolScanState & { success: boolean }>('/api/sol/scan/current'),
      loadSignals(selectedDecisions),
      apiClient.get<{ executions: SolExecutionRecord[] }>('/api/sol/executions?limit=25'),
    ]);
    if (!mounted.current) return;
    if (healthResult.status === 'fulfilled') {
      setHealth(healthResult.value);
      setMode(solPreferredMode(healthResult.value.brokerCapabilities));
    }
    if (accountResult.status === 'fulfilled') {
      setAccounts(accountResult.value);
      if (healthResult.status !== 'fulfilled') setMode(solPreferredMode(accountResult.value.brokerCapabilities));
    }
    if (scanResult.status === 'fulfilled') setScan(scanResult.value);
    if (executionResult.status === 'fulfilled') setExecutions(executionResult.value.executions || []);
    if (healthResult.status === 'rejected') showToast(`SOL health unavailable: ${errorText(healthResult.reason)}`, 'error');
    if (signalResult.status === 'rejected' && !errorText(signalResult.reason).includes('HTTP 404')) {
      showToast(`SOL signals unavailable: ${errorText(signalResult.reason)}`, 'error');
    }
    setLoading(false);
  }, [loadSignals, selectedDecisions, showToast]);

  useEffect(() => {
    mounted.current = true;
    void refreshAll();
    return () => { mounted.current = false; };
  }, [refreshAll]);

  useEffect(() => {
    if (scan?.status !== 'RUNNING') return undefined;
    const timer = window.setInterval(() => {
      void apiClient.get<SolScanState & { success: boolean }>('/api/sol/scan/current')
        .then((next) => {
          if (!mounted.current) return;
          setScan(next);
          if (next.status === 'COMPLETED') {
            void loadSignals(selectedDecisions);
            showToast(`SOL scan complete: ${next.readyCount} ready, ${next.watchCount} watch`, 'success');
          } else if (next.status === 'FAILED') {
            showToast(`SOL scan failed: ${next.error || 'unknown error'}`, 'error');
          }
        })
        .catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [loadSignals, scan?.status, selectedDecisions, showToast]);

  useEffect(() => {
    setPreview(null);
    setReplay(null);
  }, [selected?.signalId]);

  useEffect(() => {
    void loadSignals(selectedDecisions).catch(() => undefined);
  }, [loadSignals, selectedDecisions]);

  const toggleAsset = (asset: string) => {
    setSelectedAssets((current) => {
      const next = new Set(current);
      if (next.has(asset)) next.delete(asset);
      else next.add(asset);
      return next;
    });
  };

  const toggleDecision = (decision: SolDecision) => {
    setSelectedDecisions((current) => {
      const next = new Set(current);
      if (next.has(decision)) next.delete(decision);
      else next.add(decision);
      return next.size ? next : current;
    });
  };

  const startScan = async () => {
    if (!selectedAssets.size) {
      showToast('Select at least one market group', 'error');
      return;
    }
    setLoading(true);
    try {
      const next = await apiClient.post<SolScanState & { success: boolean }>('/api/sol/scan', {
        assetTypes: Array.from(selectedAssets),
      });
      setScan(next);
      setSignals([]);
      setSelectedId(null);
      showToast(`SOL scan started across ${next.totalPairs} instruments`, 'info');
    } catch (error) {
      showToast(`SOL scan did not start: ${errorText(error)}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  const attestQuote = useCallback(async (signal?: SolSignal | null, { silent = false }: { silent?: boolean } = {}) => {
    const target = signal ?? selected;
    if (!target || !solCanAttest(target.decision)) return;
    setPreviewing(true);
    try {
      const result = await apiClient.post<SolPreview>(`/api/sol/signals/${target.signalId}/preview`, {});
      if (!mounted.current) return;
      setPreview(result);
      if (silent) return;
      if (result.quote) {
        showToast(
          result.executable
            ? 'Live bid/ask attested; demo geometry is current'
            : `Live quote ${solPrice(result.quote.bid)}/${solPrice(result.quote.ask)} · ${result.error || 'not executable'}`,
          result.executable ? 'success' : 'info',
        );
      } else {
        showToast('Broker quote attested; execution geometry is current', 'success');
      }
    } catch (error) {
      if (!mounted.current) return;
      setPreview({ executable: false, error: errorText(error), gates: [] });
      if (!silent) showToast(`Quote attestation rejected: ${errorText(error)}`, 'error');
    } finally {
      if (mounted.current) setPreviewing(false);
    }
  }, [selected, showToast]);

  useEffect(() => {
    if (!selected || !solCanAttest(selected.decision)) return undefined;
    void attestQuote(selected, { silent: true });
    return undefined;
  }, [attestQuote, selected]);

  const confirmExecution = async () => {
    if (!confirmSignal) return;
    setExecuting(true);
    try {
      const response = await apiClient.post<SolExecutionRecord & { success: boolean }>(
        `/api/sol/signals/${confirmSignal.signalId}/execute`,
        {
          mode,
          idempotencyKey: makeIdempotencyKey(confirmSignal.signalId),
          confirmLive: mode === 'live',
        },
      );
      setExecutions((current) => [response, ...current.filter((row) => row.execution_id !== response.execution_id)].slice(0, 25));
      setConfirmSignal(null);
      setPreview(null);
      showToast(mode === 'paper' ? 'SOL paper fill recorded' : `SOL ${mode} order confirmed`, 'success');
    } catch (error) {
      showToast(`SOL execution rejected: ${errorText(error)}`, 'error');
    } finally {
      setExecuting(false);
    }
  };

  const runReplay = async () => {
    if (!selected) return;
    setReplaying(true);
    try {
      const result = await apiClient.post<SolReplayResult & { success: boolean }>('/api/sol/replay', {
        symbol: selected.pair,
        bars: 600,
      });
      setReplay(result);
      showToast(`Causal replay finished with ${result.metrics.tradeCount} trades`, 'info');
    } catch (error) {
      showToast(`SOL replay failed: ${errorText(error)}`, 'error');
    } finally {
      setReplaying(false);
    }
  };

  const readyForMode = Boolean(capabilities?.modes[mode]?.enabled);
  const canExecute = Boolean(selected?.decision === 'READY' && preview?.executable && readyForMode);
  const scoreForAperture = selected?.score ?? (scan ? Math.min(100, scan.readyCount * 8 + scan.watchCount * 2) : 0);

  return (
    <div className="mx-auto flex w-full max-w-[1680px] flex-col gap-4 p-4 lg:p-6">
      <section className="relative overflow-hidden rounded-2xl border border-border/70 bg-card/60 p-4 shadow-sm lg:p-5">
        <div
          className="pointer-events-none absolute -right-16 -top-20 h-72 w-72 rounded-full opacity-20 blur-3xl"
          style={{ background: 'radial-gradient(circle, #F6C453 0%, #FF8A3D 35%, transparent 72%)' }}
        />
        <div className="relative flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex min-w-0 items-center gap-4">
            <div
              className="relative grid h-20 w-20 shrink-0 place-items-center rounded-full"
              style={{
                background: `conic-gradient(#F6C453 ${scoreForAperture * 3.6}deg, #FF8A3D ${Math.min(360, scoreForAperture * 3.6 + 18)}deg, hsl(var(--muted)) 0deg)`,
                boxShadow: '0 0 34px rgba(246, 196, 83, 0.12)',
              }}
            >
              <div className="absolute inset-[6px] rounded-full bg-card" />
              <Orbit className="relative h-8 w-8 text-warning" />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-2xl font-semibold tracking-tight text-foreground">SOL Engine</h1>
                <span className="rounded-full border border-warning/30 bg-warning/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.18em] text-warning">
                  Intraday
                </span>
                <span className="rounded-full border border-border px-2 py-0.5 text-[10px] text-muted-foreground">
                  {health?.contractVersion ?? 'loading'}
                </span>
              </div>
              <p className="mt-1 max-w-3xl text-sm leading-relaxed text-muted-foreground">
                Session-oriented liquidity scanning with robust price orbits, causal sweep and compression events,
                displacement confirmation, and broker-side quote attestation.
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                <span className="inline-flex items-center gap-1.5"><DatabaseZap className="h-3.5 w-3.5" />
                  {accounts?.venues?.mt5?.connected ? `MT5 ${accounts.venues.mt5.environment || 'connected'}` : 'MT5'}
                  {' · '}
                  {accounts?.venues?.bybit?.connected ? `Bybit ${accounts.venues.bybit.environment || 'connected'}` : 'Bybit'}
                </span>
                <span className="inline-flex items-center gap-1.5"><ShieldCheck className="h-3.5 w-3.5" /> Score cannot bypass gates</span>
                <span className="inline-flex items-center gap-1.5"><FlaskConical className="h-3.5 w-3.5" /> {health?.researchStatus ?? 'UNVALIDATED'}</span>
              </div>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => void refreshAll()} disabled={loading}>
              <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} /> Refresh
            </Button>
            <Button size="sm" onClick={() => void startScan()} disabled={loading || scan?.status === 'RUNNING'} className="bg-warning text-black hover:bg-warning/90">
              <ScanSearch className="h-4 w-4" /> {scan?.status === 'RUNNING' ? 'Scanning…' : 'Run SOL scan'}
            </Button>
          </div>
        </div>

        <div className="relative mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
          <Metric label="Ready" value={scan?.readyCount ?? 0} note="all setup gates" />
          <Metric label="Watch" value={scan?.watchCount ?? 0} note="one or more gates" />
          <Metric label="Processed" value={`${scan?.processedPairs ?? 0}/${scan?.totalPairs ?? 0}`} note={scan?.status ?? 'idle'} />
          <Metric label="Score gate" value={selected ? selected.readyThreshold.toFixed(0) : '74'} note="out of 100" />
          <Metric
            label="Execution"
            value={mode.toUpperCase()}
            note={
              capabilities?.globalExecutorMode
                ? `desk ${capabilities.globalExecutorMode}${readyForMode ? '' : ' · disabled'}`
                : readyForMode ? 'available' : 'disabled'
            }
          />
          <Metric label="Latest" value={shortTime(scan?.completedAt || scan?.startedAt)} note={scan?.scanId?.slice(-8) || 'no scan'} />
        </div>
        {scan?.status === 'RUNNING' ? (
          <div className="relative mt-3">
            <div className="mb-1.5 flex items-center justify-between text-[11px] text-muted-foreground">
              <span>Broker candle scan</span><span className="readout">{progress.toFixed(0)}%</span>
            </div>
            <Progress value={progress} className="h-1.5" />
          </div>
        ) : null}
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(390px,0.8fr)]">
        <div className="rounded-2xl border border-border/70 bg-card/50 p-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="label">Market scope</div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {ASSET_TYPES.map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => toggleAsset(id)}
                    aria-pressed={selectedAssets.has(id)}
                    className={cn(
                      'rounded-full border px-2.5 py-1 text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                      selectedAssets.has(id) ? 'border-warning/40 bg-warning/10 text-warning' : 'border-border text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <div className="label">Show</div>
              <div className="mt-2 flex gap-1.5">
                {DECISIONS.map((decision) => (
                  <button
                    key={decision}
                    type="button"
                    onClick={() => toggleDecision(decision)}
                    aria-pressed={selectedDecisions.has(decision)}
                    className={cn(
                      'rounded-full border px-2.5 py-1 text-[10px] font-medium transition-colors',
                      selectedDecisions.has(decision) ? solDecisionClass(decision) : 'border-border text-muted-foreground',
                    )}
                  >
                    {decision}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="mt-4 grid max-h-[720px] gap-2 overflow-y-auto pr-1 lg:grid-cols-2" style={{ contentVisibility: 'auto' }}>
            {signals.length ? signals.map((signal) => (
              <SignalRow
                key={signal.signalId}
                signal={signal}
                selected={selected?.signalId === signal.signalId}
                onSelect={() => setSelectedId(signal.signalId)}
              />
            )) : (
              <div className="col-span-full grid min-h-64 place-items-center rounded-xl border border-dashed border-border px-6 text-center">
                <div>
                  <ScanSearch className="mx-auto h-8 w-8 text-muted-foreground/50" />
                  <div className="mt-3 text-sm font-medium text-foreground">No SOL candidates in this view</div>
                  <p className="mt-1 text-xs text-muted-foreground">Run a scan or include Watch and Blocked diagnostics.</p>
                </div>
              </div>
            )}
          </div>
        </div>

        <aside className="rounded-2xl border border-border/70 bg-card/50 p-4">
          {selected ? (
            <div className="space-y-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="readout text-xl font-semibold text-foreground">{selected.pair}</h2>
                    <span className={cn('rounded-full border px-2 py-0.5 text-[10px] font-medium', solDecisionClass(selected.decision))}>{selected.decision}</span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">{solSetupLabel(selected.setup)} · {selected.direction} · {selected.assetType}</div>
                  <LiveQuoteChip pair={selected.pair} symbol={selected.symbol} type={selected.assetType} showBook className="mt-1" />
                </div>
                <div className="text-right">
                  <div className="readout text-3xl font-semibold text-warning">{selected.score.toFixed(1)}</div>
                  <div className="text-[10px] text-muted-foreground">ready at {selected.readyThreshold.toFixed(0)}</div>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-2">
                <Metric
                  label="Live entry"
                  value={solPrice(preview?.executableEntry ?? selected.entry)}
                  note={preview?.quote ? `${preview.quote.source} ${preview.quote.ageSec.toFixed(1)}s` : 'scan close'}
                />
                <Metric label="Stop" value={solPrice(preview?.liveStop ?? selected.stop)} />
                <Metric
                  label="Target"
                  value={solPrice(preview?.liveTarget ?? selected.target)}
                  note={`RR ${(preview?.liveRr ?? selected.rr)?.toFixed(2) ?? '—'}`}
                />
              </div>
              {selected.sessionClock?.localClock ? (
                <div className="text-[10px] text-muted-foreground">
                  NY {selected.sessionClock.localClock} · {selected.sessionClock.primaryWindow || 'off-session'} · SAST {selected.sessionClock.displayClock}
                </div>
              ) : null}

              <div className="space-y-2 rounded-xl border border-border/70 bg-background/30 p-3">
                <div className="flex items-center justify-between">
                  <span className="label">Score aperture</span>
                  <span className="text-[10px] text-muted-foreground">independent components</span>
                </div>
                {selected.components.map((component) => {
                  const pct = component.maxScore > 0 ? (component.score / component.maxScore) * 100 : 0;
                  return (
                    <div key={component.name}>
                      <div className="mb-1 flex items-center justify-between text-[11px]">
                        <span className="text-muted-foreground">{solComponentLabel(component.name)}</span>
                        <span className="readout">{component.score.toFixed(1)} / {component.maxScore.toFixed(0)}</span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                        <div className="h-full rounded-full bg-warning transition-[width]" style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className="rounded-xl border border-border/70 bg-background/30 p-3">
                <div className="flex items-center justify-between">
                  <span className="label">Deterministic gates</span>
                  <span className="text-[10px] text-muted-foreground">{selected.gates.filter((gate) => gate.passed).length}/{selected.gates.length} passed</span>
                </div>
                <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                  {selected.gates.map((gate) => (
                    <div key={gate.name} className="flex min-w-0 items-center gap-1.5 text-[10px]">
                      {gate.passed ? <CheckCircle2 className="h-3 w-3 shrink-0 text-long" /> : <XCircle className="h-3 w-3 shrink-0 text-short" />}
                      <span className="truncate text-muted-foreground" title={gate.reason || gate.name}>{gate.name.replaceAll('_', ' ')}</span>
                    </div>
                  ))}
                </div>
                {selected.blockingReasons.length ? (
                  <div className="mt-2 rounded-lg border border-warning/25 bg-warning/[0.06] px-2.5 py-2 text-[11px] text-warning">
                    Reason: {selected.blockingReasons[0]}
                  </div>
                ) : null}
              </div>

              <div className="rounded-xl border border-border/70 bg-background/30 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="label">Venue candle proof</span>
                  <span className="text-[10px] text-muted-foreground">closed bars only</span>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  {(['H4', 'H1', 'M15', 'M5'] as const).map((timeframe) => (
                    <ProvenanceCard
                      key={timeframe}
                      timeframe={timeframe}
                      meta={selected.dataProvenance[timeframe] || {}}
                    />
                  ))}
                </div>
              </div>

              <div className="rounded-xl border border-border/70 bg-background/30 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="label">Broker execution</div>
                    <div className="mt-1 text-[10px] text-muted-foreground">Immutable stop and target; current bid/ask must preserve geometry.</div>
                  </div>
                  <Select value={mode} onValueChange={(value) => setMode(value as SolExecutionMode)}>
                    <SelectTrigger size="sm" className="w-28"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {(['paper', 'demo', 'live'] as SolExecutionMode[]).map((item) => (
                        <SelectItem key={item} value={item} disabled={!capabilities?.modes[item]?.enabled}>
                          {item.toUpperCase()}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {!readyForMode ? (
                  <div className="mt-2 flex items-start gap-2 rounded-lg border border-warning/25 bg-warning/[0.06] px-2.5 py-2 text-[11px] text-warning">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    {mode.toUpperCase()} is disabled by the server execution and validation controls.
                  </div>
                ) : null}
                {preview ? (
                  <div className={cn('mt-2 rounded-lg border px-2.5 py-2 text-[11px]', preview.executable ? 'border-long/25 bg-long/[0.06] text-long' : 'border-short/25 bg-short/[0.06] text-short')}>
                    <div className="flex items-center gap-1.5 font-medium">
                      {preview.executable ? <ShieldCheck className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
                      {preview.executable ? 'Quote attested' : `Rejected: ${preview.error || 'unknown gate'}`}
                    </div>
                    {preview.quote ? (
                      <div className="mt-1 text-muted-foreground">
                        bid {solPrice(preview.quote.bid)} · ask {solPrice(preview.quote.ask)} · spread {preview.quote.spreadBps.toFixed(2)} bps · age {preview.quote.ageSec.toFixed(1)}s
                      </div>
                    ) : null}
                  </div>
                ) : null}
                <div className="mt-3 flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => void attestQuote(selected)} disabled={previewing || !solCanAttest(selected.decision)} className="flex-1">
                    <Crosshair className={cn('h-4 w-4', previewing && 'animate-pulse')} /> Attest live quote
                  </Button>
                  <Button size="sm" onClick={() => setConfirmSignal(selected)} disabled={!canExecute || executing} className="flex-1 bg-warning text-black hover:bg-warning/90">
                    <Play className="h-4 w-4" /> Execute {mode}
                  </Button>
                </div>
              </div>

              <div className="rounded-xl border border-border/70 bg-background/30 p-3">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <div className="label">Causal replay</div>
                    <div className="mt-1 text-[10px] text-muted-foreground">600 M5 bars · closed-prefix evaluation · stop-first ambiguity</div>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => void runReplay()} disabled={replaying}>
                    <FlaskConical className={cn('h-4 w-4', replaying && 'animate-pulse')} /> Replay
                  </Button>
                </div>
                {replay ? (
                  <div className="mt-3 grid grid-cols-4 gap-2 text-center">
                    <Metric label="Trades" value={replay.metrics.tradeCount} />
                    <Metric label="Win rate" value={replay.metrics.winRatePct == null ? '—' : `${replay.metrics.winRatePct.toFixed(1)}%`} />
                    <Metric label="Avg R" value={replay.metrics.averageR?.toFixed(2) ?? '—'} />
                    <Metric label="Max DD" value={`${replay.metrics.maxDrawdownR.toFixed(2)}R`} note={replay.evidenceStatus.replaceAll('_', ' ')} />
                  </div>
                ) : null}
              </div>

              <div className="text-[10px] text-muted-foreground">
                Context {selected.timeframes.context} · value {selected.timeframes.value} · setup {selected.timeframes.setup} · trigger {selected.timeframes.trigger} · event {shortTime(selected.setupEventAt)} · trigger closed {shortTime(selected.barClosedAt)}
              </div>
            </div>
          ) : (
            <div className="grid min-h-[560px] place-items-center text-center">
              <div>
                <Gauge className="mx-auto h-10 w-10 text-muted-foreground/40" />
                <div className="mt-3 text-sm font-medium">Select a SOL candidate</div>
                <div className="mt-1 text-xs text-muted-foreground">Its score, gates, quote, and execution controls will appear here.</div>
              </div>
            </div>
          )}
        </aside>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-border/70 bg-card/50 p-4">
          <div className="flex items-center gap-2"><Activity className="h-4 w-4 text-warning" /><span className="label">Scan diagnostics</span></div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {(scan?.topBlockingReasons || []).slice(0, 8).map((row) => (
              <div key={row.reason} className="flex items-center justify-between rounded-lg border border-border/60 px-3 py-2 text-xs">
                <span className="truncate text-muted-foreground">{row.reason.replaceAll('_', ' ')}</span>
                <span className="readout ml-2 text-warning">{row.count}</span>
              </div>
            ))}
            {!scan?.topBlockingReasons?.length ? <div className="text-xs text-muted-foreground">Run a scan to populate the causal gate funnel.</div> : null}
          </div>
        </div>
        <div className="rounded-2xl border border-border/70 bg-card/50 p-4">
          <div className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-warning" /><span className="label">Recent SOL executions</span></div>
          <div className="mt-3 space-y-2">
            {executions.slice(0, 6).map((row) => (
              <div key={row.execution_id} className="flex items-center justify-between gap-3 rounded-lg border border-border/60 px-3 py-2 text-xs">
                <div className="min-w-0">
                  <div className="truncate font-medium text-foreground">{row.signal_id}</div>
                  <div className="mt-0.5 text-[10px] text-muted-foreground">{row.mode.toUpperCase()} · {row.venue.toUpperCase()} · {shortTime(row.requested_at)}</div>
                </div>
                <span className={cn('rounded-full border px-2 py-0.5 text-[10px]', row.status === 'SUCCESS' ? 'border-long/30 text-long' : row.status === 'PENDING' ? 'border-warning/30 text-warning' : 'border-short/30 text-short')}>
                  {row.status}
                </span>
              </div>
            ))}
            {!executions.length ? <div className="text-xs text-muted-foreground">No SOL executions recorded.</div> : null}
          </div>
        </div>
      </section>

      <AlertDialog open={!!confirmSignal} onOpenChange={(open) => { if (!open && !executing) setConfirmSignal(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm SOL {mode} execution</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3 text-sm">
                <p>
                  {mode === 'paper'
                    ? 'This records a simulated fill only. No broker order will be placed.'
                    : mode === 'demo'
                      ? 'This sends an order to the venue-attested demo account.'
                      : 'This sends a real broker order. Server-side live, account, validation, risk, and confirmation gates must all pass.'}
                </p>
                {confirmSignal ? (
                  <div className="grid grid-cols-2 gap-2 rounded-lg border border-border p-3 text-xs">
                    <div><span className="text-muted-foreground">Instrument</span><div className="readout mt-1">{confirmSignal.pair}</div></div>
                    <div><span className="text-muted-foreground">Direction</span><div className="readout mt-1">{confirmSignal.direction}</div></div>
                    <div><span className="text-muted-foreground">Stop</span><div className="readout mt-1">{solPrice(confirmSignal.stop)}</div></div>
                    <div><span className="text-muted-foreground">Target</span><div className="readout mt-1">{solPrice(confirmSignal.target)}</div></div>
                  </div>
                ) : null}
                <p className="text-xs text-muted-foreground">The backend reloads the persisted signal, rechecks the quote, and reserves an idempotency key before any broker call.</p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={executing}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void confirmExecution()} disabled={executing} className={mode === 'live' ? 'bg-destructive text-white hover:bg-destructive/90' : ''}>
              {executing ? 'Checking…' : mode === 'paper' ? 'Record paper fill' : `Send ${mode} order`}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
