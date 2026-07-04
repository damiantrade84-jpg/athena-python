import { useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Activity, Shield } from 'lucide-react';
import {
  getFactorStatus,
  getFactorSignals,
  type FactorSignalRow,
} from '@/lib/forexFactorApi';
import {
  FX_ACCENT,
  Stat,
  TabEmpty,
  TabShell,
  useForexTabFetch,
} from './shared';

function isStaleDate(value: string | null | undefined, maxAgeDays = 7): boolean {
  if (!value) return true;
  const d = Date.parse(String(value).slice(0, 10));
  if (!Number.isFinite(d)) return true;
  return Date.now() - d > maxAgeDays * 86400000;
}

export default function OverviewTab() {
  const statusFetch = useForexTabFetch(getFactorStatus, [], 60000);
  const signalsFetch = useForexTabFetch(() => getFactorSignals(), [], 60000);
  const blockedFetch = useForexTabFetch(() => getFactorSignals({ blocked_only: true }), [], 60000);

  const loading = statusFetch.loading || signalsFetch.loading;
  const error = statusFetch.error || signalsFetch.error;

  const status = statusFetch.envelope?.data;
  const freshness = status?.data_freshness || {};
  const signals = signalsFetch.envelope?.data?.signals || [];

  const eligibleByFamily = useMemo(() => {
    const counts: Record<string, number> = { carry: 0, momentum: 0, value: 0 };
    for (const row of signals) {
      const action = (row.action || '').toUpperCase();
      if (action !== 'BUY' && action !== 'SELL') continue;
      for (const fam of row.aligned_families || []) {
        const key = fam.toLowerCase();
        if (key in counts) counts[key] += 1;
      }
    }
    return counts;
  }, [signals]);

  const blockedPairs = useMemo(() => {
    const rows = blockedFetch.envelope?.data?.signals || [];
    return rows.map((row: FactorSignalRow) => ({
      symbol: row.symbol || '?',
      reasons: [
        row.reason,
        ...(row.blocked_families || []).map((f) => `blocked:${f}`),
        ...(row.diagnostics || []).map((d) => d.message || d.code || ''),
      ].filter(Boolean) as string[],
    }));
  }, [blockedFetch.envelope?.data?.signals]);

  return (
    <TabShell
      loading={loading}
      error={error}
      diagnostics={[...statusFetch.diagnostics, ...signalsFetch.diagnostics]}
      warnings={[...(statusFetch.envelope?.warnings || []), ...(signalsFetch.envelope?.warnings || [])]}
      onRefresh={() => { statusFetch.refresh(); signalsFetch.refresh(); blockedFetch.refresh(); }}
    >
      {!status ? (
        <TabEmpty message="No factor engine status available." />
      ) : (
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Activity className="h-4 w-4" style={{ color: FX_ACCENT }} /> Engine status
              </CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <Stat label="Latest as-of" value={status.latest_as_of_date || '—'} />
              <Stat label="Decisions" value={status.decision_count ?? 0} />
              <Stat label="Swap audit OK" value={status.swap_audit_valid_symbol_count ?? 0} />
              <Stat
                label="Actions"
                value={
                  <span className="text-xs">
                    {Object.entries(status.action_counts || {})
                      .map(([k, v]) => `${k}:${v}`)
                      .join(' · ') || '—'}
                  </span>
                }
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Shield className="h-4 w-4" style={{ color: FX_ACCENT }} /> Data freshness
              </CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <Stat
                label="Decisions"
                value={freshness.decisions_latest_date || '—'}
                warn={isStaleDate(freshness.decisions_latest_date, 7)}
              />
              <Stat
                label="Swap audit"
                value={freshness.swap_audit_latest_generated_at?.slice(0, 10) || '—'}
                warn={isStaleDate(freshness.swap_audit_latest_generated_at, 3)}
              />
              <Stat
                label="Total return"
                value={freshness.total_return_latest_date || '—'}
                warn={isStaleDate(freshness.total_return_latest_date, 7)}
              />
              <Stat
                label="REER"
                value={freshness.reer_latest_available_date || '—'}
                warn={isStaleDate(freshness.reer_latest_available_date, 90)}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Eligible pairs by aligned family</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2">
              {Object.entries(eligibleByFamily).map(([fam, n]) => (
                <Badge key={fam} variant="outline" className="font-mono">
                  {fam}: {n}
                </Badge>
              ))}
              {!Object.values(eligibleByFamily).some((n) => n > 0) && (
                <span className="text-xs text-muted-foreground">No eligible BUY/SELL signals</span>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Blocked / no-trade pairs</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {blockedPairs.length === 0 ? (
                <TabEmpty message="No blocked pairs on latest as-of date." />
              ) : (
                blockedPairs.map((bp) => (
                  <div key={bp.symbol} className="rounded border border-border/60 p-2 text-xs">
                    <span className="font-mono font-semibold">{bp.symbol}</span>
                    <ul className="mt-1 text-muted-foreground list-disc pl-4">
                      {bp.reasons.map((r, i) => (
                        <li key={`${bp.symbol}-${i}`}>{r}</li>
                      ))}
                    </ul>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </TabShell>
  );
}
