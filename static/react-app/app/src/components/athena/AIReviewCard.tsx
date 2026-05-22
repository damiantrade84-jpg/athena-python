import { Sparkles } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import AIReviewContextCompletenessPanel from '@/components/athena/AIReviewContextCompletenessPanel';
import AIReviewEngineAVerdictPanel from '@/components/athena/AIReviewEngineAVerdictPanel';
import AIReviewSummaryStrip from '@/components/athena/AIReviewSummaryStrip';
import TradeSkillReviewPanel from '@/components/athena/TradeSkillReviewPanel';
import {
  AI_REVIEW_EMPTY,
  AI_REVIEW_SEP,
  showReviewValue,
} from '@/lib/aiReviewDisplay';
import type {
  AIChartReviewResponse,
  AIChartReviewConcordanceState,
} from '@/types/athena';

const CONCORDANCE_PILL: Record<AIChartReviewConcordanceState, string> = {
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

export default function AIReviewCard({ response }: AIReviewCardProps) {
  const ai = response.ai_review;
  const c = response.concordance;
  const ts = response.timestamps;
  const ctx = response.engine_a_context;
  const summary = response.aiReviewSummary ?? response.ai_review_summary;
  const verdictComparison =
    response.engineAVerdictComparison ?? response.engine_a_verdict_comparison;
  const atrInfo = ctx?.atr;
  const scanDelta = deltaSeconds(ts.scan_timestamp, ts.chart_captured_at);
  const verdictClass = VERDICT_PILL[ai.verdict] ?? VERDICT_PILL.NO_TRADE;
  const concordanceClass = CONCORDANCE_PILL[c.concordance];

  const supporting = showList(ai.supporting_reasons);
  const risks = showList(ai.risks);
  const missing = showList(ai.missing_context);
  const warnings = showList(response.mismatch_warnings);
  const suggestedPlan =
    response.suggestedTradePlan ?? response.suggested_trade_plan ?? ai.suggestedTradePlan;

  const freshnessFallback = (() => {
    const status = showReviewValue(atrInfo?.atr_freshness_status);
    const tf = showReviewValue(atrInfo?.atr_tf);
    const age = atrInfo?.atr_age_seconds;
    const ageText = age == null ? AI_REVIEW_EMPTY : `${age}s`;
    return `ATR ${status} (${tf}, age ${ageText})`;
  })();

  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Sparkles className="w-4 h-4 text-primary" />
          <span className="text-sm font-semibold">AI Chart Review</span>
          <Badge className={`${verdictClass} text-[10px] border`}>
            {ai.verdict}
          </Badge>
          <Badge variant="outline" className="text-[10px]">
            confidence: {ai.confidence}
          </Badge>
          <Badge className={`${concordanceClass} text-[10px] border`}>
            {c.concordance}
          </Badge>
          {c.divergence_type !== 'none' && (
            <Badge variant="outline" className="text-[10px]">
              divergence: {c.divergence_type.replace(/_/g, ' ')}
            </Badge>
          )}
          <span className="text-[10px] text-muted-foreground font-mono ml-auto">
            {response.provider}/{showReviewValue(response.model)}
            {response.dedup_hit ? `${AI_REVIEW_SEP}cached` : ''}
          </span>
        </div>

        <AIReviewSummaryStrip summary={summary} />

        <AIReviewEngineAVerdictPanel comparison={verdictComparison} />

        <TradeSkillReviewPanel
          skill={ai}
          suggestedPlan={suggestedPlan}
          variant="engine_a_chart"
          timeframeRoute={
            (response as { timeframeRoute?: { route?: string } }).timeframeRoute?.route
            ?? (response as { timeframe_route?: { route?: string } }).timeframe_route?.route
            ?? null
          }
        />

        <AIReviewContextCompletenessPanel
          completeness={response.contextCompleteness}
          detailed={response.missingContextDetailed}
          fundingOi={response.fundingOi}
          atrDiagnostics={response.atrDiagnostics}
          resistanceMap={response.resistanceMap}
        />

        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px]">
          <Row label="Human action" value={showReviewValue(ai.human_action)} />
          <Row label="Setup type" value={showReviewValue(ai.setup_type)} />
          <Row label="Visual confirmation" value={showReviewValue(ai.visual_confirmation)} />
          <Row label="Visual contradiction" value={showReviewValue(ai.visual_contradiction)} />
          <Row label="Engine A alignment" value={showReviewValue(ai.engine_a_alignment)} />
          <Row label="ATR / RR assessment" value={showReviewValue(ai.atr_rr_assessment)} />
          <Row
            label="Freshness assessment"
            value={ai.freshness_assessment ? ai.freshness_assessment : freshnessFallback}
          />
          <Row label="Entry quality" value={showReviewValue(ai.entry_quality)} />
        </div>

        <ListBlock label="Supporting reasons" items={supporting} />
        <ListBlock label="Risks" items={risks} />
        <ListBlock label="Missing context" items={missing} />

        <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[10px] text-muted-foreground border-t border-border/40 pt-2">
          <KV label="chart captured" value={showReviewValue(ts.chart_captured_at)} />
          <KV
            label="scan timestamp"
            value={`${showReviewValue(ts.scan_timestamp)}${
              scanDelta == null ? '' : ` (delta ${scanDelta}s)`
            }`}
          />
          <KV label="latest candle" value={showReviewValue(ts.latest_candle_ts)} />
        </div>

        {warnings && (
          <div className="text-[11px] text-warning border border-border/40 rounded-md p-2">
            <div className="font-semibold mb-1">Mismatch warnings</div>
            <ul className="list-disc list-inside space-y-0.5">
              {warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}

        {c.divergence_note && (
          <div className="text-[11px] text-muted-foreground border border-border/40 rounded-md p-2">
            <span className="font-semibold">Concordance note: </span>
            {c.divergence_note}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-border/40 rounded-md px-2 py-1.5">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className="text-[11px] break-words">{value}</div>
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

function ListBlock({
  label,
  items,
}: {
  label: string;
  items: string[] | null;
}) {
  if (!items) return null;
  return (
    <div className="text-[11px]">
      <div className="text-[10px] text-muted-foreground mb-1">{label}</div>
      <ul className="list-disc list-inside space-y-0.5">
        {items.map((it, i) => (
          <li key={i}>{it}</li>
        ))}
      </ul>
    </div>
  );
}
