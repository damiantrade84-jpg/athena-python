import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { postForexRebalance } from '@/lib/forexFactorApi';
import {
  DiagnosticsList,
  TabEmpty,
  TabShell,
  useForexTabFetch,
} from './shared';

interface RebalancePlanRow {
  symbol?: string;
  action?: string;
  direction?: string | null;
  target_weight?: number;
}

interface RebalanceData {
  as_of_date?: string;
  plan?: RebalancePlanRow[];
  executed?: Array<{ symbol?: string; executed?: boolean; reason?: string }>;
  filled_count?: number;
  dry_run?: boolean;
  refresh?: Record<string, unknown>;
}

export default function ExecutionTab() {
  const [dryRun, setDryRun] = useState(true);
  const [lastResult, setLastResult] = useState<RebalanceData | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const { envelope, loading, error, diagnostics, refresh } = useForexTabFetch(
    () => postForexRebalance({ executeTrades: false }),
    [],
    60000,
  );

  const data = (lastResult || envelope?.data) as RebalanceData | undefined;
  const plan = data?.plan || [];

  const runRebalance = async (executeTrades: boolean) => {
    setBusy(true);
    setActionError(null);
    try {
      const resp = await postForexRebalance({
        executeTrades,
        dryRun: executeTrades ? dryRun : true,
      });
      setLastResult(resp.data as RebalanceData);
      refresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <TabShell
      loading={loading && !lastResult}
      error={error || actionError}
      diagnostics={diagnostics}
      warnings={envelope?.warnings}
      onRefresh={refresh}
    >
      <div className="space-y-3">
        <p className="text-[11px] text-muted-foreground">
          Demo MT5 execution via the same risk engine and guardian as Engine A/ASE.
          Requires EXECUTION_ENABLED and FX_FACTOR_EXECUTION_ENABLED in config.yaml.
        </p>

        <div className="flex flex-wrap gap-2 items-center">
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => runRebalance(false)}
          >
            Refresh plan
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={() => runRebalance(true)}
          >
            {dryRun ? 'Dry-run execute' : 'Execute on demo'}
          </Button>
          <label className="flex items-center gap-1.5 text-xs cursor-pointer">
            <Checkbox checked={dryRun} onCheckedChange={(v) => setDryRun(v === true)} />
            Dry run (no broker orders)
          </label>
          {data?.as_of_date && (
            <Badge variant="outline" className="text-[10px]">as_of {data.as_of_date}</Badge>
          )}
          {typeof data?.filled_count === 'number' && (
            <Badge className="text-[10px]">filled {data.filled_count}</Badge>
          )}
        </div>

        {!plan.length ? (
          <TabEmpty message="No rebalance plan — refresh decisions or check factor gates." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Direction</TableHead>
                <TableHead className="text-right">Weight</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {plan.map((row) => (
                <TableRow key={`${row.symbol}-${row.action}`}>
                  <TableCell className="font-mono text-xs">{row.symbol}</TableCell>
                  <TableCell>{row.action}</TableCell>
                  <TableCell>{row.direction || '—'}</TableCell>
                  <TableCell className="text-right font-mono text-xs">
                    {row.target_weight != null ? row.target_weight.toFixed(4) : '—'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        {data?.executed?.length ? (
          <div className="space-y-1">
            <p className="text-xs font-medium">Execution results</p>
            <DiagnosticsList
              items={data.executed.map((r) => ({
                code: r.symbol,
                message: `${r.executed ? 'OK' : 'SKIP'}: ${r.reason}`,
                severity: r.executed ? 'info' : 'warning',
              }))}
            />
          </div>
        ) : null}
      </div>
    </TabShell>
  );
}
