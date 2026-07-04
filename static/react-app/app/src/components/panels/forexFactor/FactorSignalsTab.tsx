import { useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { ChevronDown } from 'lucide-react';
import { fmtNum } from '@/lib/utils';
import {
  getFactorSignals,
  signalFamilyScores,
  type FactorSignalRow,
} from '@/lib/forexFactorApi';
import {
  actionBadgeClass,
  DiagnosticsList,
  TabEmpty,
  TabShell,
  useForexTabFetch,
} from './shared';

const FAMILIES = ['', 'carry', 'momentum', 'value'];
const DIRECTIONS = ['', 'LONG', 'SHORT'];

export default function FactorSignalsTab() {
  const [family, setFamily] = useState('');
  const [direction, setDirection] = useState('');
  const [eligibleOnly, setEligibleOnly] = useState(false);
  const [blockedOnly, setBlockedOnly] = useState(false);
  const [symbolFilter, setSymbolFilter] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);

  const fetchParams = useMemo(() => ({
    family: family || undefined,
    direction: direction || undefined,
    eligible_only: eligibleOnly || undefined,
    blocked_only: blockedOnly || undefined,
    symbol: symbolFilter.trim().toUpperCase() || undefined,
  }), [family, direction, eligibleOnly, blockedOnly, symbolFilter]);

  const { envelope, loading, error, diagnostics, refresh } = useForexTabFetch(
    () => getFactorSignals(fetchParams),
    [fetchParams.family, fetchParams.direction, fetchParams.eligible_only, fetchParams.blocked_only, fetchParams.symbol],
    30000,
  );

  const signals = envelope?.data?.signals || [];
  const asOf = envelope?.data?.as_of_date;

  return (
    <TabShell
      loading={loading}
      error={error}
      diagnostics={diagnostics}
      warnings={envelope?.warnings}
      onRefresh={refresh}
    >
      <div className="flex flex-wrap gap-2 items-center mb-3">
        <Select value={family || 'all'} onValueChange={(v) => setFamily(v === 'all' ? '' : v)}>
          <SelectTrigger className="w-[130px] h-8 text-xs"><SelectValue placeholder="Family" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All families</SelectItem>
            {FAMILIES.filter(Boolean).map((f) => (
              <SelectItem key={f} value={f}>{f}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={direction || 'all'} onValueChange={(v) => setDirection(v === 'all' ? '' : v)}>
          <SelectTrigger className="w-[120px] h-8 text-xs"><SelectValue placeholder="Direction" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All dirs</SelectItem>
            {DIRECTIONS.filter(Boolean).map((d) => (
              <SelectItem key={d} value={d}>{d}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          className="w-[120px] h-8 text-xs font-mono"
          placeholder="Symbol"
          value={symbolFilter}
          onChange={(e) => setSymbolFilter(e.target.value)}
        />
        <Button
          variant={eligibleOnly ? 'default' : 'outline'}
          size="sm"
          className="h-8 text-xs"
          onClick={() => { setEligibleOnly(!eligibleOnly); setBlockedOnly(false); }}
        >
          Eligible only
        </Button>
        <Button
          variant={blockedOnly ? 'default' : 'outline'}
          size="sm"
          className="h-8 text-xs"
          onClick={() => { setBlockedOnly(!blockedOnly); setEligibleOnly(false); }}
        >
          Blocked only
        </Button>
        {asOf && <span className="text-[10px] text-muted-foreground ml-auto">As-of: {asOf}</span>}
      </div>

      {signals.length === 0 ? (
        <TabEmpty message="No factor signals for current filters." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>Symbol</TableHead>
              <TableHead>Dir</TableHead>
              <TableHead>Carry</TableHead>
              <TableHead>Mom</TableHead>
              <TableHead>Value</TableHead>
              <TableHead>Aligned</TableHead>
              <TableHead>COT</TableHead>
              <TableHead>Action</TableHead>
              <TableHead>Reason</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {signals.map((row: FactorSignalRow) => {
              const scores = signalFamilyScores(row);
              const key = `${row.symbol}-${row.direction}-${row.as_of_date}`;
              const cot = row.cot_overlay as Record<string, unknown> | undefined;
              return (
                <Collapsible key={key} open={expanded === key} onOpenChange={(o) => setExpanded(o ? key : null)} asChild>
                  <>
                    <TableRow>
                      <TableCell>
                        <CollapsibleTrigger asChild>
                          <Button variant="ghost" size="sm" className="h-6 w-6 p-0">
                            <ChevronDown className={`h-4 w-4 transition-transform ${expanded === key ? 'rotate-180' : ''}`} />
                          </Button>
                        </CollapsibleTrigger>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{row.symbol}</TableCell>
                      <TableCell className="text-xs">{row.direction || '—'}</TableCell>
                      {(['carry', 'momentum', 'value'] as const).map((fam) => (
                        <TableCell key={fam} className="font-mono text-xs">
                          {fmtNum(scores[fam]?.score, 1, '—')}
                        </TableCell>
                      ))}
                      <TableCell>
                        <div className="flex flex-wrap gap-0.5">
                          {(row.aligned_families || []).map((f) => (
                            <Badge key={f} variant="outline" className="text-[9px] h-4">{f}</Badge>
                          ))}
                        </div>
                      </TableCell>
                      <TableCell className="text-xs">{String(cot?.action ?? cot?.status ?? '—')}</TableCell>
                      <TableCell>
                        <Badge className={actionBadgeClass(row.action)}>{row.action || '—'}</Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground max-w-[180px] truncate">{row.reason || '—'}</TableCell>
                    </TableRow>
                    <CollapsibleContent asChild>
                      <TableRow className="bg-muted/20">
                        <TableCell colSpan={10} className="p-3 text-xs space-y-2">
                          <div className="grid grid-cols-3 gap-3">
                            {(['carry', 'momentum', 'value'] as const).map((fam) => {
                              const s = scores[fam];
                              return (
                                <div key={fam} className="rounded border p-2">
                                  <div className="font-semibold uppercase text-[10px]">{fam}</div>
                                  <div className="font-mono">score {fmtNum(s?.score, 2)} · dir {s?.direction || '—'}</div>
                                  <div className="text-muted-foreground">pts {fmtNum(s?.points, 2, '—')} · {s?.status || '—'}</div>
                                </div>
                              );
                            })}
                          </div>
                          {row.gate_summary && (
                            <div>
                              <span className="font-semibold">Gates: </span>
                              <span className="font-mono">{JSON.stringify(row.gate_summary.gates || {})}</span>
                            </div>
                          )}
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
