import { Badge } from '@/components/ui/badge';
import {
  AI_REVIEW_EMPTY,
  AI_REVIEW_SEP,
  fmtReviewNum,
  showReviewValue,
} from '@/lib/aiReviewDisplay';
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
  const c = completeness ?? {
    score: null,
    status: 'unknown',
    missingRequired: [],
    missingOptional: [],
    notApplicable: [],
    metadata: {},
  };
  const status = c.status || 'unknown';
  const statusClass = STATUS_CLASS[status] ?? 'bg-zinc-500/15 text-zinc-300 border-zinc-500/40';
  const metadata = c.metadata || {};
  const requiredItems = detailed?.required || [];
  const optionalItems = detailed?.optional || [];
  const notApplicableItems = detailed?.notApplicable || [];
  const showNotApplicableNote =
    requiredItems.length === 0 &&
    optionalItems.length === 0 &&
    notApplicableItems.length > 0;

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
          score {showReviewValue(c.score)}
        </span>
      </div>

      {showNotApplicableNote && (
        <div className="text-[10px] text-muted-foreground">
          Some context fields are not applicable for this asset or chart surface.
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px]">
        <ContextList title="Required missing context" items={requiredItems} />
        <ContextList title="Optional missing context" items={optionalItems} />
        <ContextList title="Not applicable context" items={notApplicableItems} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[10px]">
        <div className="border border-border/40 rounded-md p-2 space-y-1">
          <div className="text-muted-foreground uppercase">Metadata</div>
          <KV label="chart captured" value={showReviewValue(metadata.chartCapturedAt)} />
          <KV label="scan timestamp" value={showReviewValue(metadata.scanTimestamp)} />
          <KV label="latest candle" value={showReviewValue(metadata.latestCandleTimestamp)} />
          <KV label="chart provider" value={showReviewValue(metadata.chartProvider, 'unknown')} />
          <KV label="engine provider" value={showReviewValue(metadata.engineProvider, 'unknown')} />
          <KV label="provider mismatch" value={showReviewValue(metadata.providerMismatch)} />
        </div>

        <div className="border border-border/40 rounded-md p-2 space-y-1">
          <div className="text-muted-foreground uppercase">Structured diagnostics</div>
          <KV label="funding rate" value={fmtReviewNum(fundingOi?.fundingRate, 4)} />
          <KV label="funding z" value={fmtReviewNum(fundingOi?.fundingRateZ, 2)} />
          <KV label="open interest" value={fmtReviewNum(fundingOi?.openInterest, 2)} />
          <KV label="OI delta pct" value={fmtReviewNum(fundingOi?.openInterestDeltaPct, 2)} />
          <KV
            label="ATR D1 / H4 / chart"
            value={`${fmtReviewNum(atrDiagnostics?.atrD1, 6)} / ${fmtReviewNum(atrDiagnostics?.atrH4, 6)} / ${fmtReviewNum(atrDiagnostics?.atrChartTf, 6)}`}
          />
          <KV label="nearest resistance" value={fmtReviewNum(resistanceMap?.nearestResistance, 6)} />
          <KV label="TP clears resistance" value={showReviewValue(resistanceMap?.tpClearsResistance)} />
          {resistanceMap?.profileLevelsTrusted === false ? (
            <KV label="VP POC / VAH / VAL" value="N/A — not used for this asset class" />
          ) : (
            <KV
              label="VP POC / VAH / VAL"
              value={`${fmtReviewNum(resistanceMap?.profileLevels?.poc, 6)} / ${fmtReviewNum(resistanceMap?.profileLevels?.vah, 6)} / ${fmtReviewNum(resistanceMap?.profileLevels?.val, 6)}`}
            />
          )}
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
        <div className="text-muted-foreground">{AI_REVIEW_EMPTY}</div>
      ) : (
        <ul className="space-y-1">
          {items.map((item, i) => (
            <li key={`${item.label}-${i}`} className="leading-snug">
              <span className="font-medium">{item.label}</span>
              {item.impact && (
                <span className="text-muted-foreground">
                  {AI_REVIEW_SEP}
                  {item.impact}
                </span>
              )}
              {item.blocksTrade && (
                <span className="text-rose-300">
                  {AI_REVIEW_SEP}
                  blocks trade
                </span>
              )}
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
