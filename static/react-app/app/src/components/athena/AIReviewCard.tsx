import { Suspense, lazy, memo, useMemo } from 'react';
import { Chip, Details, KeyValue } from '@/components/shared/primitives';
import type { Tone } from '@/components/shared/primitives';
import AIReviewContextCompletenessPanel from '@/components/athena/AIReviewContextCompletenessPanel';
import AIReviewEngineAVerdictPanel from '@/components/athena/AIReviewEngineAVerdictPanel';
import AIReviewSummaryStrip from '@/components/athena/AIReviewSummaryStrip';
import AIDisagreementVizPanel from '@/components/athena/AIDisagreementVizPanel';
import EngineANonVisualContextPanel from '@/components/athena/EngineANonVisualContextPanel';
import EvidenceRefsPanel from '@/components/athena/EvidenceRefsPanel';
import TradeSkillReviewPanel from '@/components/athena/TradeSkillReviewPanel';
import {
  AI_REVIEW_EMPTY,
  AI_REVIEW_SEP,
  showReviewValue,
} from '@/lib/aiReviewDisplay';
import type {
  AIChartReviewResponse,
  AIChartReviewConcordanceState,
  EvidenceRefClaim,
} from '@/types/athena';

const WhatIfReplayPanel = lazy(() => import('@/components/athena/WhatIfReplayPanel'));

/* Verdict and concordance are status states, so they use the reserved
   status tones rather than raw Tailwind palette hues. */
const CONCORDANCE_TONE: Record<AIChartReviewConcordanceState, Tone> = {
  agree: 'long',
  partial: 'warning',
  disagree: 'short',
  unknown: 'default',
};

const VERDICT_TONE: Record<string, Tone> = {
  VALID: 'long',
  CAUTION: 'warning',
  INVALID: 'short',
  NO_TRADE: 'default',
};

function showList(items: unknown): string[] | null {
  if (!Array.isArray(items) || items.length === 0) return null;
  return items.map((it) => String(it));
}

function deltaSeconds(a?: string | null, b?: string | null): number | null {
  if (!a || !b) return null;
  const ta = Date.parse(a);
  const tb = Date.parse(b);
  if (Number.isNaN(ta) || Number.isNaN(tb)) return null;
  return Math.round(Math.abs(ta - tb) / 1000);
}

export interface AIReviewCardProps {
  response: AIChartReviewResponse;
}

function AIReviewCardImpl({ response }: AIReviewCardProps) {
  const ai = response.ai_review;
  const c = response.concordance;
  const primaryEngine = String(response.primaryEngine || 'A').toUpperCase() === 'B' ? 'B' : 'A';
  const reviewVariant = primaryEngine === 'B' ? 'engine_b_chart' : 'engine_a_chart';
  const verdictComparison = primaryEngine === 'B'
    ? (response.engineBVerdictComparison ?? response.engine_b_verdict_comparison)
    : (response.engineAVerdictComparison ?? response.engine_a_verdict_comparison);
  if (!ai || !c) {
    return (
      <div className="rounded-lg border border-border bg-card p-3">
        <p className="note text-warning">
          AI review response is incomplete (missing {[!ai ? 'ai_review' : null, !c ? 'concordance' : null].filter(Boolean).join(' and ')}).
        </p>
      </div>
    );
  }
  const ts = response.timestamps;
  const ctx = primaryEngine === 'B'
    ? (response.engine_b_context ?? response.engineBContext)
    : response.engine_a_context;
  const engineACtx = primaryEngine === 'A' ? response.engine_a_context : undefined;
  const summary = response.aiReviewSummary ?? response.ai_review_summary;
  const atrInfo = ctx && typeof ctx === 'object' ? (ctx as { atr?: Record<string, unknown> }).atr : undefined;
  const scanDelta = deltaSeconds(ts?.scan_timestamp, ts?.chart_captured_at);
  const verdictTone = VERDICT_TONE[ai.verdict] ?? VERDICT_TONE.NO_TRADE;
  const concordanceTone = CONCORDANCE_TONE[c.concordance] ?? CONCORDANCE_TONE.unknown;

  const supporting = showList(ai.supporting_reasons);
  const risks = showList(ai.risks);
  const missing = showList(ai.missing_context);
  const warnings = showList(response.mismatch_warnings);
  const suggestedPlan =
    response.suggestedTradePlan ?? response.suggested_trade_plan ?? ai.suggestedTradePlan;
  const nonVisualContext =
    response.nonVisualContext ??
    response.engineANonVisualContext ??
    engineACtx?.non_visual_context ??
    engineACtx?.engine_a_non_visual_context;
  const scoreAttribution =
    response.scoreAttribution ??
    response.engineAScoreAttribution ??
    engineACtx?.score_attribution;

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
      concordance: c.concordance,
    }),
    [ctx, ai.verdict, ai.confidence, c.concordance],
  );

  const disagreementVerdicts = useMemo(
    () => ({
      marcus: { decision: ai.verdict, confidence: null },
      vision: {
        decision: ai.visual_confirmation ?? ai.visual_contradiction ?? null,
        confidence: null,
      },
      concordance: { decision: c.concordance, confidence: null },
    }),
    [ai.verdict, ai.visual_confirmation, ai.visual_contradiction, c.concordance],
  );

  const whatIfBaseContext = useMemo(
    () => (ctx && typeof ctx === 'object' ? (ctx as Record<string, unknown>) : {}),
    [ctx],
  );

  const freshnessFallback = (() => {
    const status = showReviewValue(atrInfo?.atr_freshness_status);
    const tf = showReviewValue(atrInfo?.atr_tf);
    const age = atrInfo?.atr_age_seconds;
    const ageText = age == null ? AI_REVIEW_EMPTY : `${age}s`;
    return `ATR ${status} (${tf}, age ${ageText})`;
  })();

  return (
    <div className="rounded-lg border border-border bg-card">
      {/* ── Verdict bar: the whole point of the review ── */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-3 py-2">
        <span className="panel-title mr-1">AI Chart Review</span>
        <Chip tone={verdictTone}>{ai.verdict}</Chip>
        <Chip tone={concordanceTone}>{c.concordance}</Chip>
        <Chip title="Model self-reported confidence">conf {ai.confidence}</Chip>
        {c.divergence_type !== 'none' && (
          <Chip tone="warning">{c.divergence_type.replace(/_/g, ' ')}</Chip>
        )}
        <span className="readout ml-auto shrink-0 text-[10px] text-muted-foreground">
          {response.provider}/{showReviewValue(response.model)}
          {response.dedup_hit ? `${AI_REVIEW_SEP}cached` : ''}
        </span>
      </div>

      <div className="space-y-3 p-3">
        {/* ── What the model actually said ── */}
        <AIReviewSummaryStrip summary={summary} />

        {/* Warnings change what you do, so they never collapse */}
        {warnings && (
          <div className="rounded border border-warning/30 bg-warning/5 p-2">
            <p className="label mb-1 text-warning">Mismatch warnings</p>
            <ul className="space-y-0.5">
              {warnings.map((w, i) => (
                <li key={i} className="note">{w}</li>
              ))}
            </ul>
          </div>
        )}

        {/* ── Reasoning ── */}
        <Details summary="Reasoning, risks and missing context" defaultOpen>
          <ListBlock label="Supporting reasons" items={supporting} />
          <ListBlock label="Risks" items={risks} />
          <ListBlock label="Missing context" items={missing} />
          {c.divergence_note && (
            <div>
              <p className="label mb-1">Concordance note</p>
              <p className="note">{c.divergence_note}</p>
            </div>
          )}
        </Details>

        {/* ── Assessment fields ── */}
        <Details summary="Assessment detail">
          <div className="grid grid-cols-1 gap-x-6 md:grid-cols-2">
            <KeyValue label="Human action" value={showReviewValue(ai.human_action)} />
            <KeyValue label="Setup type" value={showReviewValue(ai.setup_type)} />
            <KeyValue label="Visual confirmation" value={showReviewValue(ai.visual_confirmation)} />
            <KeyValue label="Visual contradiction" value={showReviewValue(ai.visual_contradiction)} />
            <KeyValue
              label="Engine alignment"
              value={showReviewValue(
                primaryEngine === 'B'
                  ? (ai as { engine_b_alignment?: string }).engine_b_alignment
                  : ai.engine_a_alignment,
              )}
            />
            <KeyValue label="ATR / RR" value={showReviewValue(ai.atr_rr_assessment)} />
            <KeyValue
              label="Freshness"
              value={ai.freshness_assessment ? ai.freshness_assessment : freshnessFallback}
            />
            <KeyValue label="Entry quality" value={showReviewValue(ai.entry_quality)} />
          </div>

          <TradeSkillReviewPanel
            skill={ai}
            suggestedPlan={suggestedPlan}
            variant={reviewVariant}
            timeframeRoute={
              (response as { timeframeRoute?: { route?: string } }).timeframeRoute?.route
              ?? (response as { timeframe_route?: { route?: string } }).timeframe_route?.route
              ?? null
            }
          />

          <AIReviewEngineAVerdictPanel comparison={verdictComparison} primaryEngine={primaryEngine} />
        </Details>

        {/* ── Provenance and cross-checks ── */}
        <Details summary="Evidence, context and provenance">
          <AIDisagreementVizPanel
            verdicts={disagreementVerdicts}
            claims={evidenceClaims}
            sources={evidenceSources}
          />

          <EvidenceRefsPanel claims={evidenceClaims} sources={evidenceSources} />

          <Suspense fallback={null}>
            <WhatIfReplayPanel baseContext={whatIfBaseContext} />
          </Suspense>

          <AIReviewContextCompletenessPanel
            completeness={response.contextCompleteness}
            detailed={response.missingContextDetailed}
            fundingOi={response.fundingOi}
            atrDiagnostics={response.atrDiagnostics}
            resistanceMap={response.resistanceMap}
          />

          <div>
            <p className="label mb-1">
              {primaryEngine === 'B' ? 'Engine B context' : 'Engine A non-visual context'}
            </p>
            {primaryEngine === 'B' ? (
              <pre className="readout whitespace-pre-wrap break-words text-[10px] text-muted-foreground">
                {JSON.stringify(ctx?.structure_context ?? {}, null, 2)}
              </pre>
            ) : (
              <EngineANonVisualContextPanel
                context={nonVisualContext}
                scoreAttribution={scoreAttribution}
              />
            )}
          </div>

          <div className="space-y-0.5">
            <KeyValue label="Chart captured" value={showReviewValue(ts.chart_captured_at)} />
            <KeyValue
              label="Scan timestamp"
              value={showReviewValue(ts.scan_timestamp)}
              meta={scanDelta == null ? undefined : `delta ${scanDelta}s`}
            />
            <KeyValue label="Latest candle" value={showReviewValue(ts.latest_candle_ts)} />
          </div>
        </Details>
      </div>
    </div>
  );
}

function ListBlock({
  label,
  items,
}: {
  label: string;
  items: string[] | null;
}) {
  if (!items) return null;
  return (
    <div>
      <p className="label mb-1">{label}</p>
      <ul className="space-y-1">
        {items.map((it, i) => (
          <li key={i} className="note flex gap-2 break-words">
            <span className="select-none text-muted-foreground/50">—</span>
            <span>{it}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const AIReviewCard = memo(AIReviewCardImpl);
export default AIReviewCard;
