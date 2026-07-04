import { Badge } from '@/components/ui/badge';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { fmtNum } from '@/lib/utils';
import { getCotPositioning } from '@/lib/forexFactorApi';
import { percentileColor, TabEmpty, TabShell, useForexTabFetch } from './shared';

export default function CotPositioningTab() {
  const currencyFetch = useForexTabFetch(() => getCotPositioning(), [], 60000);

  const data = currencyFetch.envelope?.data;
  const currencies = data?.currencies || [];

  return (
    <TabShell
      loading={currencyFetch.loading}
      error={currencyFetch.error}
      diagnostics={currencyFetch.diagnostics}
      warnings={currencyFetch.envelope?.warnings}
      onRefresh={currencyFetch.refresh}
    >
      <p className="text-[11px] text-muted-foreground mb-2">
        Currency-level COT percentiles (G8). Pair overlay available via symbol query on server.
        {data?.as_of_date ? ` As-of: ${data.as_of_date}` : ''}
      </p>

      {currencies.length === 0 ? (
        <TabEmpty message="No COT positioning data available." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Currency</TableHead>
              <TableHead>Report date</TableHead>
              <TableHead>Net</TableHead>
              <TableHead>Percentile</TableHead>
              <TableHead>Weeks</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {currencies.map((row) => (
              <TableRow key={row.currency || Math.random()}>
                <TableCell className="font-mono font-semibold">{row.currency}</TableCell>
                <TableCell className="text-xs">{row.report_date || '—'}</TableCell>
                <TableCell className="font-mono text-xs">{fmtNum(row.net, 0, '—')}</TableCell>
                <TableCell className={`font-mono text-xs ${percentileColor(row.percentile)}`}>
                  {fmtNum(row.percentile, 1, '—')}%
                </TableCell>
                <TableCell className="text-xs">{row.n_weeks ?? '—'}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {data?.view === 'pair' && data.overlays?.length ? (
        <div className="mt-4 space-y-2">
          <h4 className="text-sm font-semibold">Pair overlay — {data.symbol}</h4>
          {data.overlays.map((ov) => {
            const overlay = ov.overlay || {};
            return (
              <div key={ov.direction} className="rounded border p-2 text-xs flex flex-wrap gap-2 items-center">
                <Badge variant="outline">{ov.direction}</Badge>
                <span className="font-mono">{JSON.stringify(overlay)}</span>
              </div>
            );
          })}
        </div>
      ) : null}
    </TabShell>
  );
}
