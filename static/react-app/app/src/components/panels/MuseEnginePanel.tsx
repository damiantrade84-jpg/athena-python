import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Anchor,
  Compass,
  Crosshair,
  MoonStar,
  Play,
  Radar,
  RefreshCw,
  ScanSearch,
  Search,
  Shell,
  ShieldCheck,
  Waves,
  AlertTriangle,
  CheckCircle2,
  XCircle,
} from 'lucide-react';

import apiClient from '@/lib/apiClient';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import {
  museDecisionClass,
  musePhaseIndex,
  musePhaseSteps,
  musePrismBlurb,
  musePrismLabel,
  musePrice,
  museScoreText,
  museSetupLabel,
  museSignalMatchesQuery,
  museTideLabel,
  museWeakestPrism,
  type MuseDecision,
  type MuseHealth,
  type MusePreview,
  type MuseScanState,
  type MuseSignal,
} from '@/lib/museEngine';

const DECISIONS: MuseDecision[] = ['PRIME', 'STAGE', 'DORMANT', 'BLOCKED'];
const PHASES = musePhaseSteps();

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error || 'Unknown error');
}

function makeIdempotencyKey(signalId: string): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `muse-ui:${signalId}:${random}`;
}

function PrismBar({ name, quality }: { name: string; quality: number }) {
  const pct = Math.max(0, Math.min(1, quality));
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-medium text-cyan-100">{musePrismLabel(name)}</span>
        <span className="tabular-nums text-cyan-200/80">{(pct * 100).toFixed(1)}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-cyan-950">
        <div
          className={cn('h-full rounded-full transition-all', pct >= 0.6 ? 'bg-cyan-300' : pct >= 0.35 ? 'bg-teal-400' : 'bg-rose-400')}
          style={{ width: `${(pct * 100).toFixed(1)}%` }}
        />
      </div>
      <p className="text-[11px] text-slate-400">{musePrismBlurb(name)}</p>
    </div>
  );
}

export default function MuseEnginePanel() {
  const [health, setHealth] = useState<MuseHealth | null>(null);
  const [signals, setSignals] = useState<MuseSignal[]>([]);
  const [scan, setScan] = useState<MuseScanState | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [decisionFilter, setDecisionFilter] = useState<MuseDecision | 'ALL'>('ALL');
  const [preview, setPreview] = useState<MusePreview | null>(null);
  const [sounding, setSounding] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [h, s] = await Promise.all([
        apiClient.get<MuseHealth>('/api/muse/health'),
        apiClient.get<{ signals: MuseSignal[] }>('/api/muse/signals?limit=250'),
      ]);
      setHealth(h);
      setSignals(s.signals || []);
      if (h.scan?.scanId) setScan(h.scan as MuseScanState);
    } catch (err) {
      setNotice({ kind: 'err', text: errorText(err) });
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 30000);
    return () => clearInterval(timer);
  }, [refresh]);

  const filtered = useMemo(
    () =>
      signals.filter(
        (sig) => (decisionFilter === 'ALL' || sig.decision === decisionFilter) && museSignalMatchesQuery(sig, query),
      ),
    [signals, query, decisionFilter],
  );

  const selected = useMemo(
    () => signals.find((sig) => sig.signalId === selectedId) ?? filtered[0] ?? null,
    [signals, selectedId, filtered],
  );

  const weakest = selected ? museWeakestPrism(selected) : null;

  const startScan = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const state = await apiClient.post<MuseScanState>('/api/muse/scan', {});
      setScan(state);
      setNotice({ kind: 'ok', text: `Sounding the depths across ${state.totalPairs} pairs…` });
      await refresh();
    } catch (err) {
      setNotice({ kind: 'err', text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const loadPreview = useCallback(async (signalId: string) => {
    setPreview(null);
    try {
      const result = await apiClient.post<MusePreview>(`/api/muse/signals/${signalId}/preview`, {});
      setPreview(result);
    } catch (err) {
      setNotice({ kind: 'err', text: errorText(err) });
    }
  }, []);

  const runSounding = useCallback(async (symbol: string) => {
    setSounding(null);
    try {
      const result = await apiClient.post<Record<string, unknown>>('/api/muse/sounding', { symbol, bars: 300 });
      setSounding(result);
    } catch (err) {
      setNotice({ kind: 'err', text: errorText(err) });
    }
  }, []);

  const execute = useCallback(
    async (signalId: string, mode: 'paper' | 'demo') => {
      if (mode === 'demo' && !window.confirm('Route this MUSE signal to the DEMO broker?')) return;
      setBusy(true);
      try {
        const result = await apiClient.post<Record<string, unknown>>(`/api/muse/signals/${signalId}/execute`, {
          mode,
          idempotencyKey: makeIdempotencyKey(signalId),
        });
        setNotice({ kind: 'ok', text: `Execution ${String(result.status || 'recorded')} (${mode}).` });
        await refresh();
      } catch (err) {
        setNotice({ kind: 'err', text: errorText(err) });
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  useEffect(() => {
    if (selected) loadPreview(selected.signalId);
  }, [selected?.signalId, loadPreview]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="space-y-4 bg-gradient-to-b from-[#020b18] via-[#041423] to-[#020b18] p-4 text-slate-200">
      {/* ── Abyss header ─────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-cyan-400/20 bg-cyan-950/30 p-4">
        <div className="flex items-center gap-2">
          <Waves className="h-6 w-6 text-cyan-300" />
          <div>
            <h1 className="text-lg font-semibold tracking-wide text-cyan-100">MUSE · Meridian Undertow Synthesis</h1>
            <p className="text-xs text-cyan-200/70">
              Harmonic prism fusion · tide timing · halo consensus · {health?.contractVersion ?? 'muse.v1'}
            </p>
          </div>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-cyan-300/25 px-3 py-1 text-xs text-cyan-200">
            <MoonStar className="mr-1 inline h-3 w-3" />
            {selected?.tide ? `${museTideLabel(selected.tide)} · ${(selected.tide.quality * 100).toFixed(0)}%` : health ? 'Tide clock live' : '…'}
          </span>
          <Button onClick={startScan} disabled={busy} size="sm" className="bg-cyan-500 text-slate-950 hover:bg-cyan-400">
            {busy ? <RefreshCw className="mr-1 h-3 w-3 animate-spin" /> : <ScanSearch className="mr-1 h-3 w-3" />}
            Sound the market
          </Button>
          <Button onClick={refresh} variant="outline" size="sm">
            <RefreshCw className="h-3 w-3" />
          </Button>
        </div>
      </div>

      {notice && (
        <div className={cn('rounded-lg border px-3 py-2 text-sm', notice.kind === 'ok' ? 'border-emerald-400/30 bg-emerald-950/40' : 'border-rose-400/30 bg-rose-950/40')}>
          {notice.text}
        </div>
      )}

      {/* ── Scan counters ────────────────────────────────────────── */}
      {scan && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-6">
          {[
            ['Pairs', scan.totalPairs],
            ['Prime', scan.primeCount],
            ['Stage', scan.stageCount],
            ['Dormant', scan.dormantCount],
            ['Blocked', scan.blockedCount],
            ['Errors', scan.errorCount],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg border border-cyan-400/15 bg-slate-950/60 px-3 py-2 text-center">
              <div className="text-xl font-semibold tabular-nums text-cyan-100">{value}</div>
              <div className="text-[11px] uppercase tracking-wider text-slate-400">{label}</div>
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[300px_1fr_300px]">
        {/* ── Sonar list ─────────────────────────────────────────── */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-2 top-2.5 h-3.5 w-3.5 text-slate-500" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter pair, setup…"
                className="w-full rounded-lg border border-cyan-400/20 bg-slate-950/80 py-2 pl-8 pr-2 text-sm outline-none placeholder:text-slate-600 focus:border-cyan-300/50"
              />
            </div>
          </div>
          <div className="flex flex-wrap gap-1">
            {(['ALL', ...DECISIONS] as const).map((d) => (
              <button
                key={d}
                onClick={() => setDecisionFilter(d)}
                className={cn(
                  'rounded-full border px-2.5 py-0.5 text-[11px]',
                  decisionFilter === d ? 'border-cyan-300/60 bg-cyan-400/15 text-cyan-100' : 'border-slate-700 text-slate-400',
                )}
              >
                {d}
              </button>
            ))}
          </div>
          <div className="max-h-[560px] space-y-1.5 overflow-y-auto pr-1">
            {filtered.map((sig) => (
              <button
                key={sig.signalId}
                onClick={() => setSelectedId(sig.signalId)}
                className={cn(
                  'w-full rounded-lg border p-2.5 text-left transition-colors',
                  selected?.signalId === sig.signalId ? 'border-cyan-300/60 bg-cyan-950/60' : 'border-slate-800 bg-slate-950/60 hover:border-cyan-400/30',
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-slate-100">{sig.pair}</span>
                  <span className={cn('rounded-full border px-2 py-0.5 text-[10px] font-medium', museDecisionClass(sig.decision))}>
                    {sig.decision}
                  </span>
                </div>
                <div className="mt-1 flex items-center justify-between text-[11px] text-slate-400">
                  <span className={sig.direction === 'LONG' ? 'text-emerald-300' : sig.direction === 'SHORT' ? 'text-rose-300' : ''}>
                    {sig.direction} · {museSetupLabel(sig.setup)}
                  </span>
                  <span className="tabular-nums">{museScoreText(sig.score, sig.maxScore)}</span>
                </div>
                <Progress value={Math.min(100, sig.score)} className="mt-1.5 h-1" />
              </button>
            ))}
            {!filtered.length && <p className="py-8 text-center text-sm text-slate-500">No signals on this sounding.</p>}
          </div>
        </div>

        {/* ── Prism constellation ────────────────────────────────── */}
        <div className="space-y-3">
          {!selected ? (
            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-8 text-center text-sm text-slate-500">
              <Shell className="mx-auto mb-2 h-8 w-8 text-cyan-400/50" />
              Run a sounding, then select a signal to inspect its prisms.
            </div>
          ) : (
            <>
              <div className="rounded-xl border border-cyan-400/20 bg-slate-950/70 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-base font-semibold text-slate-100">{selected.pair}</h2>
                  <span className={cn('rounded-full border px-2 py-0.5 text-[11px]', museDecisionClass(selected.decision))}>
                    {selected.decision}
                  </span>
                  <span className="text-xs text-slate-400">
                    {selected.direction} · {museSetupLabel(selected.setup)} · {museScoreText(selected.score, selected.maxScore)}
                  </span>
                  <span className="ml-auto text-[11px] text-slate-500">D1 / H4 / M15 / M5</span>
                </div>
                {/* Phase voyage */}
                <div className="mt-3 flex items-center gap-1">
                  {PHASES.map((step, i) => {
                    const active = i <= musePhaseIndex(selected.phase);
                    return (
                      <div key={step.id} className="flex flex-1 items-center gap-1">
                        <div className={cn('flex-1 rounded-full py-1 text-center text-[10px] font-medium', active ? 'bg-cyan-400/25 text-cyan-100' : 'bg-slate-800/80 text-slate-500')}>
                          {step.label}
                        </div>
                        {i < PHASES.length - 1 && <div className={cn('h-px w-2', active ? 'bg-cyan-300/60' : 'bg-slate-700')} />}
                      </div>
                    );
                  })}
                </div>
                <p className="mt-2 text-xs text-slate-400">{selected.decisionReason}</p>
                {weakest && (
                  <p className="mt-1 flex items-center gap-1 text-[11px] text-amber-200/90">
                    <Anchor className="h-3 w-3" /> Harmonic anchor: {musePrismLabel(weakest.name)} at {(weakest.quality * 100).toFixed(1)}% drags conviction.
                  </p>
                )}
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                  <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-cyan-200/80">
                    <Compass className="h-3.5 w-3.5" /> Prism constellation
                  </h3>
                  {selected.prisms.map((p) => (
                    <PrismBar key={p.name} name={p.name} quality={p.quality} />
                  ))}
                  <div className="grid grid-cols-3 gap-2 border-t border-slate-800 pt-2 text-center text-[11px]">
                    <div><div className="tabular-nums text-slate-100">{(selected.conviction * 100).toFixed(1)}%</div><div className="text-slate-500">conviction</div></div>
                    <div><div className="tabular-nums text-slate-100">{(selected.timingFactor * 100).toFixed(0)}%</div><div className="text-slate-500">tide ×</div></div>
                    <div><div className="tabular-nums text-slate-100">×{selected.haloModifier.toFixed(2)}</div><div className="text-slate-500">halo</div></div>
                  </div>
                </div>
                <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                  <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-cyan-200/80">
                    <Crosshair className="h-3.5 w-3.5" /> Release levels
                  </h3>
                  {[
                    ['Entry', musePrice(selected.entry)],
                    ['Abyss stop', musePrice(selected.stop)],
                    ['Haven target', musePrice(selected.target)],
                    ['Reward × risk', selected.rr != null ? `${selected.rr.toFixed(2)}R` : '—'],
                    ['ATR', selected.atr != null ? musePrice(selected.atr, 6) : '—'],
                  ].map(([label, value]) => (
                    <div key={label} className="flex items-center justify-between text-sm">
                      <span className="text-slate-400">{label}</span>
                      <span className="font-mono tabular-nums text-slate-100">{value}</span>
                    </div>
                  ))}
                  <div className="border-t border-slate-800 pt-2">
                    <h4 className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">Deterministic gates</h4>
                    <div className="max-h-36 space-y-1 overflow-y-auto">
                      {selected.gates.map((g) => (
                        <div key={g.name} className="flex items-center gap-1.5 text-[11px]">
                          {g.passed ? <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-400" /> : <XCircle className="h-3 w-3 shrink-0 text-rose-400" />}
                          <span className="font-mono text-slate-300">{g.name}</span>
                          {!g.passed && g.reason && <span className="truncate text-rose-300/80">{g.reason}</span>}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              {/* Sounding history */}
              <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <div className="flex items-center gap-2">
                  <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-cyan-200/80">
                    <Radar className="h-3.5 w-3.5" /> Sounding — causal replay
                  </h3>
                  <Button variant="outline" size="sm" className="ml-auto" onClick={() => runSounding(selected.symbol)}>
                    <Play className="mr-1 h-3 w-3" /> Replay {selected.pair}
                  </Button>
                </div>
                {sounding && (
                  <div className="mt-2 text-xs text-slate-300">
                    <span className="tabular-nums">
                      {String(sounding.signals)} signals · {String(sounding.prime)} prime · hit rate {typeof sounding.hitRate === 'number' ? `${(sounding.hitRate * 100).toFixed(1)}%` : '—'}
                    </span>
                    {Array.isArray(sounding.rows) && sounding.rows.length > 0 && (
                      <div className="mt-2 max-h-32 space-y-1 overflow-y-auto">
                        {(sounding.rows as Record<string, unknown>[]).slice(-8).reverse().map((row, i) => (
                          <div key={i} className="flex justify-between font-mono text-[11px] text-slate-400">
                            <span>{String(row.generatedAt ?? '').slice(0, 16).replace('T', ' ')}</span>
                            <span>{String(row.direction)} {String(row.setup)}</span>
                            <span>{String(row.decision)} {typeof row.score === 'number' ? row.score.toFixed(1) : ''}</span>
                            <span className={row.outcome === 'TARGET' ? 'text-emerald-300' : row.outcome === 'STOP' ? 'text-rose-300' : ''}>
                              {String(row.outcome ?? '')}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* ── Execution column ───────────────────────────────────── */}
        <div className="space-y-3">
          <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
            <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-cyan-200/80">
              <ShieldCheck className="h-3.5 w-3.5" /> Execution attestation
            </h3>
            {!selected ? (
              <p className="mt-2 text-xs text-slate-500">Select a signal to attest.</p>
            ) : (
              <>
                <div className="mt-2 space-y-1">
                  {(preview?.checks ?? []).map((c) => (
                    <div key={c.name} className="flex items-center gap-1.5 text-[11px]">
                      {c.passed ? <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-400" /> : <XCircle className="h-3 w-3 shrink-0 text-rose-400" />}
                      <span className="font-mono text-slate-300">{c.name}</span>
                      {!c.passed && c.reason && <span className="truncate text-rose-300/80">{c.reason}</span>}
                    </div>
                  ))}
                  {!preview && <p className="text-xs text-slate-500">Attesting quote, spread, drift…</p>}
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <Button size="sm" disabled={busy || !preview?.executable} onClick={() => selected && execute(selected.signalId, 'paper')} className="bg-cyan-500 text-slate-950 hover:bg-cyan-400">
                    Paper release
                  </Button>
                  <Button size="sm" variant="outline" disabled={busy || !preview?.executable} onClick={() => selected && execute(selected.signalId, 'demo')}>
                    Demo order
                  </Button>
                </div>
                {preview && !preview.executable && (
                  <p className="mt-2 flex items-start gap-1 text-[11px] text-amber-200/90">
                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" /> Fail-closed: resolve the failed checks before release. Live stays locked until research is VALIDATED.
                  </p>
                )}
              </>
            )}
          </div>
          <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-4 text-[11px] text-slate-400">
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-cyan-200/80">How MUSE differs</h3>
            <p>Harmonic conviction (weakest prism rules) · tide-scaled timing · median halo · post-echo arc. Paper-first; demo follows EXECUTOR_MODE; live disabled.</p>
          </div>
        </div>
      </div>
    </div>
  );
}
