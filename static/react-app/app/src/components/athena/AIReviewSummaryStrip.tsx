import { Badge } from '@/components/ui/badge';
import {
  AI_REVIEW_EMPTY,
  AI_REVIEW_SEP,
  fmtReviewNum,
  fmtReviewPass,
  showReviewValue,
} from '@/lib/aiReviewDisplay';
import type {
  AIChartReviewEngineSummary,
  AIChartReviewProviderStatus,
  AIChartReviewSummary,
} from '@/types/athena';

const STATUS_PILL: Record<AIChartReviewProviderStatus, string> = {
  success: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  failed_auth: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
  insufficient_credit: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  rate_limited: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  timeout: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  fallback_used: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  parse_error: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
  unknown: 'bg-zinc-500/15 text-zinc-300 border-zinc-500/40',
};

const ACTION_PILL: Record<string, string> = {
  trade: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  wait: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  reject: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
  watch: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
};

function engineALine(engine: AIChartReviewEngineSummary | null | undefined): string {
  if (!engine) return AI_REVIEW_EMPTY;
  const score = fmtReviewNum(engine.score, 2);
  const max = fmtReviewNum(engine.maxScore, 2);
  const threshold = fmtReviewNum(engine.threshold, 2);
  const pass = fmtReviewPass(engine.passed);
  const direction = showReviewValue(engine.direction);
  const norm =
    engine.normalizedScore != null && !Number.isNaN(engine.normalizedScore)
      ? `${Math.round(engine.normalizedScore)}%`
      : null;
  const normText = norm ? `${AI_REVIEW_SEP}${norm} norm` : '';
  return `${score} / ${max}${AI_REVIEW_SEP}threshold ${threshold}${AI_REVIEW_SEP}${pass}${AI_REVIEW_SEP}${direction}${normText}`;
}

function ScoreCell({ label, value }: { label: string; value: number | null | undefined }) {
  const display = fmtReviewNum(value);
  return (
    <div className="border border-border/40 rounded-md px-2 py-1.5 min-w-0">
      <div className="text-[10px] text-muted-foreground truncate">{label}</div>
      <div className="text-[11px] font-mono font-semibold">
        {display === AI_REVIEW_EMPTY ? display : `${display}/100`}
      </div>
    </div>
  );
}

export interface AIReviewSummaryStripProps {
  summary?: AIChartReviewSummary | null;
}

export default function AIReviewSummaryStrip({ summary }: AIReviewSummaryStripProps) {
  const s = summary ?? ({} as AIChartReviewSummary);
  const providerStatus = s.providerStatus || 'unknown';
  const statusClass =
    STATUS_PILL[providerStatus as AIChartReviewProviderStatus] ?? STATUS_PILL.unknown;
  const humanAction = s.humanAction || 'watch';
  const actionClass = ACTION_PILL[String(humanAction)] ?? ACTION_PILL.watch;
  const provider = s.provider || 'unknown';
  const model = s.model || 'unknown';
  const selectedProvider = s.selectedProvider || null;
  const providerFailure = s.providerFailure ?? s.provider_failure ?? null;
  const setupType = s.setupType || s.engineD?.setupType || AI_REVIEW_EMPTY;

  const providerLine =
    selectedProvider && selectedProvider !== provider
      ? `selected: ${selectedProvider} → active: ${provider} / ${model} / ${providerStatus.replace(/_/g, ' ')}`
      : `${provider} / ${model} / ${providerStatus.replace(/_/g, ' ')}`;

  const fallbackReasonLine = (() => {
    if (s.fallbackUsed && providerFailure) {
      const failedProvider = providerFailure.provider || selectedProvider || 'unknown';
      const error = providerFailure.error || 'unknown error';
      const status = providerFailure.providerStatus
        ? ` (${String(providerFailure.providerStatus).replace(/_/g, ' ')})`
        : '';
      return `${failedProvider} → "${error}"${status}`;
    }
    if (selectedProvider && selectedProvider !== provider) {
      return `${selectedProvider} was selected but ${provider} answered (provider mismatch)`;
    }
    return null;
  })();

  return (
    <div className="space-y-2 border border-border/50 rounded-md p-2 bg-muted/20">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
          Deterministic review postprocessor
        </span>
        <Badge className={`${actionClass} text-[10px] border`}>
          {humanAction}
        </Badge>
        <Badge className={`${statusClass} text-[10px] border`}>
          {providerStatus.replace(/_/g, ' ')}
        </Badge>
        {s.fallbackUsed && (
          <Badge variant="outline" className="text-[10px] border-amber-500/50 text-amber-300">
            fallback
          </Badge>
        )}
        {selectedProvider && selectedProvider !== provider && !s.fallbackUsed && (
          <Badge variant="outline" className="text-[10px] border-amber-500/50 text-amber-300">
            provider mismatch
          </Badge>
        )}
        <Badge variant="outline" className="text-[10px]">
          model confidence {fmtReviewNum(s.modelConfidence ?? s.confidence)} / 100
        </Badge>
        {s.confidenceCalibrated !== true && (
          <Badge variant="outline" className="text-[10px] border-amber-500/50 text-amber-300">
            uncalibrated
          </Badge>
        )}
        <span className="text-[10px] text-muted-foreground font-mono ml-auto truncate max-w-[50%]">
          {provider}/{model}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-1.5">
        <ScoreCell label="Postprocessed overall" value={s.overallScore} />
        <ScoreCell label="Postprocessed tradeability" value={s.tradeabilityScore} />
        <ScoreCell
          label={s.sourceQualityScore != null && s.engineAlignmentScore == null ? 'Source' : 'Engine align'}
          value={s.sourceQualityScore ?? s.engineAlignmentScore}
        />
        <ScoreCell label="Visual" value={s.visualConfirmationScore} />
        <ScoreCell label="Entry" value={s.entryQualityScore} />
        <ScoreCell label="Risk" value={s.riskScore} />
      </div>

      {s.modelScores && Object.values(s.modelScores).some((value) => value != null) && (
        <SummaryKV
          label="Model-proposed scores (advisory)"
          value={Object.entries(s.modelScores)
            .filter(([, value]) => value != null)
            .map(([key, value]) => `${key.replace(/Score$/, '')} ${fmtReviewNum(value)}`)
            .join(`${AI_REVIEW_SEP}`)}
        />
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5 text-[10px]">
        <SummaryKV label="Human action" value={showReviewValue(humanAction)} />
        <SummaryKV label="Setup type" value={setupType} />
        <div className="border border-border/40 rounded-md px-2 py-1">
          {s.engineD ? (
            <>
              <span className="text-muted-foreground">Engine D: </span>
              <span className="font-mono">
                {showReviewValue(s.engineD.direction)} grade {showReviewValue((s.engineD as { aiGrade?: string | null }).aiGrade)} / {fmtReviewNum((s.engineD as { aiScore?: number | null }).aiScore, 1)}
              </span>
            </>
          ) : (
            <>
              <span className="text-muted-foreground">Engine A: </span>
              <span className="font-mono">{engineALine(s.engineA)}</span>
            </>
          )}
        </div>
        <SummaryKV
          label="Provider"
          value={providerLine}
        />
        {fallbackReasonLine ? (
          <SummaryKV label="Fallback reason" value={fallbackReasonLine} />
        ) : null}
        <SummaryKV label="Fallback used" value={s.fallbackUsed ? 'yes' : 'no'} />
        <SummaryKV label="Final reason" value={s.finalReason || AI_REVIEW_EMPTY} />
      </div>
    </div>
  );
}

function SummaryKV({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-border/40 rounded-md px-2 py-1 min-w-0">
      <span className="text-muted-foreground">{label}: </span>
      <span className="break-words">{value}</span>
    </div>
  );
}
