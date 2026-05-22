import { Badge } from '@/components/ui/badge';
import { fmtReviewBool, showReviewValue } from '@/lib/aiReviewDisplay';
import type { SuggestedTradePlan, TradeSkillReview } from '@/types/athena';

const DECISION_PILL: Record<string, string> = {
  ENTRY_NOW: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  WAIT_FOR_PULLBACK: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  WAIT_FOR_ACCEPTANCE: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  WATCH_ONLY: 'bg-zinc-500/15 text-zinc-300 border-zinc-500/40',
  NO_TRADE: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
  INVALIDATED: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
};

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-border/40 bg-background/30 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-0.5 text-[11px] text-foreground/85 break-words">{value}</div>
    </div>
  );
}

function SuggestedPlanBlock({ plan }: { plan: SuggestedTradePlan | null | undefined }) {
  if (!plan) return null;
  return (
    <div className="rounded-md border border-border/50 bg-background/20 p-2 space-y-1 text-[11px]">
      <div className="font-medium text-muted-foreground">Suggested trade plan (advisory)</div>
      <div className="grid grid-cols-2 gap-2">
        <Row label="Action" value={showReviewValue(plan.action)} />
        <Row label="Trigger" value={showReviewValue(plan.triggerType)} />
        <Row label="Level" value={plan.level != null ? String(plan.level) : '—'} />
        <Row label="Context TF" value={showReviewValue(plan.contextTf)} />
        <Row label="Entry TF" value={showReviewValue(plan.entryTf)} />
        <Row label="Execution TF" value={showReviewValue(plan.executionTf)} />
      </div>
      {plan.reason && <div className="text-muted-foreground">{plan.reason}</div>}
    </div>
  );
}

export interface TradeSkillReviewPanelProps {
  skill: TradeSkillReview | null | undefined;
  suggestedPlan?: SuggestedTradePlan | null;
  /** Engine D shows market state / aggression; Engine A chart review hides aggression. */
  variant?: 'engine_a_chart' | 'engine_d_scalp';
  timeframeRoute?: string | null;
}

export default function TradeSkillReviewPanel({
  skill,
  suggestedPlan,
  variant = 'engine_a_chart',
  timeframeRoute,
}: TradeSkillReviewPanelProps) {
  if (!skill?.decision && skill?.entryAllowedNow == null) return null;

  const decision = String(skill.decision || 'WATCH_ONLY').toUpperCase();
  const decisionClass = DECISION_PILL[decision] ?? DECISION_PILL.WATCH_ONLY;
  const isScalp = variant === 'engine_d_scalp';

  return (
    <div className="rounded-md border border-primary/30 bg-primary/5 p-2 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Trade skill review
        </span>
        <Badge className={`${decisionClass} text-[10px] border`}>{decision}</Badge>
        <Badge variant="outline" className="text-[10px]">
          entry allowed: {fmtReviewBool(skill.entryAllowedNow)}
        </Badge>
        {timeframeRoute && (
          <Badge variant="outline" className="text-[10px]">
            TF route: {timeframeRoute}
          </Badge>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <Row label="Direction" value={showReviewValue(skill.direction)} />
        {isScalp && <Row label="Market state" value={showReviewValue(skill.marketState)} />}
        <Row label="Location" value={showReviewValue(skill.locationAssessment)} />
        {isScalp && (
          <Row label="Aggression" value={showReviewValue(skill.aggressionAssessment)} />
        )}
        {isScalp && <Row label="Entry model" value={showReviewValue(skill.entryModel)} />}
        <Row
          label="Invalidation"
          value={
            skill.invalidationLevel != null
              ? `${skill.invalidationLevel}${skill.invalidationReason ? ` — ${skill.invalidationReason}` : ''}`
              : showReviewValue(skill.invalidationReason)
          }
        />
        {!isScalp && (
          <Row
            label="Direction valid"
            value={skill.direction === 'LONG' || skill.direction === 'SHORT' ? 'Yes' : 'No / neutral'}
          />
        )}
      </div>

      {skill.waitReason && <Row label="Wait reason" value={skill.waitReason} />}
      {skill.noTradeReason && <Row label="No trade reason" value={skill.noTradeReason} />}

      {skill.requiredConfirmation && skill.requiredConfirmation.length > 0 && (
        <div className="text-[11px]">
          <div className="text-[10px] text-muted-foreground mb-1">Required confirmation</div>
          <ul className="list-disc list-inside space-y-0.5">
            {skill.requiredConfirmation.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {skill.chartReadSummary && (
        <Row label="Chart read" value={skill.chartReadSummary} />
      )}

      <SuggestedPlanBlock plan={suggestedPlan ?? skill.suggestedTradePlan} />

      <div className="text-[10px] text-muted-foreground italic">
        AI review is advisory only — does not grant execution permission.
      </div>
    </div>
  );
}
