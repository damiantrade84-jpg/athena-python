/**
 * Tuning Lab — separate from Backtest V3. Lets an operator tune Engine A/B
 * timeframe roles, per-group weights/thresholds, Engine B gate overrides, and
 * a set of new indicators (ROC, MFI; Engine B oscillator confluence)
 * for one pair, run a frozen-candle A/B comparison against current default
 * scoring, and — only on explicit confirmation — push an improvement into
 * config.local.yaml. Uses /api/experiment/* only; does not touch /api/v3/*.
 */
import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, FlaskConical, Play, RefreshCw, Upload } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { useStore } from '@/hooks/useStore';
import { cn } from '@/lib/utils';
import type { PairListEntry, PairsResponse } from '@/types/athena';

type EngineKey = 'A' | 'B';

type PairOption = { symbol: string; display: string; group: string; enabled: boolean };

/** Trimmed copy of BacktestV3Panel's group builder — kept local so this panel
 * never imports from / touches BacktestV3Panel.tsx. */
function pairsByGroup(data: PairsResponse | null | undefined): Record<string, PairOption[]> {
  if (!data) return {};
  if (data.groups && typeof data.groups === 'object') {
    const out: Record<string, PairOption[]> = {};
    for (const [group, items] of Object.entries(data.groups)) {
      if (!Array.isArray(items)) continue;
      out[group] = items
        .map((item) => {
          const row = item as { sym?: string; label?: string; enabled?: boolean };
          const symbol = String(row.sym || '').trim();
          const display = String(row.label || row.sym || '').trim();
          if (!symbol && !display) return null;
          return { symbol: symbol || display, display: display || symbol, group, enabled: row.enabled !== false };
        })
        .filter((row): row is PairOption => row != null)
        .sort((a, b) => a.display.localeCompare(b.display));
    }
    return out;
  }
  const flat: PairListEntry[] = Array.isArray(data.pairs) ? data.pairs : [];
  const out: Record<string, PairOption[]> = {};
  for (const item of flat) {
    const group = String(item.type || 'Other');
    const display = String(item.display || item.symbol || '').trim();
    const symbol = String(item.symbol || item.display || '').trim();
    if (!display && !symbol) continue;
    if (!out[group]) out[group] = [];
    out[group].push({ symbol: symbol || display, display: display || symbol, group, enabled: item.enabled !== false });
  }
  for (const group of Object.keys(out)) out[group].sort((a, b) => a.display.localeCompare(b.display));
  return out;
}

type KnobDef = {
  id: string;
  label: string;
  appliesTo: EngineKey[];
  kind: 'enum' | 'float' | 'bool';
  scope: 'group_style' | 'group' | 'global';
  default: number | string | boolean | null;
  choices: string[] | null;
  min: number | null;
  max: number | null;
  description: string;
};

type KnobsCatalog = { success?: boolean; engine: EngineKey; tfLadder: string[]; tfRoles: string[]; knobs: KnobDef[] };

type CurrentValue = string | number | boolean | null;
type DefaultsResponse = { success?: boolean; error?: string; group?: string; style?: string; values?: Record<string, CurrentValue> };

function formatCurrentValue(value: CurrentValue | undefined): string {
  if (value === undefined || value === null) return 'no override (style default)';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
  return String(value);
}

type RunMetrics = {
  totalTrades?: number;
  winRate?: number;
  profitFactor?: number;
  expectancy?: number;
  sqn?: number;
  totalR?: number;
  maxDrawdownR?: number;
  wfSplit?: { lowSampleSqnWarning?: boolean };
  error?: string;
};

type RunResult = {
  success?: boolean;
  error?: string;
  group?: string;
  style?: string;
  engine?: EngineKey;
  symbol?: string;
  pair?: string;
  replayDays?: number | null;
  knobs?: Record<string, unknown>;
  overlay?: Record<string, unknown>;
  baseline?: RunMetrics;
  variant?: RunMetrics;
  timeframeComparison?: {
    baseline?: { roles?: Record<string, string>; trendTimeframes?: string[] };
    variant?: { roles?: Record<string, string>; trendTimeframes?: string[] };
  };
};

function formatRoleRoute(roles: Record<string, string> | undefined): string {
  if (!roles) return 'Unavailable';
  return ['regime', 'bias', 'structure', 'setup', 'trigger', 'execution']
    .map((role) => roles[role])
    .filter(Boolean)
    .join(' → ') || 'Unavailable';
}

function metricValue(value: number | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return value.toFixed(digits);
}

function CompareTile({ label, base, variant, digits = 2, pct = false }: {
  label: string; base?: number; variant?: number; digits?: number; pct?: boolean;
}) {
  // Backend winRate is already a 0-100 percentage — render it as-is.
  const fmt = (v?: number) => (pct ? (v == null ? '—' : `${v.toFixed(1)}%`) : metricValue(v, digits));
  const delta = base != null && variant != null ? variant - base : null;
  const improved = delta != null && delta > 0;
  const worsened = delta != null && delta < 0;
  return (
    <div className="rounded-xl border border-border/60 bg-black/20 p-3">
      <div className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">{label}</div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="font-mono text-sm text-muted-foreground">{fmt(base)}</span>
        <span className="text-muted-foreground">→</span>
        <span
          className={cn(
            'font-mono text-lg font-semibold',
            improved && 'text-emerald-400',
            worsened && 'text-rose-400',
          )}
        >
          {fmt(variant)}
        </span>
      </div>
    </div>
  );
}

function KnobField({ knob, value, currentValue, loadingCurrent, onChange }: {
  knob: KnobDef; value: string; currentValue: CurrentValue | undefined; loadingCurrent: boolean;
  onChange: (id: string, v: string) => void;
}) {
  const currentLabel = loadingCurrent ? 'loading…' : formatCurrentValue(currentValue);
  if (knob.kind === 'enum') {
    return (
      <Select value={value || '__unset__'} onValueChange={(v) => onChange(knob.id, v === '__unset__' ? '' : v)}>
        <SelectTrigger className="truncate">
          <SelectValue placeholder={`current: ${currentLabel}`} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__unset__">— keep current ({currentLabel}) —</SelectItem>
          {(knob.choices || []).map((choice) => (
            <SelectItem key={choice} value={choice}>
              {choice}{choice === currentValue ? ' (current)' : ''}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }
  if (knob.kind === 'bool') {
    return (
      <Select value={value || '__unset__'} onValueChange={(v) => onChange(knob.id, v === '__unset__' ? '' : v)}>
        <SelectTrigger className="truncate">
          <SelectValue placeholder={`current: ${currentLabel}`} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__unset__">— keep current ({currentLabel}) —</SelectItem>
          <SelectItem value="true">true</SelectItem>
          <SelectItem value="false">false</SelectItem>
        </SelectContent>
      </Select>
    );
  }
  return (
    <div className="space-y-1">
      <Input
        type="number"
        step="0.05"
        min={knob.min ?? undefined}
        max={knob.max ?? undefined}
        placeholder={`current: ${currentLabel}`}
        value={value}
        onChange={(e) => onChange(knob.id, e.target.value)}
      />
      <div className="text-[10px] text-muted-foreground">
        Currently live: <span className="font-mono text-foreground/80">{currentLabel}</span>
      </div>
    </div>
  );
}

function KnobGroup({ title, hint, knobs, values, currentValues, loadingCurrent, onChange }: {
  title: string; hint?: string; knobs: KnobDef[];
  values: Record<string, string>; currentValues: Record<string, CurrentValue>; loadingCurrent: boolean;
  onChange: (id: string, v: string) => void;
}) {
  if (!knobs.length) return null;
  return (
    <div className="space-y-3 rounded-xl border border-border/50 bg-black/10 p-3">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.1em] text-foreground">{title}</div>
        {hint && <div className="text-[10px] text-muted-foreground">{hint}</div>}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {knobs.map((knob) => (
          <div key={knob.id} className="space-y-1.5">
            <Label className="flex items-center gap-1.5 text-xs">
              {knob.label}
              {knob.scope === 'global' && (
                <Badge variant="outline" className="border-amber-400/30 bg-amber-400/10 text-[9px] text-amber-200">
                  GLOBAL
                </Badge>
              )}
            </Label>
            <KnobField
              knob={knob}
              value={values[knob.id] ?? ''}
              currentValue={currentValues[knob.id]}
              loadingCurrent={loadingCurrent}
              onChange={onChange}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function ExperimentLabPanel() {
  const { showToast } = useStore();
  const [engine, setEngine] = useState<EngineKey>('A');
  const [style, setStyle] = useState('intraday');
  // Engine B lookback cap for the bar-by-bar replay (days). Engine A has no
  // such window and runs fast on the full frozen history.
  const [replayDays, setReplayDays] = useState('90');
  // /api/pairs groups by broad asset-class display labels. This is only a
  // selector/filter; the API derives the canonical score group from `pair`.
  const [assetGroup, setAssetGroup] = useState('');
  const [pair, setPair] = useState('');
  const [knobValues, setKnobValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<RunResult | null>(null);

  const pairsPoll = useApiPoll<PairsResponse>('/api/pairs', 0);
  const knobsPoll = useApiPoll<KnobsCatalog>(`/api/experiment/knobs?engine=${engine}`, 0);
  const defaultsUrl = pair
    ? `/api/experiment/defaults?engine=${engine}&symbol=${encodeURIComponent(pair)}&style=${style}`
    : '';
  const defaultsPoll = useApiPoll<DefaultsResponse>(defaultsUrl, 0, Boolean(defaultsUrl));
  const runMutation = useApiPost<RunResult>();
  const pushMutation = useApiPost<{ success?: boolean; error?: string; note?: string }>();

  const grouped = useMemo(() => pairsByGroup(pairsPoll.data), [pairsPoll.data]);
  const groupNames = useMemo(
    () => Object.keys(grouped).filter((name) => (grouped[name] || []).length > 0).sort(),
    [grouped],
  );
  const groupPairs = useMemo(() => {
    const rows = grouped[assetGroup] || [];
    const enabled = rows.filter((row) => row.enabled);
    return enabled.length ? enabled : rows;
  }, [grouped, assetGroup]);

  useEffect(() => {
    if (!groupNames.length) return;
    if (!assetGroup || !groupNames.includes(assetGroup)) {
      setAssetGroup(groupNames.find((name) => name.toLowerCase().includes('forex')) || groupNames[0]);
      setResult(null);
    }
  }, [groupNames, assetGroup]);

  useEffect(() => {
    if (!groupPairs.length) { setPair(''); setResult(null); return; }
    const stillValid = groupPairs.some((row) => row.display === pair || row.symbol === pair);
    if (!stillValid) {
      setPair(groupPairs[0].display || groupPairs[0].symbol);
      setResult(null);
    }
  }, [groupPairs, pair]);

  // Reset tuning knobs whenever the engine changes — Engine A and Engine B
  // knob ids don't overlap, so stale values from the other engine are inert
  // anyway, but clearing avoids confusing UI state.
  useEffect(() => {
    setKnobValues({});
    setResult(null);
  }, [engine]);

  const knobs = knobsPoll.data?.knobs || [];
  const tfRoleKnobs = knobs.filter((k) => k.id.startsWith('tf_role.'));
  const weightKnobs = knobs.filter((k) => k.id.startsWith('engine_a.weight.') || k.id === 'engine_a.trade_threshold');
  const gateKnobs = knobs.filter((k) => k.id.startsWith('engine_b.gate.'));
  const indicatorKnobs = knobs.filter((k) => k.id.startsWith('indicator.'));
  const currentValues = defaultsPoll.data?.values || {};
  const scoreGroup = defaultsPoll.data?.group || '';
  const loadingCurrent = defaultsPoll.loading;

  const setKnob = useCallback((id: string, value: string) => {
    setResult(null);
    setKnobValues((prev) => {
      const next = { ...prev };
      if (value === '') delete next[id];
      else next[id] = value;
      return next;
    });
  }, []);

  const activeKnobCount = Object.keys(knobValues).length;

  const buildOverlayPayload = useCallback((): Record<string, unknown> => {
    const payload: Record<string, unknown> = {};
    for (const [id, raw] of Object.entries(knobValues)) {
      const knob = knobs.find((k) => k.id === id);
      if (!knob) continue;
      if (knob.kind === 'bool') payload[id] = raw === 'true';
      else if (knob.kind === 'float') payload[id] = Number(raw);
      else payload[id] = raw;
    }
    return payload;
  }, [knobValues, knobs]);

  const runExperiment = useCallback(async () => {
    const token = pair.trim();
    if (!token) { showToast('Select a pair to test', 'error'); return; }
    setResult(null);
    const response = await runMutation.post('/api/experiment/run', {
      engine,
      symbol: token,
      style,
      replayDays: engine === 'B' ? Number(replayDays) : undefined,
      overlay: buildOverlayPayload(),
    });
    if (!response || response.error) {
      showToast(response?.error || runMutation.error || 'Tuning Lab run failed', 'error');
      setResult(response);
      return;
    }
    setResult(response);
    showToast(
      `${token}: baseline SQN ${metricValue(response.baseline?.sqn)} → variant SQN ${metricValue(response.variant?.sqn)}`,
      'success',
    );
  }, [pair, engine, style, buildOverlayPayload, runMutation, showToast]);

  const pushOverride = useCallback(async () => {
    if (!result || result.error || !result.group || !result.knobs) {
      showToast('Run this exact variant before pushing it', 'error');
      return;
    }
    const testedSymbol = result.symbol || result.pair || pair;
    const response = await pushMutation.post('/api/experiment/push', {
      engine: result.engine || engine,
      symbol: testedSymbol,
      group: result.group,
      style: result.style || style,
      confirm: true,
      knobs: result.knobs,
    });
    if (!response || response.error) {
      showToast(response?.error || pushMutation.error || 'Push failed', 'error');
      return;
    }
    showToast('Pushed to config.local.yaml. Restart Athena to apply it live.', 'success');
  }, [result, pair, engine, style, pushMutation, showToast]);

  const improved =
    result?.baseline?.sqn != null && result?.variant?.sqn != null && result.variant.sqn > result.baseline.sqn;
  const testedKnobCount = Object.keys(result?.knobs || {}).length;
  const hasGlobalKnob = Object.keys(result?.knobs || {}).some(
    (id) => knobs.find((k) => k.id === id)?.scope === 'global',
  );
  const lowSample = Boolean(
    result?.baseline?.wfSplit?.lowSampleSqnWarning || result?.variant?.wfSplit?.lowSampleSqnWarning,
  );

  return (
    <div className="space-y-5 p-4 md:p-6">
      <div className="relative overflow-hidden rounded-2xl border border-primary/20 bg-card/70 p-5 backdrop-blur-xl">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_8%_0%,hsl(var(--primary)/0.16),transparent_38%)]" />
        <div className="relative flex flex-col justify-between gap-4 lg:flex-row lg:items-center">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <FlaskConical className="h-5 w-5 text-primary" />
              <h1 className="text-lg font-semibold tracking-[0.08em] text-foreground">TUNING LAB</h1>
              <Badge variant="outline" className="border-emerald-400/30 bg-emerald-400/10 text-[10px] text-emerald-200">
                SEPARATE FROM BACKTEST V3
              </Badge>
            </div>
            <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
              Tune timeframe roles, weights/thresholds, and new indicators (ROC, MFI) for one pair,
              run it against frozen candles, and compare to today's default
              before pushing anything live. Uses /api/experiment/* only.
            </p>
          </div>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_1fr]">
        <Card className="panel-glass">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Play className="h-4 w-4 text-primary" />
              Configure &amp; run
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-3">
              {(['A', 'B'] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setEngine(value)}
                  className={cn(
                    'rounded-xl border p-3 text-left transition',
                    engine === value ? 'border-primary/55 bg-primary/12' : 'border-border/60 bg-black/15 hover:border-primary/30',
                  )}
                >
                  <div className="text-sm font-semibold">Engine {value}</div>
                </button>
              ))}
              <div className="space-y-2">
                <Label>Style</Label>
                <Select value={style} onValueChange={(value) => { setStyle(value); setResult(null); }}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {['intraday', 'swing'].map((item) => (
                      <SelectItem key={item} value={item}>{item}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {engine === 'B' && (
                  <>
                    <Label className="pt-1">Lookback (days)</Label>
                    <Select value={replayDays} onValueChange={(value) => { setReplayDays(value); setResult(null); }}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {['30', '60', '90', '180'].map((item) => (
                          <SelectItem key={item} value={item}>{item}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </>
                )}
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>Asset filter</Label>
                <Select
                  value={assetGroup || undefined}
                  onValueChange={(value) => { setAssetGroup(value); setPair(''); setResult(null); }}
                  disabled={!groupNames.length}
                >
                  <SelectTrigger><SelectValue placeholder={pairsPoll.loading ? 'Loading…' : 'Select asset class'} /></SelectTrigger>
                  <SelectContent>
                    {groupNames.map((name) => (
                      <SelectItem key={name} value={name}>{name} ({grouped[name]?.length ?? 0})</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Pair</Label>
                <Select value={pair || undefined} onValueChange={(value) => { setPair(value); setResult(null); }} disabled={!groupPairs.length}>
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder={assetGroup ? 'Select pair' : 'Pick an asset class first'} />
                  </SelectTrigger>
                  {/* popper + max height: long pair lists must wheel-scroll without
                      re-snapping to the selected (often first) item at the top */}
                  <SelectContent position="popper" className="max-h-72">
                    {groupPairs.map((row) => (
                      <SelectItem key={`${row.group}-${row.symbol}`} value={row.display || row.symbol}>{row.display}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {pair && (
              <div className="rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-muted-foreground">
                Score group:{' '}
                <span className="font-mono font-semibold text-foreground">
                  {loadingCurrent ? 'resolving…' : scoreGroup || 'unresolved'}
                </span>
                <span className="ml-2">Only this canonical score group is tuned or pushed.</span>
              </div>
            )}

            {knobsPoll.loading ? (
              <div className="rounded-xl border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
                Loading knob catalog…
              </div>
            ) : (
              <div className="space-y-3">
                <KnobGroup
                  title="Timeframe roles"
                  hint={`Ladder: ${(knobsPoll.data?.tfLadder || []).join(' > ')}. Each field shows the role's currently live timeframe for this pair — leave unset to keep it.`}
                  knobs={tfRoleKnobs}
                  values={knobValues}
                  currentValues={currentValues}
                  loadingCurrent={loadingCurrent}
                  onChange={setKnob}
                />
                {engine === 'A' && (
                  <KnobGroup
                    title="Engine A weights / threshold"
                    hint="Currently live values shown below each field — these are real per-group numbers already in production, not zero."
                    knobs={weightKnobs}
                    values={knobValues}
                    currentValues={currentValues}
                    loadingCurrent={loadingCurrent}
                    onChange={setKnob}
                  />
                )}
                {engine === 'B' && (
                  <KnobGroup
                    title="Engine B gate overrides"
                    hint='"no override" means this group/style has no explicit override and falls back to its style profile.'
                    knobs={gateKnobs}
                    values={knobValues}
                    currentValues={currentValues}
                    loadingCurrent={loadingCurrent}
                    onChange={setKnob}
                  />
                )}
                <KnobGroup
                  title="New indicators"
                  hint="These are genuinely new to both engines — currently 0 (unused) everywhere. Scoped to this score group only: pushing here only changes scoring for the group you tested, every other group is untouched."
                  knobs={indicatorKnobs}
                  values={knobValues}
                  currentValues={currentValues}
                  loadingCurrent={loadingCurrent}
                  onChange={setKnob}
                />
              </div>
            )}

            <Button className="w-full" onClick={runExperiment} disabled={runMutation.loading}>
              {runMutation.loading ? (
                <><RefreshCw className="mr-2 h-4 w-4 animate-spin" />Running…</>
              ) : (
                <><Play className="mr-2 h-4 w-4" />Run test ({activeKnobCount} knob{activeKnobCount === 1 ? '' : 's'} changed)</>
              )}
            </Button>
            {engine === 'B' && (
              <div className="text-[10px] text-muted-foreground">
                Engine B replays bar-by-bar — one test runs two backtests over the lookback window and can take several minutes.
              </div>
            )}
            {runMutation.error && (
              <div className="rounded-lg border border-rose-400/30 bg-rose-400/10 p-3 text-xs text-rose-200">{runMutation.error}</div>
            )}
          </CardContent>
        </Card>

        <Card className="panel-glass">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <FlaskConical className="h-4 w-4 text-primary" />
              Baseline vs. variant
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {!result ? (
              <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
                Run a test to compare default scoring against your changes.
              </div>
            ) : result.error ? (
              <div className="rounded-xl border border-rose-400/30 bg-rose-400/10 p-4 text-sm text-rose-200">{result.error}</div>
            ) : (
              <>
                {typeof result.replayDays === 'number' && (
                  <div className="text-[10px] text-muted-foreground">
                    Engine B replay window: last {result.replayDays} days — both runs use the same window.
                  </div>
                )}
                <div className="grid grid-cols-2 gap-2">
                  <CompareTile label="Trades" base={result.baseline?.totalTrades} variant={result.variant?.totalTrades} digits={0} />
                  <CompareTile label="SQN" base={result.baseline?.sqn} variant={result.variant?.sqn} />
                  <CompareTile label="Total R" base={result.baseline?.totalR} variant={result.variant?.totalR} />
                  <CompareTile label="Win rate" base={result.baseline?.winRate} variant={result.variant?.winRate} pct />
                  <CompareTile label="Expectancy" base={result.baseline?.expectancy} variant={result.variant?.expectancy} digits={3} />
                  <CompareTile label="Max DD (R)" base={result.baseline?.maxDrawdownR} variant={result.variant?.maxDrawdownR} />
                </div>

                {result.timeframeComparison && (
                  <div className="grid gap-2 rounded-xl border border-border/60 bg-black/20 p-3 text-xs md:grid-cols-2">
                    <div>
                      <div className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">Baseline TF route</div>
                      <div className="mt-1 font-mono">{formatRoleRoute(result.timeframeComparison.baseline?.roles)}</div>
                      {engine === 'A' && (
                        <div className="mt-1 text-[10px] text-muted-foreground">
                          Trend coherence TFs: {(result.timeframeComparison.baseline?.trendTimeframes || []).join(' / ') || 'Unavailable'}
                        </div>
                      )}
                    </div>
                    <div>
                      <div className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">Variant TF route</div>
                      <div className="mt-1 font-mono">{formatRoleRoute(result.timeframeComparison.variant?.roles)}</div>
                      {engine === 'A' && (
                        <div className="mt-1 text-[10px] text-muted-foreground">
                          Trend coherence TFs: {(result.timeframeComparison.variant?.trendTimeframes || []).join(' / ') || 'Unavailable'}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {lowSample && (
                  <div className="rounded-lg border border-amber-400/30 bg-amber-400/10 p-3 text-xs text-amber-200">
                    Fewer than 60 trades in this comparison — SQN deltas on small samples are noisy.
                    Treat any improvement as indicative, not proven.
                  </div>
                )}

                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button
                      variant={improved ? 'default' : 'outline'}
                      className="w-full"
                      disabled={!testedKnobCount || pushMutation.loading}
                    >
                      <Upload className="mr-2 h-4 w-4" />
                      Push to default
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle className="flex items-center gap-2">
                        <AlertTriangle className="h-4 w-4 text-amber-400" />
                        Confirm push to config.local.yaml
                      </AlertDialogTitle>
                      <AlertDialogDescription className="space-y-2">
                        <span className="block">
                          {testedKnobCount} tested knob{testedKnobCount === 1 ? '' : 's'} for engine {result.engine || engine}
                          {result.group ? `, score group "${result.group}"` : ''} will be deep-merged into config.local.yaml.
                        </span>
                        {!improved && (
                          <span className="block text-amber-300">
                            This run did not show a clear SQN improvement — pushing anyway is your call.
                          </span>
                        )}
                        {lowSample && (
                          <span className="block text-amber-300">
                            This run has fewer than 60 trades — the SQN comparison is statistically noisy.
                          </span>
                        )}
                        {hasGlobalKnob && (
                          <span className="block text-amber-300">
                            One or more changed knobs are GLOBAL and will affect every pair sharing that
                            config, not only {result.pair || result.symbol || 'this pair'}.
                          </span>
                        )}
                        <span className="block">
                          This does not affect the currently running Athena process — restart to apply it
                          to live scanning.
                        </span>
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction onClick={pushOverride}>Push override</AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
                {pushMutation.error && (
                  <div className="rounded-lg border border-rose-400/30 bg-rose-400/10 p-3 text-xs text-rose-200">{pushMutation.error}</div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export default memo(ExperimentLabPanel);
