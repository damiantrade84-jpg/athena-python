import { Badge } from '@/components/ui/badge';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { getVolCostGates } from '@/lib/forexFactorApi';
import { gateResultClass, TabEmpty, TabShell, useForexTabFetch } from './shared';

export default function VolCostGatesTab() {
  const { envelope, loading, error, diagnostics, refresh } = useForexTabFetch(
    () => getVolCostGates(),
    [],
    30000,
  );

  const data = envelope?.data;
  const symbols = data?.symbols || [];

  return (
    <TabShell loading={loading} error={error} diagnostics={diagnostics} warnings={envelope?.warnings} onRefresh={refresh}>
      <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs mb-3">
        <span className="font-semibold text-destructive">Hard gates — not part of score.</span>
        {' '}Volatility and cost gates can block or reduce size independently of factor alignment.
      </div>

      {symbols.length === 0 ? (
        <TabEmpty message="No gate data on latest as-of date." />
      ) : (
        <div className="space-y-4">
          {symbols.map((sym) => (
            <div key={sym.symbol} className="rounded border border-border/60 overflow-hidden">
              <div className="px-3 py-1.5 bg-muted/30 flex items-center gap-2 text-xs">
                <span className="font-mono font-semibold">{sym.symbol}</span>
                <Badge variant="outline">{sym.volatility_action || '—'}</Badge>
              </div>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Gate</TableHead>
                    <TableHead>Result</TableHead>
                    <TableHead>Reason</TableHead>
                    <TableHead>Action</TableHead>
                    <TableHead>Size mult</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(sym.gate_results || []).map((g, i) => (
                    <TableRow key={`${sym.symbol}-${g.gate_name}-${i}`}>
                      <TableCell className="text-xs">{g.gate_name}</TableCell>
                      <TableCell>
                        <Badge className={gateResultClass(g.result)}>{g.result || '—'}</Badge>
                      </TableCell>
                      <TableCell className="text-xs font-mono">{g.reason_code || '—'}</TableCell>
                      <TableCell className="text-xs">{g.recommended_action || '—'}</TableCell>
                      <TableCell className="font-mono text-xs">{g.size_multiplier ?? '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {sym.cost_diagnostics && Object.keys(sym.cost_diagnostics).length > 0 && (
                <pre className="text-[10px] p-2 bg-muted/20 overflow-auto">
                  {JSON.stringify(sym.cost_diagnostics, null, 2)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </TabShell>
  );
}
