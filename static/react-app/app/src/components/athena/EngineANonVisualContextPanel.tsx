import type { ReactNode } from 'react';
import {
  AI_REVIEW_EMPTY,
  fmtReviewNum,
  showReviewValue,
} from '@/lib/aiReviewDisplay';
import type {
  EngineANonVisualContext,
  ScoreAttribution,
} from '@/types/athena';

export interface EngineANonVisualContextPanelProps {
  context?: EngineANonVisualContext;
  scoreAttribution?: ScoreAttribution;
}

export default function EngineANonVisualContextPanel({
  context,
  scoreAttribution,
}: EngineANonVisualContextPanelProps) {
  const addon = context?.addonContext;
  const derivatives = context?.derivativesContext;
  const microstructure = context?.microstructureContext;
  const intermarket = context?.intermarketContext;
  const news = context?.newsContext;

  return (
    <div className="space-y-2 border border-border/50 rounded-md p-2 bg-muted/10 text-[11px]">
      <Section title="Score attribution">
        <KV label="Raw score" value={fmtReviewNum(scoreAttribution?.technicalScoreRaw, 3)} />
        <KV label="Intermarket delta" value={fmtReviewNum(scoreAttribution?.intermarketDelta, 3)} />
        <KV label="News delta" value={fmtReviewNum(scoreAttribution?.newsSentimentDelta, 3)} />
        <KV label="Final score" value={fmtReviewNum(scoreAttribution?.finalEngineAScore, 3)} />
        <KV label="Threshold" value={fmtReviewNum(scoreAttribution?.threshold, 3)} />
        <KV
          label="AI score mutation"
          value={scoreAttribution?.aiReviewCanMutateScore === true ? 'enabled' : 'disabled'}
        />
      </Section>

      <Section title="Addon">
        <KV label="Type" value={showReviewValue(addon?.addonType)} />
        <KV label="Value" value={fmtReviewNum(addon?.addonValue, 3)} />
        <KV label="Status" value={showReviewValue(addon?.addonStatus)} />
        <KV label="Applies" value={showReviewValue(addon?.appliesToAssetClass)} />
        <KV label="Unsupported" value={showReviewValue(addon?.addonUnsupported)} />
        <KV label="Interpretation" value={showReviewValue(addon?.interpretation)} wide />
      </Section>

      <Section title="Funding / OI">
        <KV label="Status" value={showReviewValue(derivatives?.status)} />
        <KV label="Funding" value={fmtReviewNum(derivatives?.fundingRate, 6)} />
        <KV label="Funding z" value={fmtReviewNum(derivatives?.fundingRateZ, 3)} />
        <KV label="Open interest" value={fmtReviewNum(derivatives?.openInterest, 2)} />
        <KV label="OI delta pct" value={fmtReviewNum(derivatives?.openInterestDeltaPct, 3)} />
        <KV label="Source" value={showReviewValue(derivatives?.source)} />
      </Section>

      <Section title="Intermarket">
        <KV label="Verdict" value={showReviewValue(intermarket?.verdict)} />
        <KV label="Score" value={fmtReviewNum(intermarket?.score, 3)} />
        <KV label="Engine A delta" value={fmtReviewNum(intermarket?.engineADelta, 3)} />
        <KV label="Support" value={showReviewValue(intermarket?.supportDirection)} />
        <KV label="Strength" value={showReviewValue(intermarket?.supportStrength)} />
        <KV label="Severe contradiction" value={showReviewValue(intermarket?.severeContradiction)} />
        <KV label="Unavailable priors" value={showList(intermarket?.unavailablePriors)} wide />
      </Section>

      <Section title="News">
        <KV label="Vote" value={fmtReviewNum(news?.vote, 3)} />
        <KV label="Delta" value={fmtReviewNum(news?.delta, 3)} />
        <KV label="Fresh articles" value={fmtReviewNum(news?.freshArticleCount, 0)} />
        <KV label="Direction" value={showReviewValue(news?.direction)} />
        <KV label="Major event" value={showReviewValue(news?.majorEventDetected)} />
        <KV label="Major event description" value={showReviewValue(news?.majorEventDescription)} wide />
      </Section>

      <Section title="Microstructure">
        <KV label="Status" value={showReviewValue(microstructure?.status)} />
        <KV label="Order book imbalance" value={fmtReviewNum(microstructure?.orderBookImbalance, 4)} />
        <KV label="Orderflow delta" value={fmtReviewNum(microstructure?.orderflowDelta, 4)} />
        <KV label="Liquidity pressure" value={fmtReviewNum(microstructure?.liquidityPressure, 4)} />
        <KV label="Volume momentum spread" value={fmtReviewNum(microstructure?.volumeMomentumSpread, 4)} />
        <KV label="Source" value={showReviewValue(microstructure?.source)} />
      </Section>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="border border-border/40 rounded-md p-2">
      <div className="text-[10px] uppercase text-muted-foreground mb-1">{title}</div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-3 gap-y-1">{children}</div>
    </div>
  );
}

function KV({
  label,
  value,
  wide,
}: {
  label: string;
  value: string;
  wide?: boolean;
}) {
  return (
    <div className={`font-mono break-words ${wide ? 'md:col-span-2' : ''}`}>
      <span className="text-muted-foreground">{label}: </span>
      <span>{value}</span>
    </div>
  );
}

function showList(value: unknown): string {
  if (!Array.isArray(value) || value.length === 0) return AI_REVIEW_EMPTY;
  return value
    .map((item) => {
      if (item && typeof item === 'object') {
        return JSON.stringify(item);
      }
      return showReviewValue(item);
    })
    .join(' | ');
}
