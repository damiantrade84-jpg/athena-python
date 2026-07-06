import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { fmtNum } from '@/lib/utils';
import { getValidationLatest } from '@/lib/forexFactorApi';
import { Stat, TabEmpty, TabShell, useForexTabFetch, ValidationDemoBanner, fxFamilyTradeCounts, fxValidationPass } from './shared';

export default function ValidationTab() {
  const { envelope, loading, error, diagnostics, refresh } = useForexTabFetch(
    () => getValidationLatest(),
    [],
    60000,
  );

  const data = envelope?.data;
  const validation = (data?.validation || {}) as Record<string, unknown>;
  const summary = (data?.summary || {}) as Record<string, unknown>;
  const pairSanity = (data?.pair_sanity_flags || {}) as Record<string, unknown>;

  const n = Number(validation.n ?? validation.sample_size ?? summary.trade_count ?? summary.n ?? NaN);
  const lowSample = Number.isFinite(n) && n < 30;

  const familyCounts = fxFamilyTradeCounts(validation);
  const validationPass = fxValidationPass(validation);
  const pairContrib = (validation.pair_contribution || pairSanity.pair_contribution || {}) as Record<string, number>;

  return (
    <TabShell loading={loading} error={error} diagnostics={diagnostics} warnings={envelope?.warnings} onRefresh={refresh}>
      {!data?.validation && !data?.summary ? (
        <TabEmpty message="No completed backtest validation available. Run a backtest first." />
      ) : (
        <div className="space-y-4">
          {data.run_id && (
            <p className="text-[11px] text-muted-foreground font-mono">Source run: {data.run_id}</p>
          )}

          <ValidationDemoBanner validation={validation} />

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                Sample size
                {lowSample && <Badge className="badge-neutral text-amber-500 border-amber-500/40">n &lt; 30</Badge>}
              </CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Stat label="Trades (n)" value={Number.isFinite(n) ? n : '—'} warn={lowSample} />
              <Stat label="Win rate" value={`${fmtNum(summary.win_rate ?? validation.win_rate, 1, '—')}%`} />
              <Stat label="Cost drag" value={fmtNum(summary.cost_drag ?? validation.cost_drag, 2, '—')} />
              <Stat label="Swap contrib." value={fmtNum(summary.swap_contribution ?? validation.swap_contribution, 2, '—')} />
            </CardContent>
          </Card>

          {Object.keys(familyCounts).length > 0 && (
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">Family trade counts</CardTitle></CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                {Object.entries(familyCounts).map(([fam, count]) => (
                  <Badge
                    key={fam}
                    variant="outline"
                    className={count < 30 ? 'border-amber-500/50 text-amber-400' : validationPass ? 'border-emerald-500/40 text-emerald-300' : ''}
                  >
                    {fam}: {count}
                  </Badge>
                ))}
              </CardContent>
            </Card>
          )}

          {Object.keys(pairContrib).length > 0 && (
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">Pair contribution</CardTitle></CardHeader>
              <CardContent className="space-y-1 text-xs font-mono">
                {Object.entries(pairContrib).map(([pair, pct]) => {
                  const val = typeof pct === 'number' ? pct : Number(pct);
                  const high = Number.isFinite(val) && val > 40;
                  return (
                    <div key={pair} className={high ? 'text-destructive' : ''}>
                      {pair}: {fmtNum(val, 1)}%{high ? ' ⚠ >40%' : ''}
                    </div>
                  );
                })}
              </CardContent>
            </Card>
          )}

          {(validation.score_distribution != null || validation.candidate_thresholds != null) && (
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">Score distribution</CardTitle></CardHeader>
              <CardContent>
                <pre className="text-[10px] bg-muted/30 rounded p-2 overflow-auto max-h-48">
                  {JSON.stringify({
                    score_distribution: validation.score_distribution,
                    candidate_thresholds: validation.candidate_thresholds,
                  }, null, 2)}
                </pre>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </TabShell>
  );
}
