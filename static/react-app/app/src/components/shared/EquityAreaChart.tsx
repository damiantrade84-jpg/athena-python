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

const TOOLTIP_STYLE: React.CSSProperties = {
  background: 'hsl(var(--popover))',
  border: '1px solid hsl(var(--border))',
  borderRadius: '6px',
  boxShadow: '0 4px 16px rgb(0 0 0 / 0.5)',
  fontSize: 11,
  color: 'hsl(var(--foreground))',
  padding: '6px 8px',
};

/**
 * Shared equity curve.
 *
 * Single series, so no legend — the panel title names it. Deliberately
 * plain per the house chart rules: one 2px stroke in a single accent hue
 * (the previous three-stop indigo→violet→azure gradient implied a colour
 * encoding that carried no data), a low-opacity fill for area weight only,
 * no drop-shadow glow filter, and recessive gridlines. The crosshair
 * tooltip is the interaction layer and is always on.
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
  const fillId = `eqFill${idSuffix}`;

  return (
    <div style={{ width: '100%', height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
          <defs>
            <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="hsl(var(--chart-1))" stopOpacity={0.22} />
              <stop offset="100%" stopColor="hsl(var(--chart-1))" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="2 4" vertical={false} />
          <XAxis
            dataKey={xKey}
            tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
            axisLine={false}
            tickLine={false}
            minTickGap={24}
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
            cursor={{ stroke: 'hsl(var(--muted-foreground))', strokeWidth: 1, strokeDasharray: '3 3' }}
            contentStyle={TOOLTIP_STYLE}
            labelStyle={{ color: 'hsl(var(--muted-foreground))', marginBottom: 2 }}
            itemStyle={{ color: 'hsl(var(--foreground))' }}
            formatter={(v: number) => [valueFormatter(v), valueLabel]}
            {...(labelFormatter ? { labelFormatter } : {})}
          />
          <Area
            type="monotone"
            dataKey={dataKey}
            stroke="hsl(var(--chart-1))"
            strokeWidth={2}
            fill={`url(#${fillId})`}
            dot={false}
            activeDot={{
              r: 4,
              fill: 'hsl(var(--chart-1))',
              // 2px surface ring keeps the marker legible over the fill
              stroke: 'hsl(var(--card))',
              strokeWidth: 2,
            }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
