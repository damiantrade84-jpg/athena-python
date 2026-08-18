/**
 * Session Heat — historical Engine A / Engine B performance by session bucket.
 *
 * X axis: fixed-UTC session buckets (Asia / London / NY AM / NY PM / Off-hours),
 *         each documented with the ICT killzone it contains.
 * Y axis: engine, optionally split by Engine A score group.
 * Colour: the selected metric relative to that row's own baseline, so a
 *         structurally weak group is not painted uniformly cold.
 *
 * Everything shown is measured from recorded backtest_trades_v3 outcomes.
 * ADVISORY ONLY — this surface never gates, sizes, scores, or approves anything.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ErrorBanner, RefreshButton } from '@/components/shared';
import { Flame, Info, AlertTriangle } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { SessionHeatCell, SessionHeatMatrix, SessionHeatRow } from '@/types/athena';

type MetricKey = 'expectancyR' | 'profitFactor' | 'winRate';

const METRIC_LABEL: Record<MetricKey, string> = {
  expectancyR: 'Expectancy (R)',
  profitFactor: 'Profit factor',
  winRate: 'Win rate',
};

const LOOKBACK_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '180', label: '6 months' },
  { value: '365', label: '1 year' },
  { value: '730', label: '2 years' },
  { value: '1095', label: '3 years' },
  { value: '0', label: 'All history' },
];

function fmtR(value: number | null | undefined): string {
  if (value == null) return '—';
  return `${value >= 0 ? '+' : ''}${value.toFixed(3)}R`;
}

function fmtMetric(cell: SessionHeatCell | undefined, metric: MetricKey): string {
  if (!cell || cell.trades === 0) return '—';
  if (metric === 'expectancyR') return fmtR(cell.expectancyR);
  if (metric === 'profitFactor') return cell.profitFactor == null ? 'n/a' : cell.profitFactor.toFixed(2);
  return cell.winRate == null ? '—' : `${(cell.winRate * 100).toFixed(1)}%`;
}

/**
 * Colour intensity comes from `heatScore` (delta vs the row baseline, -1..+1).
 * INSUFFICIENT cells stay neutral grey on purpose: a thin sample must never read
 * as a strong or weak window.
 */
function cellStyle(cell: SessionHeatCell | undefined): CSSProperties {
  if (!cell || cell.heat === 'INSUFFICIENT' || cell.heatScore == null) {
    return { background: 'hsl(var(--muted) / 0.25)', borderColor: 'transparent' };
  }
  const score = Math.max(-1, Math.min(1, cell.heatScore));
  const alpha = 0.08 + Math.abs(score) * 0.34;
  const hue = score >= 0 ? 'var(--long)' : 'var(--short)';
  return {
    background: `hsl(${hue} / ${alpha.toFixed(3)})`,
    borderColor: `hsl(${hue} / ${(alpha + 0.12).toFixed(3)})`,
  };
}

function rowLabel(row: SessionHeatRow): string {
  return row.scope === 'engine' ? `Engine ${row.engine} — all groups` : (row.scoreGroup ?? '');
}

/** Small reusable heat pill — shared by the signal cards and the detail panel. */
export function SessionHeatPill({
  label,
  tone,
  title,
  className,
}: {
  label: string;
  tone: string;
  title?: string;
  className?: string;
}) {
  const cls =
    tone === 'strong'
      ? 'bg-long/15 text-long border-long/30'
      : tone === 'weak'
        ? 'bg-short/15 text-short border-short/30'
        : tone === 'neutral'
          ? 'bg-muted/40 text-muted-foreground border-border/60'
          : 'bg-muted/20 text-muted-foreground border-border/40';
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-1.5 py-px text-[9px] font-mono whitespace-nowrap',
        cls,
        className,
      )}
    >
      <Flame className="w-2.5 h-2.5 shrink-0" />
      {label}
    </span>
  );
}

export default function SessionHeatChart({
  currentSessionKey,
  defaultScoreGroup,
}: {
  /** Highlighted column. Sourced from the snapshot so chart and cards agree. */
  currentSessionKey?: string;
  /** Pre-select the selected symbol's score group when the chart opens. */
  defaultScoreGroup?: string | null;
}) {
  const [engine, setEngine] = useState<'all' | 'A' | 'B'>('all');
  const [lookback, setLookback] = useState('1095');
  const [metric, setMetric] = useState<MetricKey>('expectancyR');
  const [scoreGroup, setScoreGroup] = useState<string>(defaultScoreGroup || 'all');
  const [data, setData] = useState<SessionHeatMatrix | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ engine, lookbackDays: lookback });
      const res = await fetch(`/api/live-dashboard/session-heat?${params.toString()}`);
      const json = (await res.json()) as SessionHeatMatrix & { error?: string };
      if (!res.ok || json.error) setError(json.error || `HTTP ${res.status}`);
      else setData(json);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [engine, lookback]);

  useEffect(() => {
    load();
  }, [load]);

  const sessions = data?.sessions || [];
  const activeSession = currentSessionKey || data?.currentSession?.key;
  const scoreGroups = useMemo(() => data?.coverage?.scoreGroups || [], [data]);

  const rows = useMemo(() => {
    const all = data?.matrix || [];
    if (scoreGroup === 'all') return all.filter((r) => r.scope === 'engine');
    // Show the score group beside the engine-wide row it is compared against.
    return all.filter((r) => r.scope === 'engine' || r.scoreGroup === scoreGroup);
  }, [data, scoreGroup]);

  const settings = data?.settings;
  const minSample = settings?.minSample ?? 30;
  const disabled = data?.status === 'DISABLED';
  const noData = data?.status === 'NO_DATA' || data?.status === 'ERROR';

  return (
    <Card className="border-border/60 bg-card/50">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <CardTitle className="panel-title flex items-center gap-2">
            <Flame className="w-3.5 h-3.5 text-primary" /> Session Heat
            {data?.currentSession && (
              <Badge className="text-[9px] bg-primary/15 text-primary">
                NOW · {data.currentSession.label} · {data.currentSession.minutesRemaining}m left
              </Badge>
            )}
            <Badge variant="outline" className="text-[9px]">
              advisory
            </Badge>
          </CardTitle>
          <div className="flex items-center gap-1.5 flex-wrap">
            <Select value={engine} onValueChange={(v) => setEngine(v as 'all' | 'A' | 'B')}>
              <SelectTrigger className="w-[116px] h-7 text-[11px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Both engines</SelectItem>
                <SelectItem value="A">Engine A</SelectItem>
                <SelectItem value="B">Engine B</SelectItem>
              </SelectContent>
            </Select>
            <Select value={lookback} onValueChange={setLookback}>
              <SelectTrigger className="w-[108px] h-7 text-[11px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LOOKBACK_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={metric} onValueChange={(v) => setMetric(v as MetricKey)}>
              <SelectTrigger className="w-[132px] h-7 text-[11px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(METRIC_LABEL) as MetricKey[]).map((m) => (
                  <SelectItem key={m} value={m}>
                    {METRIC_LABEL[m]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={scoreGroup} onValueChange={setScoreGroup}>
              <SelectTrigger className="w-[156px] h-7 text-[11px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Engine level only</SelectItem>
                {scoreGroups.map((g) => (
                  <SelectItem key={g} value={g}>
                    {g}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <RefreshButton onClick={load} loading={loading} />
          </div>
        </div>
      </CardHeader>

      <CardContent className="pt-0 space-y-3">
        {error && <ErrorBanner message={error} onRetry={load} />}

        {disabled && (
          <p className="text-[11px] text-muted-foreground flex items-center gap-1.5">
            <Info className="w-3.5 h-3.5 shrink-0" />
            Session Heat is disabled in config ({data?.reason || 'LIVE_DASHBOARD.SESSION_HEAT.ENABLED'}).
          </p>
        )}

        {noData && (
          <p className="text-[11px] text-warning flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            No recorded trade outcomes available{data?.error ? `: ${data.error}` : '.'} Run the v3 backtest to
            populate <code className="text-[10px]">backtest_trades_v3</code>.
          </p>
        )}

        {!disabled && !noData && rows.length > 0 && (
          <>
            <ScrollArea className="w-full">
              <div className="min-w-[720px]">
                {/* Column headers — the documented session contract */}
                <div className="grid grid-cols-[190px_repeat(5,1fr)] gap-1">
                  <div className="text-[9px] uppercase tracking-widest text-muted-foreground self-end pb-1">
                    Engine / score group
                  </div>
                  {sessions.map((s) => {
                    const active = s.key === activeSession;
                    return (
                      <div
                        key={s.key}
                        title={`${s.windowUtc} · ${s.killzone} · ${s.description}`}
                        className={cn(
                          'text-center pb-1 rounded-t-md px-1',
                          active && 'bg-primary/10 ring-1 ring-primary/40',
                        )}
                      >
                        <div className={cn('text-[11px] font-semibold', active ? 'text-primary' : 'text-foreground')}>
                          {s.label}
                          {active && <span className="ml-1 text-[8px] align-middle">NOW</span>}
                        </div>
                        <div className="text-[9px] text-muted-foreground font-mono">{s.windowUtc}</div>
                      </div>
                    );
                  })}
                </div>

                {/* One row per engine / score group */}
                {rows.map((row) => (
                  <div
                    key={`${row.engine}-${row.scoreGroup ?? 'all'}`}
                    className="grid grid-cols-[190px_repeat(5,1fr)] gap-1 mb-1"
                  >
                    <div className="flex flex-col justify-center pr-2 py-1 min-w-0">
                      <span className="text-[11px] font-mono font-semibold truncate" title={rowLabel(row)}>
                        {rowLabel(row)}
                      </span>
                      <span className="text-[9px] text-muted-foreground font-mono">
                        base {fmtR(row.baseline.expectancyR)} · n={row.baseline.trades}
                      </span>
                    </div>
                    {sessions.map((s) => {
                      const cell = row.cells?.[s.key];
                      const active = s.key === activeSession;
                      const insufficient = !cell || cell.heat === 'INSUFFICIENT';
                      return (
                        <div
                          key={s.key}
                          title={
                            cell?.heatReason
                              ? `Engine ${row.engine} · ${rowLabel(row)} · ${s.label}: ${cell.heatReason}`
                              : 'No sample'
                          }
                          style={cellStyle(cell)}
                          className={cn(
                            'rounded-md border px-1.5 py-1.5 text-center transition-colors',
                            active && 'ring-1 ring-primary/50',
                          )}
                        >
                          <div className={cn('text-xs font-mono font-bold', insufficient && 'text-muted-foreground')}>
                            {insufficient ? 'n/a' : fmtMetric(cell, metric)}
                          </div>
                          <div className="text-[9px] text-muted-foreground font-mono">n={cell?.trades ?? 0}</div>
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
            </ScrollArea>

            {/* Legend + provenance */}
            <div className="flex items-center gap-3 flex-wrap text-[9px] text-muted-foreground pt-1 border-t border-border/40">
              <span className="flex items-center gap-1">
                <span className="inline-block w-3 h-3 rounded-sm" style={{ background: 'hsl(var(--long) / 0.42)' }} />
                better than this row&apos;s baseline
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-3 h-3 rounded-sm" style={{ background: 'hsl(var(--short) / 0.42)' }} />
                worse than baseline
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-3 h-3 rounded-sm" style={{ background: 'hsl(var(--muted) / 0.25)' }} />
                n/a — under {minSample} trades
              </span>
              {settings && (
                <span>
                  STRONG/WEAK needs ≥{settings.minDeltaR}R and ≥{settings.significanceZ}·SE vs baseline
                </span>
              )}
            </div>

            {data?.coverage && (
              <p className="text-[9px] text-muted-foreground font-mono">
                {data.coverage.trades.toLocaleString()} recorded trades from {data.source} ·{' '}
                {data.coverage.runsUsed ?? 0} de-duplicated runs · {data.coverage.firstEntryIso?.slice(0, 10)} →{' '}
                {data.coverage.lastEntryIso?.slice(0, 10)} · cache {data.cache?.state}
              </p>
            )}

            {data?.status === 'INSUFFICIENT' && (
              <p className="text-[10px] text-warning flex items-center gap-1.5">
                <AlertTriangle className="w-3 h-3 shrink-0" />
                Every cell is below the {minSample}-trade minimum — no session call can be made yet.
              </p>
            )}
          </>
        )}

        {!disabled && !noData && rows.length === 0 && !loading && (
          <p className="text-[11px] text-muted-foreground">No rows for this filter.</p>
        )}
      </CardContent>
    </Card>
  );
}
