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
  Play,
  RefreshCw,
  ScanSearch,
  Search,
  ShieldCheck,
  Sparkles,
  Timer,
  Wallet,
  XCircle,
} from 'lucide-react';

import { useStore } from '@/hooks/useStore';
import apiClient from '@/lib/apiClient';
import { cn } from '@/lib/utils';
import { LiveQuoteChip } from '@/components/shared';
import {
  grokClockLabel,
  grokComponentLabel,
  grokDecisionClass,
  grokDisplayZoneLabel,
  grokNarrativeSteps,
  grokPreviewNotice,
  grokPrice,
  grokScanProgress,
  grokSignalMatchesQuery,
  grokWindowSchedule,
  grokSetupLabel,
  type GrokAccounts,
  type GrokCapabilities,
  type GrokDecision,
  type GrokExecutionMode,
  type GrokExecutionRecord,
  type GrokHealth,
  type GrokNarrative,
  type GrokPreview,
  type GrokReplayResult,
  type GrokScanState,
  type GrokSignal,
} from '@/lib/grokEngine';
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

const DECISIONS: GrokDecision[] = ['READY', 'WATCH', 'BLOCKED'];
const NARRATIVE = grokNarrativeSteps();

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
  return `grok-ui:${signalId}:${random}`;
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

function DecisionIcon({ decision }: { decision: GrokDecision }) {
  if (decision === 'READY') return <CheckCircle2 className="h-3.5 w-3.5" />;
  if (decision === 'WATCH') return <Clock3 className="h-3.5 w-3.5" />;
  return <XCircle className="h-3.5 w-3.5" />;
}

function NarrativeRail({ current }: { current?: GrokNarrative }) {
  const active = current || 'RANGE_BUILD';
  const activeIndex = Math.max(0, NARRATIVE.indexOf(active));
  return (
    <div className="grid grid-cols-5 gap-1">
      {NARRATIVE.map((step, index) => {
        const on = index <= activeIndex;
        const here = step === active;
        return (
          <div
            key={step}
            className={cn(
              'rounded-lg border px-1.5 py-1.5 text-center text-[9px] font-medium uppercase tracking-[0.12em]',
              here
                ? 'border-cyan-400/50 bg-cyan-400/10 text-cyan-200'
                : on
                  ? 'border-amber-400/30 bg-amber-400/10 text-amber-100'
                  : 'border-border/60 text-muted-foreground',
            )}
          >
            {step.replaceAll('_', ' ')}
          </div>
        );
      })}
    </div>
  );
}

function KillzoneSchedule({ clock }: { clock: Record<string, unknown> | undefined }) {
  const rows = grokWindowSchedule(clock);
  if (!rows.length) return null;
  const zone = grokDisplayZoneLabel(clock);
  const active = typeof clock?.primaryWindow === 'string' ? clock.primaryWindow : '';
  return (
    <div className="relative mt-4 grid grid-cols-2 gap-1.5 lg:grid-cols-4">
      {rows.map((row) => {
        const here = row.name === active;
        return (
          <div
            key={row.name}
            className={cn(
              'rounded-lg border px-2 py-1.5',
              here ? 'border-cyan-400/40 bg-cyan-400/10' : 'border-border/60 bg-background/30',
            )}
          >
            <div className="truncate text-[9px] font-medium uppercase tracking-[0.12em] text-foreground">
              {row.name.replaceAll('_', ' ')}
            </div>
            <div className="mt-0.5 text-[11px] text-cyan-200">
              {row.displayStart}–{row.displayEnd} {zone}
            </div>
            <div className="text-[9px] text-muted-foreground">
              {row.nyStart}–{row.nyEnd} NY · {row.kind.replaceAll('_', ' ')}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function KillzoneDial({ score, windowLabel }: { score: number; windowLabel: string }) {
  const angle = Math.max(0, Math.min(100, score)) * 3.6;
  return (
    <div className="relative grid h-[5.5rem] w-[5.5rem] place-items-center">
      <div
        className="absolute inset-0 rounded-full"
        style={{
          background: `conic-gradient(#67e8f9 ${angle}deg, #fbbf24 ${Math.min(360, angle + 16)}deg, hsl(var(--muted)) 0deg)`,
          boxShadow: '0 0 28px rgba(103, 232, 249, 0.16)',
        }}
      />
      <div className="absolute inset-[7px] rounded-full bg-card" />
      <Timer className="relative h-7 w-7 text-cyan-300" />
      <div className="pointer-events-none absolute -bottom-5 w-28 truncate text-center text-[9px] uppercase tracking-[0.14em] text-cyan-200/80">
        {windowLabel}
      </div>
    </div>
  );
}

function ProvenanceCard({ timeframe, meta }: { timeframe: string; meta: Record<string, unknown> }) {
  const provider = typeof meta.provider === 'string' ? meta.provider : 'unknown';
  const bars = typeof meta.bars === 'number' && Number.isFinite(meta.bars) ? meta.bars : 0;
  const formingDropped = typeof meta.formingBarsDropped === 'number' && Number.isFinite(meta.formingBarsDropped)
    ? meta.formingBarsDropped
    : 0;
  const lastClosedAt = typeof meta.lastClosedAt === 'string' ? meta.lastClosedAt : null;
  return (
    <div className="surface-inset min-w-0 px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="readout text-xs font-semibold text-foreground">{timeframe}</span>
        <span className="truncate text-[9px] uppercase tracking-wide text-cyan-300" title={provider}>{provider}</span>
      </div>
      <div className="mt-1 text-[10px] text-muted-foreground">{bars} closed · {formingDropped} forming dropped</div>
      <div className="mt-0.5 truncate text-[9px] text-muted-foreground" title={lastClosedAt || undefined}>
        close {shortTime(lastClosedAt)}
      </div>
    </div>
  );
}

function SignalRow({ signal, selected, onSelect }: { signal: GrokSignal; selected: boolean; onSelect: () => void }) {
  const scoreAngle = `${Math.max(0, Math.min(100, signal.score)) * 3.6}deg`;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        'group w-full rounded-xl border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        selected ? 'border-cyan-400/40 bg-cyan-400/[0.06]' : 'border-border/70 bg-card/50 hover:border-border hover:bg-card/80',
      )}
    >
      <div className="flex items-start gap-3">
        <div
          className="relative grid h-12 w-12 shrink-0 place-items-center rounded-full"
          style={{ background: `conic-gradient(#67e8f9 ${scoreAngle}, hsl(var(--muted)) 0deg)` }}
          aria-label={`Score ${signal.score.toFixed(1)} out of 100`}
        >
          <div className="absolute inset-[4px] rounded-full bg-card" />
          <span className="readout relative text-xs font-semibold">{signal.score.toFixed(0)}</span>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="readout text-sm font-semibold text-foreground">{signal.pair}</span>
            <span className={cn('inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium', grokDecisionClass(signal.decision))}>
              <DecisionIcon decision={signal.decision} />
              {signal.decision}
            </span>
            <span className={cn('text-[10px] font-semibold', signal.direction === 'LONG' ? 'text-long' : signal.direction === 'SHORT' ? 'text-short' : 'text-muted-foreground')}>
              {signal.direction}
            </span>
          </div>
          <div className="mt-1 text-xs text-muted-foreground">{grokSetupLabel(signal.setup)} · {signal.narrative.replaceAll('_', ' ')}</div>
          <LiveQuoteChip compact pair={signal.pair} symbol={signal.symbol} type={signal.assetType} className="mt-1" />
          <div className="mt-2 grid grid-cols-3 gap-2 text-[10px]">
            <div><span className="text-muted-foreground">Entry </span><span className="readout">{grokPrice(signal.entry)}</span></div>
            <div><span className="text-muted-foreground">RR </span><span className="readout">{signal.rr?.toFixed(2) ?? '—'}</span></div>
            <div><span className="text-muted-foreground">Venue </span><span className="uppercase">{signal.venue}</span></div>
          </div>
        </div>
      </div>
    </button>
  );
}

export default function GrokEnginePanel() {
  const { showToast } = useStore();
  const [health, setHealth] = useState<GrokHealth | null>(null);
  const [accounts, setAccounts] = useState<GrokAccounts | null>(null);
  const [scan, setScan] = useState<GrokScanState | null>(null);
  const [signals, setSignals] = useState<GrokSignal[]>([]);
  const [executions, setExecutions] = useState<GrokExecutionRecord[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedAssets, setSelectedAssets] = useState<Set<string>>(() => new Set(ASSET_TYPES.map(([id]) => id)));
  const [selectedDecisions, setSelectedDecisions] = useState<Set<GrokDecision>>(() => new Set(['READY', 'WATCH']));
  const [pairQuery, setPairQuery] = useState('');
  const [mode, setMode] = useState<GrokExecutionMode>('paper');
  const [preview, setPreview] = useState<GrokPreview | null>(null);
  const [replay, setReplay] = useState<GrokReplayResult | null>(null);
  const [confirmSignal, setConfirmSignal] = useState<GrokSignal | null>(null);
  const [loading, setLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const mounted = useRef(true);

  const capabilities: GrokCapabilities | null = health?.brokerCapabilities ?? accounts?.brokerCapabilities ?? null;
  const visibleSignals = useMemo(
    () => (pairQuery.trim() ? signals.filter((row) => grokSignalMatchesQuery(row, pairQuery)) : signals),
    [pairQuery, signals],
  );
  const selected = useMemo(
    () => visibleSignals.find((signal) => signal.signalId === selectedId) ?? visibleSignals[0] ?? null,
    [visibleSignals, selectedId],
  );
  const progress = grokScanProgress(scan);
  const clock = selected?.sessionClock;

  const pairQueryRef = useRef(pairQuery);
  pairQueryRef.current = pairQuery;

  const loadSignals = useCallback(async (decisions: Set<GrokDecision>, queryText?: string) => {
    const text = queryText ?? pairQueryRef.current;
    const searching = Boolean(text.trim());
    const decisionSet = searching ? new Set<GrokDecision>(['READY', 'WATCH', 'BLOCKED']) : decisions;
    const query = new URLSearchParams({ decisions: Array.from(decisionSet).join(','), limit: '300' });
    const response = await apiClient.get<{ signals: GrokSignal[] }>(`/api/grok/signals?${query.toString()}`);
    if (!mounted.current) return;
    const rows = Array.isArray(response.signals) ? response.signals : [];
    setSignals(rows);
    const visible = searching ? rows.filter((row) => grokSignalMatchesQuery(row, text)) : rows;
    setSelectedId((current) => (current && visible.some((row) => row.signalId === current) ? current : visible[0]?.signalId ?? null));
  }, []);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    const [healthResult, accountResult, scanResult, signalResult, executionResult] = await Promise.allSettled([
      apiClient.get<GrokHealth>('/api/grok/health'),
      apiClient.get<GrokAccounts>('/api/grok/accounts'),
      apiClient.get<GrokScanState & { success: boolean }>('/api/grok/scan/current'),
      loadSignals(selectedDecisions),
      apiClient.get<{ executions: GrokExecutionRecord[] }>('/api/grok/executions?limit=25'),
    ]);
    if (!mounted.current) return;
    if (healthResult.status === 'fulfilled') {
      setHealth(healthResult.value);
      const defaultMode = healthResult.value.brokerCapabilities.defaultMode;
      setMode((current) => {
        if (healthResult.value.brokerCapabilities.modes[current]?.enabled) return current;
        const order: GrokExecutionMode[] = [defaultMode, 'paper', 'demo', 'live'];
        return order.find((candidate) => healthResult.value.brokerCapabilities.modes[candidate]?.enabled) ?? 'paper';
      });
    }
    if (accountResult.status === 'fulfilled') setAccounts(accountResult.value);
    if (scanResult.status === 'fulfilled') setScan(scanResult.value);
    if (executionResult.status === 'fulfilled') setExecutions(executionResult.value.executions || []);
    if (healthResult.status === 'rejected') showToast(`GROK health unavailable: ${errorText(healthResult.reason)}`, 'error');
    if (signalResult.status === 'rejected' && !errorText(signalResult.reason).includes('HTTP 404')) {
      showToast(`GROK signals unavailable: ${errorText(signalResult.reason)}`, 'error');
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
      void apiClient.get<GrokScanState & { success: boolean }>('/api/grok/scan/current')
        .then((next) => {
          if (!mounted.current) return;
          setScan(next);
          if (next.status === 'COMPLETED') {
            void loadSignals(selectedDecisions);
            showToast(`GROK scan complete: ${next.readyCount} ready, ${next.watchCount} watch`, 'success');
          } else if (next.status === 'FAILED') {
            showToast(`GROK scan failed: ${next.error || 'unknown error'}`, 'error');
          }
        })
        .catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [loadSignals, scan?.status, selectedDecisions, showToast]);

  useEffect(() => {
    setPreview(null);
    setReplay(null);
  }, [selected?.signalId, mode]);

  useEffect(() => {
    void loadSignals(selectedDecisions, pairQuery).catch(() => undefined);
  }, [loadSignals, selectedDecisions, pairQuery]);

  const toggleAsset = (asset: string) => {
    setSelectedAssets((current) => {
      const next = new Set(current);
      if (next.has(asset)) next.delete(asset);
      else next.add(asset);
      return next;
    });
  };

  const toggleDecision = (decision: GrokDecision) => {
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
      const needle = pairQuery.trim();
      const next = await apiClient.post<GrokScanState & { success: boolean }>('/api/grok/scan', {
        assetTypes: Array.from(selectedAssets),
        ...(needle ? { symbols: [needle] } : {}),
      });
      setScan(next);
      setSignals([]);
      setSelectedId(null);
      showToast(
        needle
          ? `GROK scan started for ${needle} (${next.totalPairs} instruments)`
          : `GROK scan started across ${next.totalPairs} live instruments`,
        'info',
      );
    } catch (error) {
      showToast(`GROK scan did not start: ${errorText(error)}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  const attestQuote = async () => {
    if (!selected) return;
    setPreviewing(true);
    try {
      const result = await apiClient.post<GrokPreview>(`/api/grok/signals/${selected.signalId}/preview`, {});
      setPreview(result);
      const notice = grokPreviewNotice(result);
      showToast(notice.message, notice.tone);
    } catch (error) {
      setPreview({ executable: false, error: errorText(error), gates: [] });
      showToast(`Quote attestation rejected: ${errorText(error)}`, 'error');
    } finally {
      setPreviewing(false);
    }
  };

  const confirmExecution = async () => {
    if (!confirmSignal) return;
    setExecuting(true);
    try {
      const response = await apiClient.post<GrokExecutionRecord & { success: boolean }>(
        `/api/grok/signals/${confirmSignal.signalId}/execute`,
        {
          mode,
          idempotencyKey: makeIdempotencyKey(confirmSignal.signalId),
          confirmLive: mode === 'live',
        },
      );
      setExecutions((current) => [response, ...current.filter((row) => row.execution_id !== response.execution_id)].slice(0, 25));
      setConfirmSignal(null);
      setPreview(null);
      showToast(mode === 'paper' ? 'GROK paper fill recorded' : `GROK ${mode} order confirmed`, 'success');
    } catch (error) {
      showToast(`GROK execution rejected: ${errorText(error)}`, 'error');
    } finally {
      setExecuting(false);
    }
  };

  const runReplay = async () => {
    if (!selected) return;
    setReplaying(true);
    try {
      const result = await apiClient.post<GrokReplayResult & { success: boolean }>('/api/grok/replay', {
        symbol: selected.pair,
        bars: 600,
      });
      setReplay(result);
      showToast(`Causal replay finished with ${result.metrics.tradeCount} trades`, 'info');
    } catch (error) {
      showToast(`GROK replay failed: ${errorText(error)}`, 'error');
    } finally {
      setReplaying(false);
    }
  };

  const readyForMode = Boolean(capabilities?.modes[mode]?.enabled);
  const canExecute = Boolean(selected?.decision === 'READY' && preview?.executable && readyForMode);
  const scoreForDial = selected?.score ?? (scan ? Math.min(100, scan.readyCount * 8 + scan.watchCount * 2) : 0);
  const universeCount = health?.universe?.activePairs ?? accounts?.universe.activePairs ?? 0;
  const mt5 = accounts?.venues.mt5;
  const bybit = accounts?.venues.bybit;

  return (
    <div className="mx-auto flex w-full max-w-[1680px] flex-col gap-4 p-4 lg:p-6">
      <section className="relative overflow-hidden rounded-2xl border border-cyan-400/15 bg-card/60 p-4 shadow-sm lg:p-5">
        <div
          className="pointer-events-none absolute -right-10 -top-24 h-80 w-80 rounded-full opacity-30 blur-3xl"
          style={{ background: 'radial-gradient(circle, #67e8f9 0%, #fbbf24 28%, transparent 70%)' }}
        />
        <div className="relative flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex min-w-0 items-center gap-4">
            <KillzoneDial score={scoreForDial} windowLabel={selected ? grokClockLabel(clock) : 'session clock'} />
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-2xl font-semibold tracking-tight text-foreground">GROK Engine</h1>
                <span className="rounded-full border border-cyan-400/30 bg-cyan-400/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.18em] text-cyan-200">
                  KDD
                </span>
                <span className="rounded-full border border-border px-2 py-0.5 text-[10px] text-muted-foreground">
                  {health?.contractVersion ?? 'loading'}
                </span>
              </div>
              <p className="mt-1 max-w-3xl text-sm leading-relaxed text-muted-foreground">
                Killzone Displacement Delivery — time-first ICT: session raid, displacement, open void, CISD,
                then broker-attested execution on the live instrument book. Killzones score in New York time and
                are labeled in SAST (Africa/Johannesburg).
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                <span className="inline-flex items-center gap-1.5"><DatabaseZap className="h-3.5 w-3.5" /> {universeCount} live pairs · MT5 + Bybit</span>
                <span className="inline-flex items-center gap-1.5"><ShieldCheck className="h-3.5 w-3.5" /> Score cannot bypass gates</span>
                <span className="inline-flex items-center gap-1.5"><FlaskConical className="h-3.5 w-3.5" /> {health?.researchStatus ?? 'UNVALIDATED'}</span>
              </div>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => void refreshAll()} disabled={loading}>
              <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} /> Refresh
            </Button>
            <Button size="sm" onClick={() => void startScan()} disabled={loading || scan?.status === 'RUNNING'} className="bg-cyan-300 text-black hover:bg-cyan-200">
              <ScanSearch className="h-4 w-4" /> {scan?.status === 'RUNNING' ? 'Scanning…' : 'Run GROK scan'}
            </Button>
          </div>
        </div>

        <KillzoneSchedule clock={clock} />

        <div className="relative mt-5 grid grid-cols-1 gap-2 md:grid-cols-2">
          <div className="rounded-xl border border-border/70 bg-background/30 p-3">
            <div className="mb-2 flex items-center gap-2 text-[11px] text-muted-foreground"><Wallet className="h-3.5 w-3.5" /> Linked accounts</div>
            <div className="grid grid-cols-2 gap-2 text-[11px]">
              <div className="surface-inset px-2.5 py-2">
                <div className="label">MT5</div>
                <div className="mt-1 font-medium text-foreground">{mt5?.connected ? `${mt5.environment || 'connected'} · ${mt5.server || mt5.login || 'account'}` : mt5?.error || 'not connected'}</div>
                <div className="mt-0.5 text-muted-foreground">{mt5?.connected ? `${mt5.equity ?? '—'} ${mt5.currency || ''}` : 'broker unread'}</div>
              </div>
              <div className="surface-inset px-2.5 py-2">
                <div className="label">Bybit</div>
                <div className="mt-1 font-medium text-foreground">{bybit?.connected ? `${bybit.demo ? 'demo' : bybit.testnet ? 'testnet' : 'live'} · ${bybit.server || 'USDT'}` : bybit?.error || 'not connected'}</div>
                <div className="mt-0.5 text-muted-foreground">{bybit?.connected ? `${bybit.equity ?? '—'} ${bybit.currency || ''}` : 'venue unread'}</div>
              </div>
            </div>
          </div>
          <div className="rounded-xl border border-border/70 bg-background/30 p-3">
            <div className="mb-2 text-[11px] text-muted-foreground">Delivery narrative</div>
            <NarrativeRail current={selected?.narrative} />
          </div>
        </div>

        <div className="relative mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
          <Metric label="Ready" value={scan?.readyCount ?? 0} note="all setup gates" />
          <Metric label="Watch" value={scan?.watchCount ?? 0} note="one or more gates" />
          <Metric label="Processed" value={`${scan?.processedPairs ?? 0}/${scan?.totalPairs ?? universeCount}`} note={scan?.status ?? 'idle'} />
          <Metric label="Score gate" value={selected ? selected.readyThreshold.toFixed(0) : '76'} note="out of 100" />
          <Metric label="Execution" value={mode.toUpperCase()} note={readyForMode ? 'available' : 'disabled'} />
          <Metric label="Latest" value={shortTime(scan?.completedAt || scan?.startedAt)} note={scan?.scanId?.slice(-8) || 'no scan'} />
        </div>
        {scan?.status === 'RUNNING' ? (
          <div className="relative mt-3">
            <div className="mb-1.5 flex items-center justify-between text-[11px] text-muted-foreground">
              <span>Live-book candle scan</span><span className="readout">{progress.toFixed(0)}%</span>
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
                      selectedAssets.has(id) ? 'border-cyan-400/40 bg-cyan-400/10 text-cyan-200' : 'border-border text-muted-foreground hover:text-foreground',
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
                      selectedDecisions.has(decision) ? grokDecisionClass(decision) : 'border-border text-muted-foreground',
                    )}
                  >
                    {decision}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <label className="relative mt-3 block">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              value={pairQuery}
              onChange={(event) => setPairQuery(event.target.value)}
              placeholder="Find XAU/USD — loads Blocked rows and scopes the next scan"
              className="h-9 w-full rounded-lg border border-border bg-background/60 pl-8 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </label>

          <div className="mt-4 grid max-h-[720px] gap-2 overflow-y-auto pr-1 lg:grid-cols-2" style={{ contentVisibility: 'auto' }}>
            {visibleSignals.length ? visibleSignals.map((signal) => (
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
                  <div className="mt-3 text-sm font-medium text-foreground">
                    {pairQuery.trim() ? `No GROK row matching ${pairQuery.trim()}` : 'No GROK candidates in this view'}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {pairQuery.trim()
                      ? 'Run a scan while this search is set to score that instrument, or include Blocked diagnostics.'
                      : 'Run a scan on the live pair book or include Watch and Blocked diagnostics.'}
                  </p>
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
                    <span className={cn('rounded-full border px-2 py-0.5 text-[10px] font-medium', grokDecisionClass(selected.decision))}>{selected.decision}</span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">{grokSetupLabel(selected.setup)} · {selected.direction} · {selected.assetType}</div>
                  <LiveQuoteChip pair={selected.pair} symbol={selected.symbol} type={selected.assetType} showBook className="mt-1" />
                </div>
                <div className="text-right">
                  <div className="readout text-3xl font-semibold text-cyan-300">{selected.score.toFixed(1)}</div>
                  <div className="text-[10px] text-muted-foreground">ready at {selected.readyThreshold.toFixed(0)}</div>
                </div>
              </div>

              <NarrativeRail current={selected.narrative} />

              <div className="grid grid-cols-3 gap-2">
                <Metric label="Entry" value={grokPrice(selected.entry)} />
                <Metric label="Stop" value={grokPrice(selected.stop)} />
                <Metric label="Target" value={grokPrice(selected.target)} note={`RR ${selected.rr?.toFixed(2) ?? '—'}`} />
              </div>

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
                        <span className="text-muted-foreground">{grokComponentLabel(component.name)}</span>
                        <span className="readout">{component.score.toFixed(1)} / {component.maxScore.toFixed(0)}</span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                        <div className="h-full rounded-full bg-cyan-300 transition-[width]" style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
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
                  {(['D1', 'H1', 'M15', 'M5'] as const).map((timeframe) => (
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
                  <Select value={mode} onValueChange={(value) => setMode(value as GrokExecutionMode)}>
                    <SelectTrigger size="sm" className="w-28"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {(['paper', 'demo', 'live'] as GrokExecutionMode[]).map((item) => (
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
                        bid {grokPrice(preview.quote.bid)} · ask {grokPrice(preview.quote.ask)} · spread {preview.quote.spreadBps.toFixed(2)} bps · age {preview.quote.ageSec.toFixed(1)}s
                        {preview.gates?.some((gate) => gate.name === 'quote_drift' && gate.passed === false)
                          ? ` · drift ${Number(preview.gates.find((gate) => gate.name === 'quote_drift')?.driftAtr ?? 0).toFixed(2)} ATR`
                          : ''}
                        {preview.liveRr != null ? ` · live R:R ${preview.liveRr.toFixed(2)}` : ''}
                        {preview.gates?.some((gate) => gate.name === 'live_geometry' && gate.rebasedTarget === true)
                          ? ` · live TP ${grokPrice(preview.liveTarget)}`
                          : ''}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                <div className="mt-3 flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => void attestQuote()} disabled={previewing || selected.decision !== 'READY'} className="flex-1">
                    <Crosshair className={cn('h-4 w-4', previewing && 'animate-pulse')} /> Attest quote
                  </Button>
                  <Button size="sm" onClick={() => setConfirmSignal(selected)} disabled={!canExecute || executing} className="flex-1 bg-cyan-300 text-black hover:bg-cyan-200">
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
                Bias {selected.timeframes.bias} · session {selected.timeframes.session} · setup {selected.timeframes.setup} · trigger {selected.timeframes.trigger} · {grokClockLabel(selected.sessionClock)} · trigger closed {shortTime(selected.barClosedAt)}
              </div>
            </div>
          ) : (
            <div className="grid min-h-[560px] place-items-center text-center">
              <div>
                <Gauge className="mx-auto h-10 w-10 text-muted-foreground/40" />
                <div className="mt-3 text-sm font-medium">Select a GROK candidate</div>
                <div className="mt-1 text-xs text-muted-foreground">Its killzone, raid, void, quote, and execution controls will appear here.</div>
              </div>
            </div>
          )}
        </aside>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-border/70 bg-card/50 p-4">
          <div className="flex items-center gap-2"><Activity className="h-4 w-4 text-cyan-300" /><span className="label">Scan diagnostics</span></div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {(scan?.topBlockingReasons || []).slice(0, 8).map((row) => (
              <div key={row.reason} className="flex items-center justify-between rounded-lg border border-border/60 px-3 py-2 text-xs">
                <span className="truncate text-muted-foreground">{row.reason.replaceAll('_', ' ')}</span>
                <span className="readout ml-2 text-cyan-300">{row.count}</span>
              </div>
            ))}
            {!scan?.topBlockingReasons?.length ? <div className="text-xs text-muted-foreground">Run a scan to populate the killzone gate funnel.</div> : null}
          </div>
        </div>
        <div className="rounded-2xl border border-border/70 bg-card/50 p-4">
          <div className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-amber-300" /><span className="label">Recent GROK executions</span></div>
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
            {!executions.length ? <div className="text-xs text-muted-foreground">No GROK executions recorded.</div> : null}
          </div>
        </div>
      </section>

      <AlertDialog open={!!confirmSignal} onOpenChange={(open) => { if (!open && !executing) setConfirmSignal(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm GROK {mode} execution</AlertDialogTitle>
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
                    <div><span className="text-muted-foreground">Stop</span><div className="readout mt-1">{grokPrice(confirmSignal.stop)}</div></div>
                    <div><span className="text-muted-foreground">Target</span><div className="readout mt-1">{grokPrice(confirmSignal.target)}</div></div>
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
