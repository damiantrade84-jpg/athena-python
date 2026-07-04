import { Badge } from '@/components/ui/badge';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { AlertTriangle } from 'lucide-react';
import { fmtNum } from '@/lib/utils';
import { getReerValue } from '@/lib/forexFactorApi';
import { TabEmpty, TabShell, useForexTabFetch } from './shared';

export default function ReerValueTab() {
  const { envelope, loading, error, diagnostics, refresh } = useForexTabFetch(
    () => getReerValue(),
    [],
    60000,
  );

  const currencies = envelope?.data?.currencies || [];

  return (
    <TabShell loading={loading} error={error} diagnostics={diagnostics} warnings={envelope?.warnings} onRefresh={refresh}>
      {currencies.length === 0 ? (
        <TabEmpty message="No REER / value data available." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Currency</TableHead>
              <TableHead>Z-score</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Obs. month</TableHead>
              <TableHead>Available</TableHead>
              <TableHead>Lag method</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {currencies.map((row) => {
              const z = row.z_score ?? row.z;
              const conservative = (row.lag_method || '').toLowerCase().includes('conservative');
              return (
                <TableRow key={row.currency}>
                  <TableCell className="font-mono font-semibold">{row.currency}</TableCell>
                  <TableCell className="font-mono text-xs">{fmtNum(z, 2, '—')}</TableCell>
                  <TableCell className="text-xs">{row.status_label || '—'}</TableCell>
                  <TableCell className="text-xs">{row.observation_month || '—'}</TableCell>
                  <TableCell className="text-xs">{row.available_date || '—'}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className="text-[9px]">
                      {row.lag_method || '—'}
                      {conservative && <AlertTriangle className="inline h-3 w-3 ml-1 text-amber-500" />}
                    </Badge>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </TabShell>
  );
}
