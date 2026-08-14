import { Badge } from '@/components/ui/badge';
import {
  AI_REVIEW_EMPTY,
  AI_REVIEW_SEP,
  fmtReviewPass,
  fmtReviewBool,
  showReviewValue,
} from '@/lib/aiReviewDisplay';
import type {
  AIChartReviewEngineAVerdictComparison,
  AIChartReviewEngineBVerdictComparison,
} from '@/types/athena';

const VERDICT_CLASS: Record<string, string> = {
  engine_a_confirmed: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  engine_a_direction_confirmed_entry_rejected:
    'bg-amber-500/15 text-amber-300 border-amber-500/40',
  engine_a_direction_confirmed_wait:
    'bg-sky-500/15 text-sky-300 border-sky-500/40',
  engine_a_contradicted: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
  engine_a_missing: 'bg-zinc-500/15 text-zinc-300 border-zinc-500/40',
  engine_b_confirmed: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  engine_b_direction_confirmed_entry_rejected:
    'bg-amber-500/15 text-amber-300 border-amber-500/40',
  engine_b_direction_confirmed_wait:
    'bg-sky-500/15 text-sky-300 border-sky-500/40',
  engine_b_contradicted: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
  engine_b_missing: 'bg-zinc-500/15 text-zinc-300 border-zinc-500/40',
  mixed: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  unknown: 'bg-zinc-500/15 text-zinc-300 border-zinc-500/40',
};

function fmtScoreLineA(v: AIChartReviewEngineAVerdictComparison): string {
  const score = v.engineAScore;
  const max = v.engineAMaxScore;
  const threshold = v.engineAThreshold;
  const scoreText =
    score == null && max == null
      ? AI_REVIEW_EMPTY
      : `${score ?? AI_REVIEW_EMPTY} / ${max ?? AI_REVIEW_EMPTY}${AI_REVIEW_SEP}threshold ${threshold ?? AI_REVIEW_EMPTY}`;
  return `${scoreText}${AI_REVIEW_SEP}${fmtReviewPass(v.engineAPassed)}${AI_REVIEW_SEP}${showReviewValue(v.engineADirection)}`;
}

function fmtScoreLineB(v: AIChartReviewEngineBVerdictComparison): string {
  const score = v.engineBScore;
  const max = v.engineBMaxScore;
  const threshold = v.engineBThreshold;
  const scoreText =
    score == null && max == null
      ? AI_REVIEW_EMPTY
      : `${score ?? AI_REVIEW_EMPTY} / ${max ?? AI_REVIEW_EMPTY}${AI_REVIEW_SEP}threshold ${threshold ?? AI_REVIEW_EMPTY}`;
  return `${scoreText}${AI_REVIEW_SEP}${fmtReviewPass(v.engineBPassed)}${AI_REVIEW_SEP}${showReviewValue(v.engineBDirection)}`;
}

export interface AIReviewEngineAVerdictPanelProps {
  comparison?: AIChartReviewEngineAVerdictComparison | AIChartReviewEngineBVerdictComparison | null;
  primaryEngine?: 'A' | 'B';
}

export default function AIReviewEngineAVerdictPanel({
  comparison,
  primaryEngine = 'A',
}: AIReviewEngineAVerdictPanelProps) {
  const isB = primaryEngine === 'B';
  const v = comparison ?? ({} as AIChartReviewEngineAVerdictComparison);
  const verdict = v.comparisonVerdict || 'unknown';
  const verdictClass = VERDICT_CLASS[verdict] ?? VERDICT_CLASS.unknown;
  const entryRejected =
    verdict === (isB ? 'engine_b_direction_confirmed_entry_rejected' : 'engine_a_direction_confirmed_entry_rejected')
    || (
      isB
        ? (v as AIChartReviewEngineBVerdictComparison).chartConfirmsEngineBDirection === true
          && (v as AIChartReviewEngineBVerdictComparison).chartContradictsEntryTiming === true
        : (v as AIChartReviewEngineAVerdictComparison).chartConfirmsEngineADirection === true
          && (v as AIChartReviewEngineAVerdictComparison).chartContradictsEntryTiming === true
    );

  return (
    <div className="space-y-2 border border-border/50 rounded-md p-2 bg-muted/10">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
          {isB ? 'Engine B vs AI verdict' : 'Engine A vs AI verdict'}
        </span>
        <Badge className={`${verdictClass} text-[10px] border`}>
          {verdict.replace(/_/g, ' ')}
        </Badge>
        {(isB
          ? (v as AIChartReviewEngineBVerdictComparison).aiDowngradedEngineB
          : (v as AIChartReviewEngineAVerdictComparison).aiDowngradedEngineA) && (
          <Badge variant="outline" className="text-[10px] border-amber-500/50 text-amber-300">
            {isB ? 'AI downgraded Engine B' : 'AI downgraded Engine A'}
          </Badge>
        )}
      </div>

      {entryRejected && v.finalReason && (
        <div className="text-[11px] border border-amber-500/30 rounded-md px-2 py-1.5 text-amber-100/90 bg-amber-500/5">
          {v.finalReason}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5 text-[10px]">
        <VerdictKV
          label={isB ? 'Engine B' : 'Engine A'}
          value={isB ? fmtScoreLineB(v as AIChartReviewEngineBVerdictComparison) : fmtScoreLineA(v as AIChartReviewEngineAVerdictComparison)}
        />
        <VerdictKV
          label="Chart confirms direction"
          value={fmtReviewBool(
            isB
              ? (v as AIChartReviewEngineBVerdictComparison).chartConfirmsEngineBDirection
              : (v as AIChartReviewEngineAVerdictComparison).chartConfirmsEngineADirection,
          )}
        />
        <VerdictKV label="Chart confirms entry timing" value={fmtReviewBool(v.chartConfirmsEntryTiming)} />
        <VerdictKV label="Chart contradicts entry timing" value={fmtReviewBool(v.chartContradictsEntryTiming)} />
        <VerdictKV
          label="Chart contradicts direction"
          value={fmtReviewBool(
            isB
              ? (v as AIChartReviewEngineBVerdictComparison).chartContradictsEngineBDirection
              : (v as AIChartReviewEngineAVerdictComparison).chartContradictsEngineADirection,
          )}
        />
        <VerdictKV
          label={isB ? 'AI agrees with Engine B' : 'AI agrees with Engine A'}
          value={fmtReviewBool(
            isB
              ? (v as AIChartReviewEngineBVerdictComparison).aiAgreesWithEngineB
              : (v as AIChartReviewEngineAVerdictComparison).aiAgreesWithEngineA,
          )}
        />
        <VerdictKV label="Final decision" value={showReviewValue(v.finalDecision, 'unknown')} />
        <VerdictKV label="Final reason" value={showReviewValue(v.finalReason)} />
      </div>

      {(v.downgradeReasons?.length ?? 0) > 0 && (
        <ReasonList title="Downgrade reasons" items={v.downgradeReasons!} />
      )}
      {(v.upgradeReasons?.length ?? 0) > 0 && (
        <ReasonList title="Upgrade reasons" items={v.upgradeReasons!} />
      )}
    </div>
  );
}

function VerdictKV({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-border/40 rounded-md px-2 py-1 min-w-0">
      <span className="text-muted-foreground">{label}: </span>
      <span className="break-words">{value}</span>
    </div>
  );
}

function ReasonList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="text-[11px]">
      <div className="text-[10px] text-muted-foreground mb-0.5">{title}</div>
      <ul className="list-disc list-inside space-y-0.5">
        {items.map((it, i) => (
          <li key={i}>{it}</li>
        ))}
      </ul>
    </div>
  );
}
