import { Suspense, lazy, memo, useMemo } from 'react';
import { Sparkles } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import AIReviewContextCompletenessPanel from '@/components/athena/AIReviewContextCompletenessPanel';
import AIReviewSummaryStrip from '@/components/athena/AIReviewSummaryStrip';
import EvidenceRefsPanel from '@/components/athena/EvidenceRefsPanel';
import TradeSkillReviewPanel from '@/components/athena/TradeSkillReviewPanel';
import {
  AI_REVIEW_EMPTY,
  AI_REVIEW_SEP,
  fmtReviewBool,
  showReviewValue,
} from '@/lib/aiReviewDisplay';
import type {
  AIChartReviewContextCompleteness,
  AIChartReviewSummary,
  EvidenceRefClaim,
  ScalpAIChartReviewResponse,
  ScalpContextCompleteness,
  ScalpVerdictComparison,
} from '@/types/athena';

const WhatIfReplayPanel = lazy(() => import('@/components/athena/WhatIfReplayPanel'));

function normalizeContextCompleteness(
  raw: ScalpContextCompleteness | undefined,
): AIChartReviewContextCompleteness | undefined {
  if (!raw) return undefined;
  return {
    score: raw.score ?? null,
    status: raw.status ?? 'unknown',
    missingRequired: raw.missingRequired ?? [],
    missingOptional: raw.missingOptional ?? [],
    notApplicable: raw.notApplicable ?? [],
    metadata: raw.metadata ?? {},
  };
}

const CONCORDANCE_PILL: Record<string, string> = {
  agree: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  partial: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  disagree: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
  unknown: 'bg-zinc-500/15 text-zinc-300 border-zinc-500/40',
};

const VERDICT_PILL: Record<string, string> = {
  VALID: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  CAUTION: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  INVALID: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
  NO_TRADE: 'bg-zinc-500/15 text-zinc-300 border-zinc-500/40',
};

function showList(items: unknown): string[] | null {
  if (!Array.isArray(items) || items.length === 0) return null;
  return items.map((it) => String(it));
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-border/40 bg-background/30 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-0.5 text-[11px] text-foreground/85 break-words">{value}</div>
    </div>
  );
}

function ScalpVerdictPanel({ comparison }: { comparison: ScalpVerdictComparison | undefined }) {
  if (!comparison) return null;
  return (
    <div className="rounded-md border border-border/50 bg-background/30 p-2 space-y-2">
      <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        Setup vs chart
      </div>
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <Row label="Setup direction" value={showReviewValue(comparison.setupDirection)} />
        <Row label="Setup grade" value={showReviewValue(comparison.setupGrade)} />
        <Row label="Verdict" value={showReviewValue(comparison.comparisonVerdict)} />
        <Row label="Final decision" value={showReviewValue(comparison.finalDecision)} />
        <Row
          label="Chart confirms direction"
          value={fmtReviewBool(comparison.chartConfirmsDirection)}
        />
        <Row
          label="Entry timing ok"
          value={fmtReviewBool(comparison.chartConfirmsEntryTiming)}
        />
      </div>
      {comparison.downgradeReasons && comparison.downgradeReasons.length > 0 && (
        <div className="text-[11px] text-muted-foreground">
          Downgrades: {comparison.downgradeReasons.join(', ')}
        </div>
      )}
      {comparison.finalReason && (
        <div className="text-[11px] text-foreground/80">{comparison.finalReason}</div>
      )}
    </div>
  );
}

export interface ScalpAIReviewCardProps {
  response: ScalpAIChartReviewResponse;
}

function ScalpAIReviewCardImpl({ response }: ScalpAIReviewCardProps) {
  const ai = response.ai_review || {};
  const c = response.concordance || {};
  const ctx = response.engine_d_context ?? response.engineDContext;
  const summary = response.aiReviewSummary ?? response.ai_review_summary;
  const verdictComparison =
    response.scalpVerdictComparison ?? response.scalp_verdict_comparison;
  const verdictClass = VERDICT_PILL[String(ai.verdict || 'NO_TRADE')] ?? VERDICT_PILL.NO_TRADE;
  const concordanceClass = CONCORDANCE_PILL[String(c.concordance || 'unknown')] ?? CONCORDANCE_PILL.unknown;

  const supporting = showList(ai.supporting_reasons);
  const risks = showList(ai.risks);
  const missing = showList(ai.missing_context);
  const warnings = showList(response.mismatch_warnings);
  const suggestedPlan =
    response.suggestedTradePlan ?? response.suggested_trade_plan ?? ai.suggestedTradePlan;

  const evidenceClaims = useMemo((): EvidenceRefClaim[] => {
    const claims: EvidenceRefClaim[] = [];
    const summary = ai.chartReadSummary ?? (ai as { chart_read_summary?: string }).chart_read_summary;
    if (summary) claims.push({ claim_id: 'chart_read', text: String(summary), source_field: 'chartReadSummary' });
    supporting?.forEach((text, idx) => {
      claims.push({ claim_id: `support_${idx}`, text, source_field: 'supporting_reasons' });
    });
    return claims;
  }, [ai.chartReadSummary, supporting]);

  const evidenceSources = useMemo(
    () => ({
      ...(ctx && typeof ctx === 'object' ? (ctx as Record<string, unknown>) : {}),
      verdict: ai.verdict,
      confidence: ai.confidence,
    }),
    [ctx, ai.verdict, ai.confidence],
  );

  const whatIfBaseContext = useMemo(
    () => (ctx && typeof ctx === 'object' ? (ctx as Record<string, unknown>) : {}),
    [ctx],
  );

  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Sparkles className="w-4 h-4 text-primary" />
          <span className="text-sm font-semibold">Engine D AI Review</span>
          <Badge className={`${verdictClass} text-[10px] border`}>
            {showReviewValue(ai.verdict, 'CAUTION')}
          </Badge>
          <Badge variant="outline" className="text-[10px]">
            confidence: {showReviewValue(ai.confidence, '0')}
          </Badge>
          <Badge className={`${concordanceClass} text-[10px] border`}>
            {showReviewValue(c.concordance, 'unknown')}
          </Badge>
          {ctx?.ai_grade && (
            <Badge variant="outline" className="text-[10px]">
              grade {ctx.ai_grade}
              {ctx.ai_score != null ? ` / ${ctx.ai_score}` : ''}
            </Badge>
          )}
          <span className="text-[10px] text-muted-foreground font-mono ml-auto">
            {response.provider}/{showReviewValue(response.model)}
            {response.dedup_hit ? `${AI_REVIEW_SEP}cached` : ''}
          </span>
        </div>

        <AIReviewSummaryStrip summary={summary as AIChartReviewSummary | null | undefined} />

        <ScalpVerdictPanel comparison={verdictComparison} />

        <EvidenceRefsPanel claims={evidenceClaims} sources={evidenceSources} />

        <Suspense fallback={null}>
          <WhatIfReplayPanel baseContext={whatIfBaseContext} />
        </Suspense>

        <TradeSkillReviewPanel
          skill={ai}
          suggestedPlan={suggestedPlan}
          variant="engine_d_scalp"
        />

        <details className="rounded-md border border-border/40 bg-background/20 px-2 py-1.5">
          <summary className="cursor-pointer text-[11px] font-medium text-muted-foreground">
            Context completeness
          </summary>
          <div className="mt-2">
            <AIReviewContextCompletenessPanel
              completeness={normalizeContextCompleteness(
                response.contextCompleteness ?? response.context_completeness,
              )}
            />
          </div>
        </details>

        <details className="rounded-md border border-border/40 bg-background/20 px-2 py-1.5">
          <summary className="cursor-pointer text-[11px] font-medium text-muted-foreground">
            Review detail fields
          </summary>
          <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px]">
            <Row label="Human action" value={showReviewValue(ai.human_action)} />
            <Row label="Setup type" value={showReviewValue(ai.setup_type)} />
            <Row label="Visual confirmation" value={showReviewValue(ai.visual_confirmation)} />
            <Row label="Visual contradiction" value={showReviewValue(ai.visual_contradiction)} />
            <Row label="Entry quality" value={showReviewValue(ai.entry_quality)} />
            <Row label="Source quality" value={showReviewValue(ai.source_quality_assessment)} />
            <Row label="Execution TF" value={showReviewValue(ctx?.execution_tf)} />
            <Row label="Setup direction" value={showReviewValue(ctx?.direction)} />
          </div>
        </details>

        <details className="rounded-md border border-border/40 bg-background/20 px-2 py-1.5">
          <summary className="cursor-pointer text-[11px] font-medium text-muted-foreground">
            Supporting reasons, risks, and warnings
          </summary>
          <div className="mt-2 space-y-2">
            {supporting && (
              <div className="text-[11px]">
                <div className="font-medium text-muted-foreground">Supporting</div>
                <ul className="mt-1 list-disc pl-4 text-foreground/80 break-words">
                  {supporting.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
            {risks && (
              <div className="text-[11px]">
                <div className="font-medium text-muted-foreground">Risks</div>
                <ul className="mt-1 list-disc pl-4 text-foreground/80 break-words">
                  {risks.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
            {missing && (
              <div className="text-[11px]">
                <div className="font-medium text-muted-foreground">Missing context</div>
                <ul className="mt-1 list-disc pl-4 text-foreground/80 break-words">
                  {missing.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
            {warnings && (
              <div className="text-[11px] text-warning">
                Warnings: {warnings.join(', ')}
              </div>
            )}
          </div>
        </details>
      </CardContent>
    </Card>
  );
}

const ScalpAIReviewCard = memo(ScalpAIReviewCardImpl);
export default ScalpAIReviewCard;
