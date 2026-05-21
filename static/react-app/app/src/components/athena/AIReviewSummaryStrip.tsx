import { Badge } from '@/components/ui/badge';
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

function fmtNum(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return digits > 0 ? value.toFixed(digits) : String(Math.round(value));
}

function show(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined) return fallback;
  if (typeof value === 'string') return value.trim() === '' ? fallback : value;
  return String(value);
}

function fmtPass(passed: boolean | null | undefined): string {
  if (passed === true) return 'PASS';
  if (passed === false) return 'FAIL';
  return '—';
}

function engineALine(engine: AIChartReviewEngineSummary | null | undefined): string {
  if (!engine) return '—';
  const score = fmtNum(engine.score, 2);
  const max = fmtNum(engine.maxScore, 2);
  const threshold = fmtNum(engine.threshold, 2);
  const pass = fmtPass(engine.passed);
  const direction = show(engine.direction);
  return `${score} / ${max} · threshold ${threshold} · ${pass} · ${direction}`;
}

function ScoreCell({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div className="border border-border/40 rounded-md px-2 py-1.5 min-w-0">
      <div className="text-[10px] text-muted-foreground truncate">{label}</div>
      <div className="text-[11px] font-mono font-semibold">{fmtNum(value)}</div>
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
  const setupType = s.setupType || s.engineD?.setupType || '—';

  return (
    <div className="space-y-2 border border-border/50 rounded-md p-2 bg-muted/20">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
          Review summary
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
        <span className="text-[10px] text-muted-foreground font-mono ml-auto truncate max-w-[50%]">
          {provider}/{model}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-7 gap-1.5">
        <ScoreCell label="Overall" value={s.overallScore} />
        <ScoreCell label="Tradeability" value={s.tradeabilityScore} />
        <ScoreCell label="Engine align" value={s.engineAlignmentScore} />
        <ScoreCell label="Visual" value={s.visualConfirmationScore} />
        <ScoreCell label="Entry" value={s.entryQualityScore} />
        <ScoreCell label="Risk" value={s.riskScore} />
        <ScoreCell label="Confidence" value={s.confidence} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5 text-[10px]">
        <SummaryKV label="Human action" value={show(humanAction)} />
        <SummaryKV label="Setup type" value={setupType} />
        <div className="border border-border/40 rounded-md px-2 py-1">
          <span className="text-muted-foreground">Engine A: </span>
          <span className="font-mono">{engineALine(s.engineA)}</span>
        </div>
        <SummaryKV
          label="Provider"
          value={`${provider} / ${model} / ${providerStatus.replace(/_/g, ' ')}`}
        />
        <SummaryKV label="Fallback used" value={s.fallbackUsed ? 'yes' : 'no'} />
        <SummaryKV label="Final reason" value={s.finalReason || '—'} />
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
