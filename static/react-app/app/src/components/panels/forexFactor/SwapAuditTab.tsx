import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { AlertTriangle, ChevronDown } from 'lucide-react';
import { cn, fmtNum } from '@/lib/utils';
import { getSwapAudit, type SwapAuditRow } from '@/lib/forexFactorApi';
import {
  DiagnosticsList,
  TabEmpty,
  TabShell,
  useForexTabFetch,
} from './shared';

function rowWarn(row: SwapAuditRow): 'red' | 'amber' | null {
  if (row.markup_warning || row.conversion_warning || row.stale_swap_warning) return 'amber';
  if (row.status && row.status !== 'ok') return 'red';
  return null;
}

export default function SwapAuditTab() {
  const [expanded, setExpanded] = useState<string | null>(null);
  const { envelope, loading, error, diagnostics, refresh } = useForexTabFetch(
    () => getSwapAudit(),
    [],
    60000,
  );

  const rows = envelope?.data?.rows || [];

  return (
    <TabShell
      loading={loading}
      error={error}
      diagnostics={diagnostics}
      warnings={envelope?.warnings}
      onRefresh={refresh}
    >
      {rows.length === 0 ? (
        <TabEmpty message="No swap audit rows available." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>Symbol</TableHead>
              <TableHead>Swap L/S</TableHead>
              <TableHead>Ann. carry L/S</TableHead>
              <TableHead>Eligible L/S</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Warnings</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => {
              const warn = rowWarn(row);
              const key = row.symbol || 'unknown';
              return (
                <Collapsible
                  key={key}
                  open={expanded === key}
                  onOpenChange={(o) => setExpanded(o ? key : null)}
                  asChild
                >
                  <>
                    <TableRow
                      className={cn(
                        warn === 'red' && 'bg-destructive/10',
                        warn === 'amber' && 'bg-amber-500/10',
                      )}
                    >
                      <TableCell>
                        <CollapsibleTrigger asChild>
                          <Button variant="ghost" size="sm" className="h-6 w-6 p-0">
                            <ChevronDown className={`h-4 w-4 ${expanded === key ? 'rotate-180' : ''}`} />
                          </Button>
                        </CollapsibleTrigger>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{row.symbol}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {fmtNum(row.swap_long, 4)} / {fmtNum(row.swap_short, 4)}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {fmtNum(row.annualized_swap_return_long, 2)}% / {fmtNum(row.annualized_swap_return_short, 2)}%
                      </TableCell>
                      <TableCell className="text-xs">
                        {row.carry_long_eligible ? 'L✓' : 'L✗'} / {row.carry_short_eligible ? 'S✓' : 'S✗'}
                      </TableCell>
                      <TableCell>
                        <Badge className={row.status === 'ok' ? 'badge-long' : 'badge-short'}>
                          {row.status || '—'}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {(warn || row.markup_warning || row.conversion_warning || row.stale_swap_warning) && (
                          <AlertTriangle className={cn('h-4 w-4', warn === 'red' ? 'text-destructive' : 'text-amber-500')} />
                        )}
                      </TableCell>
                    </TableRow>
                    <CollapsibleContent asChild>
                      <TableRow className="bg-muted/20">
                        <TableCell colSpan={7} className="p-3 text-xs font-mono space-y-1">
                          <div>Mode: {row.swap_mode ?? '—'} · Theoretical carry: {fmtNum(row.theoretical_carry, 4, '—')}</div>
                          <div>Markup L/S: {fmtNum(row.broker_markup_long, 4, '—')} / {fmtNum(row.broker_markup_short, 4, '—')}</div>
                          <DiagnosticsList items={row.diagnostics} />
                        </TableCell>
                      </TableRow>
                    </CollapsibleContent>
                  </>
                </Collapsible>
              );
            })}
          </TableBody>
        </Table>
      )}
    </TabShell>
  );
}
