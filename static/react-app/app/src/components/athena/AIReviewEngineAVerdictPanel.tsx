import { Badge } from '@/components/ui/badge';
import type { AIChartReviewEngineAVerdictComparison } from '@/types/athena';

const VERDICT_CLASS: Record<string, string> = {
  engine_a_confirmed: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  engine_a_direction_confirmed_entry_rejected:
    'bg-amber-500/15 text-amber-300 border-amber-500/40',
  engine_a_contradicted: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
  engine_a_missing: 'bg-zinc-500/15 text-zinc-300 border-zinc-500/40',
  mixed: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  unknown: 'bg-zinc-500/15 text-zinc-300 border-zinc-500/40',
};

function show(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined) return fallback;
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'string') return value.trim() === '' ? fallback : value;
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : fallback;
  return String(value);
}

function fmtBool(value: boolean | null | undefined): string {
  if (value === true) return 'yes';
  if (value === false) return 'no';
  return '—';
}

function fmtPass(passed: boolean | null | undefined): string {
  if (passed === true) return 'PASS';
  if (passed === false) return 'FAIL';
  return '—';
}

function fmtScoreLine(v: AIChartReviewEngineAVerdictComparison): string {
  const score = v.engineAScore;
  const max = v.engineAMaxScore;
  const threshold = v.engineAThreshold;
  const scoreText =
    score == null && max == null
      ? '—'
      : `${score ?? '—'} / ${max ?? '—'} · threshold ${threshold ?? '—'}`;
  return `${scoreText} · ${fmtPass(v.engineAPassed)} · ${show(v.engineADirection)}`;
}

export interface AIReviewEngineAVerdictPanelProps {
  comparison?: AIChartReviewEngineAVerdictComparison | null;
}

export default function AIReviewEngineAVerdictPanel({
  comparison,
}: AIReviewEngineAVerdictPanelProps) {
  const v = comparison ?? ({} as AIChartReviewEngineAVerdictComparison);
  const verdict = v.comparisonVerdict || 'unknown';
  const verdictClass = VERDICT_CLASS[verdict] ?? VERDICT_CLASS.unknown;
  const entryRejected =
    verdict === 'engine_a_direction_confirmed_entry_rejected' ||
    (v.chartConfirmsEngineADirection === true && v.chartContradictsEntryTiming === true);

  return (
    <div className="space-y-2 border border-border/50 rounded-md p-2 bg-muted/10">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
          Engine A vs AI verdict
        </span>
        <Badge className={`${verdictClass} text-[10px] border`}>
          {verdict.replace(/_/g, ' ')}
        </Badge>
        {v.aiDowngradedEngineA && (
          <Badge variant="outline" className="text-[10px] border-amber-500/50 text-amber-300">
            AI downgraded Engine A
          </Badge>
        )}
      </div>

      {entryRejected && v.finalReason && (
        <div className="text-[11px] border border-amber-500/30 rounded-md px-2 py-1.5 text-amber-100/90 bg-amber-500/5">
          {v.finalReason}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5 text-[10px]">
        <VerdictKV label="Engine A" value={fmtScoreLine(v)} />
        <VerdictKV label="Chart confirms direction" value={fmtBool(v.chartConfirmsEngineADirection)} />
        <VerdictKV label="Chart confirms entry timing" value={fmtBool(v.chartConfirmsEntryTiming)} />
        <VerdictKV label="Chart contradicts entry timing" value={fmtBool(v.chartContradictsEntryTiming)} />
        <VerdictKV label="Chart contradicts direction" value={fmtBool(v.chartContradictsEngineADirection)} />
        <VerdictKV label="AI agrees with Engine A" value={fmtBool(v.aiAgreesWithEngineA)} />
        <VerdictKV label="Final decision" value={show(v.finalDecision, 'unknown')} />
        <VerdictKV label="Final reason" value={show(v.finalReason)} />
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
