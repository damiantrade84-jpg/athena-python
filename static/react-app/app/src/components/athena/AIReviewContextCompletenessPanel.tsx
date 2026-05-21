import { Badge } from '@/components/ui/badge';
import type {
  AIChartReviewAtrDiagnostics,
  AIChartReviewContextCompleteness,
  AIChartReviewFundingOi,
  AIChartReviewMissingContextDetailed,
  AIChartReviewResistanceMap,
} from '@/types/athena';

const STATUS_CLASS: Record<string, string> = {
  complete: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  partial: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  insufficient: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
};

function show(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined) return fallback;
  if (typeof value === 'string') return value.trim() === '' ? fallback : value;
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : fallback;
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  return String(value);
}

function fmtNum(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return value.toFixed(digits).replace(/\.?0+$/, '');
}

export interface AIReviewContextCompletenessPanelProps {
  completeness?: AIChartReviewContextCompleteness;
  detailed?: AIChartReviewMissingContextDetailed;
  fundingOi?: AIChartReviewFundingOi;
  atrDiagnostics?: AIChartReviewAtrDiagnostics;
  resistanceMap?: AIChartReviewResistanceMap;
}

export default function AIReviewContextCompletenessPanel({
  completeness,
  detailed,
  fundingOi,
  atrDiagnostics,
  resistanceMap,
}: AIReviewContextCompletenessPanelProps) {
  if (!completeness) return null;
  const status = completeness.status || 'unknown';
  const statusClass = STATUS_CLASS[status] ?? 'bg-zinc-500/15 text-zinc-300 border-zinc-500/40';
  const metadata = completeness.metadata || {};

  return (
    <div className="space-y-2 border border-border/50 rounded-md p-2 bg-muted/10">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
          Context completeness
        </span>
        <Badge className={`${statusClass} text-[10px] border`}>
          {status}
        </Badge>
        <span className="text-[10px] font-mono text-muted-foreground ml-auto">
          score {show(completeness.score)}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px]">
        <ContextList title="Required missing context" items={detailed?.required || []} />
        <ContextList title="Optional missing context" items={detailed?.optional || []} />
        <ContextList title="Not applicable context" items={detailed?.notApplicable || []} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[10px]">
        <div className="border border-border/40 rounded-md p-2 space-y-1">
          <div className="text-muted-foreground uppercase">Metadata</div>
          <KV label="chart captured" value={show(metadata.chartCapturedAt)} />
          <KV label="scan timestamp" value={show(metadata.scanTimestamp)} />
          <KV label="latest candle" value={show(metadata.latestCandleTimestamp)} />
          <KV label="chart provider" value={show(metadata.chartProvider, 'unknown')} />
          <KV label="engine provider" value={show(metadata.engineProvider, 'unknown')} />
          <KV label="provider mismatch" value={show(metadata.providerMismatch)} />
        </div>

        <div className="border border-border/40 rounded-md p-2 space-y-1">
          <div className="text-muted-foreground uppercase">Structured diagnostics</div>
          <KV label="funding rate" value={fmtNum(fundingOi?.fundingRate)} />
          <KV label="funding z" value={fmtNum(fundingOi?.fundingRateZ, 2)} />
          <KV label="open interest" value={fmtNum(fundingOi?.openInterest, 2)} />
          <KV label="OI delta pct" value={fmtNum(fundingOi?.openInterestDeltaPct, 2)} />
          <KV label="ATR D1 / H4 / chart" value={`${fmtNum(atrDiagnostics?.atrD1, 6)} / ${fmtNum(atrDiagnostics?.atrH4, 6)} / ${fmtNum(atrDiagnostics?.atrChartTf, 6)}`} />
          <KV label="nearest resistance" value={fmtNum(resistanceMap?.nearestResistance, 6)} />
          <KV label="TP clears resistance" value={show(resistanceMap?.tpClearsResistance)} />
        </div>
      </div>
    </div>
  );
}

function ContextList({
  title,
  items,
}: {
  title: string;
  items: Array<{ label: string; reason: string; impact?: string; blocksTrade?: boolean }>;
}) {
  return (
    <div className="border border-border/40 rounded-md p-2 min-h-[72px]">
      <div className="text-[10px] text-muted-foreground mb-1">{title}</div>
      {items.length === 0 ? (
        <div className="text-muted-foreground">—</div>
      ) : (
        <ul className="space-y-1">
          {items.map((item, i) => (
            <li key={`${item.label}-${i}`} className="leading-snug">
              <span className="font-medium">{item.label}</span>
              {item.impact && <span className="text-muted-foreground"> · {item.impact}</span>}
              {item.blocksTrade && <span className="text-rose-300"> · blocks trade</span>}
              <div className="text-muted-foreground">{item.reason}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="font-mono">
      <span className="text-muted-foreground">{label}: </span>
      <span>{value}</span>
    </div>
  );
}
