import {
  AreaChart,
  Area,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  CartesianGrid,
  ReferenceLine,
} from 'recharts';

export interface EquityPoint {
  idx?: number;
  equity?: number;
  [key: string]: unknown;
}

interface EquityAreaChartProps {
  data: EquityPoint[];
  dataKey?: string;
  height?: number;
  xKey?: string;
  /** Renders the value column label + formatting in the tooltip. */
  valueFormatter?: (v: number) => string;
  valueLabel?: string;
  labelFormatter?: (label: unknown) => string;
  /** Baseline for the dashed reference line (defaults to 0). */
  baseline?: number;
  /** Stable id so multiple charts on one page don't share gradient ids. */
  idSuffix?: string;
}

const DEFAULT_TOOLTIP_STYLE: React.CSSProperties = {
  background: 'hsl(var(--popover))',
  border: '1px solid hsl(var(--gold) / 0.35)',
  borderRadius: '0.6rem',
  boxShadow: '0 8px 24px hsl(250 60% 3% / 0.6), 0 0 12px hsl(var(--gold) / 0.12)',
  fontSize: 11,
  color: 'hsl(var(--foreground))',
};

/**
 * Shared premium area chart used for equity curves across Athena panels.
 * Indigo→violet→azure gradient stroke with a soft glow, layered gradient
 * fill, subtle dashed grid, baseline reference line, and a glass tooltip.
 * Presentation-only — renders whatever data the caller provides.
 */
export default function EquityAreaChart({
  data,
  dataKey = 'equity',
  height = 260,
  xKey = 'idx',
  valueFormatter = (v) => (Number.isFinite(v) ? v.toFixed(2) : '—'),
  valueLabel = 'Equity',
  labelFormatter,
  baseline = 0,
  idSuffix = '',
}: EquityAreaChartProps) {
  const strokeId = `eqStroke${idSuffix}`;
  const fillId = `eqFill${idSuffix}`;
  const glowId = `eqGlow${idSuffix}`;

  return (
    <div style={{ width: '100%', height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ left: 4, right: 8, top: 8, bottom: 0 }}>
          <defs>
            <linearGradient id={strokeId} x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor="hsl(250 72% 62%)" />
              <stop offset="55%" stopColor="hsl(258 90% 70%)" />
              <stop offset="100%" stopColor="hsl(199 92% 64%)" />
            </linearGradient>
            <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="hsl(258 90% 68%)" stopOpacity={0.42} />
              <stop offset="60%" stopColor="hsl(258 90% 68%)" stopOpacity={0.10} />
              <stop offset="100%" stopColor="hsl(258 90% 68%)" stopOpacity={0} />
            </linearGradient>
            <filter id={glowId} x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>
          <CartesianGrid stroke="hsl(var(--border) / 0.5)" strokeDasharray="3 6" vertical={false} />
          <XAxis
            dataKey={xKey}
            tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            domain={['auto', 'auto']}
            tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
            axisLine={false}
            tickLine={false}
            width={52}
          />
          <ReferenceLine y={baseline} stroke="hsl(var(--border))" strokeDasharray="2 4" />
          <Tooltip
            cursor={{ stroke: 'hsl(var(--gold) / 0.4)', strokeWidth: 1, strokeDasharray: '4 4' }}
            contentStyle={DEFAULT_TOOLTIP_STYLE}
            labelStyle={{ color: 'hsl(var(--muted-foreground))' }}
            formatter={(v: number) => [valueFormatter(v), valueLabel]}
            {...(labelFormatter ? { labelFormatter } : {})}
          />
          <Area
            type="monotone"
            dataKey={dataKey}
            stroke={`url(#${strokeId})`}
            strokeWidth={2.5}
            fill={`url(#${fillId})`}
            dot={false}
            activeDot={{ r: 4, fill: 'hsl(var(--gold-light))', stroke: 'hsl(var(--gold))', strokeWidth: 2 }}
            style={{ filter: `url(#${glowId})` }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
