import { useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import { G8_PAIRS, getTotalReturn } from '@/lib/forexFactorApi';
import { TabEmpty, TabShell, useForexTabFetch } from './shared';

export default function TotalReturnTab() {
  const [symbol, setSymbol] = useState('EURUSD');

  const { envelope, loading, error, diagnostics, refresh } = useForexTabFetch(
    () => getTotalReturn({ symbol }),
    [symbol],
    0,
  );

  const rows = envelope?.data?.rows || [];
  const usesTotalReturn = envelope?.data?.production_momentum_uses_total_return;

  const chartData = useMemo(
    () => rows.map((r) => ({
      date: r.date,
      longIdx: r.long_total_return_index,
      shortIdx: r.short_total_return_index,
      quality: r.data_quality_status,
    })),
    [rows],
  );

  const hasDegraded = rows.some(
    (r) => r.data_quality_status && r.data_quality_status !== 'ok',
  );

  return (
    <TabShell loading={loading} error={error} diagnostics={diagnostics} warnings={envelope?.warnings} onRefresh={refresh}>
      <div className="flex items-center gap-3 mb-3">
        <Select value={symbol} onValueChange={setSymbol}>
          <SelectTrigger className="w-[140px] h-8 text-xs font-mono"><SelectValue /></SelectTrigger>
          <SelectContent>
            {G8_PAIRS.map((p) => (
              <SelectItem key={p} value={p} className="font-mono text-xs">{p}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        {usesTotalReturn && (
          <span className="text-[11px] text-muted-foreground">
            Production momentum uses total-return series
          </span>
        )}
      </div>

      {hasDegraded && (
        <div className="flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200 mb-3">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          Some rows are degraded or spot-only — interpret momentum signals with caution.
        </div>
      )}

      {chartData.length === 0 ? (
        <TabEmpty message={`No total-return data for ${symbol}.`} />
      ) : (
        <div className="h-[320px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData}>
              <XAxis dataKey="date" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
              <Tooltip contentStyle={{ fontSize: 11 }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Line type="monotone" dataKey="longIdx" name="Long TR index" stroke="hsl(var(--long))" dot={false} strokeWidth={1.5} />
              <Line type="monotone" dataKey="shortIdx" name="Short TR index" stroke="hsl(var(--short))" dot={false} strokeWidth={1.5} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </TabShell>
  );
}
