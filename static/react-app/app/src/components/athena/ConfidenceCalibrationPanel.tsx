// ConfidenceCalibrationPanel — Phase 6 advisory calibration chart.
//
// Uses SWR for request deduplication. No-op when server gate is off or bins
// are empty. Advisory-only; never auto-applies findings.

import { memo } from 'react';
import useSWR from 'swr';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Badge } from '@/components/ui/badge';
import { getCalibration } from '@/lib/apiClient';

interface ConfidenceCalibrationPanelProps {
  surface?: string;
  lookbackDays?: number;
}

function ConfidenceCalibrationPanelImpl({
  surface = 'marcus',
  lookbackDays = 30,
}: ConfidenceCalibrationPanelProps) {
  const { data, error, isLoading } = useSWR(
    ['calibration', surface, lookbackDays],
    () => getCalibration(surface, lookbackDays),
    { revalidateOnFocus: false, dedupingInterval: 60_000 },
  );

  if (isLoading) return null;
  if (error || !data?.enabled || !data.bins?.length) return null;

  const chartData = data.bins.map((b) => ({
    bin: b.bin,
    avg_confidence: b.avg_confidence ?? 0,
    realized_accuracy: b.realized_accuracy ?? 0,
    gap: b.gap ?? 0,
    count: b.count,
  }));

  const hasSamples = chartData.some((b) => b.count > 0);
  if (!hasSamples) return null;

  return (
    <div className="rounded-md border border-border/50 bg-card/40 p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          AI confidence calibration
        </span>
        <Badge className="text-[9px] border border-amber-500/40 text-amber-300 bg-amber-500/10">
          advisory only
        </Badge>
        <span className="text-[9px] text-muted-foreground ml-auto">
          {surface} · {lookbackDays}d
        </span>
      </div>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
          <XAxis dataKey="bin" tick={{ fontSize: 9 }} />
          <YAxis domain={[0, 1]} tick={{ fontSize: 9 }} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
          <Tooltip
            contentStyle={{ fontSize: 10, background: '#1e293b', border: '1px solid #334155' }}
            formatter={(value: number, name: string) => [`${Math.round(value * 100)}%`, name]}
          />
          <Legend wrapperStyle={{ fontSize: 9 }} />
          <Bar dataKey="avg_confidence" name="Avg confidence" fill="#f59e0b" radius={[2, 2, 0, 0]} />
          <Bar dataKey="realized_accuracy" name="Realized accuracy" fill="#10b981" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

const ConfidenceCalibrationPanel = memo(ConfidenceCalibrationPanelImpl);
export default ConfidenceCalibrationPanel;
