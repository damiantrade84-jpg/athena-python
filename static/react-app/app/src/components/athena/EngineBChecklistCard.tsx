import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Check, X, AlertTriangle, Info } from 'lucide-react';
import { cn, fmtNum } from '@/lib/utils';
import { fmtLiveQuoteMeta, fmtPrice, engineBScoreBreakdown } from '@/lib/athenaFormat';
import {
  executableLevels,
  readEngineBCanonicalGatesFromNaked,
} from '@/lib/engineBCanonicalGates';
import type { EngineBNakedResult } from '@/types/athena';

interface Props {
  data: EngineBNakedResult | null | undefined;
  pair?: string;
  type?: string;
  livePrice?: number;
  livePriceAgeSec?: number;
  livePriceSource?: string;
  compact?: boolean;
}

function firstFiniteNumber(...values: unknown[]): number | undefined {
  for (const value of values) {
    if (value == null || value === '' || typeof value === 'boolean') continue;
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

export default function EngineBChecklistCard({ data, pair, type, livePrice, livePriceAgeSec, livePriceSource, compact }: Props) {
  if (!data) {
    return (
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-4 text-xs text-muted-foreground text-center">
          No Engine B result yet — run the Engine B scan.
        </CardContent>
      </Card>
    );
  }

  const conf = data.confidence || {};
  const breakdown = engineBScoreBreakdown(data);
  const checklist = (data.checklist || conf.checklist || {}) as Record<string, unknown>;
  const score = breakdown?.gateScore ?? (conf.gate_score as number | undefined);
  const max = breakdown?.gateMax ?? (conf.gate_max_possible as number | undefined);
  const totalScore = breakdown?.totalScore ?? ((conf.score ?? data.score) as number | undefined);
  const totalMax = breakdown?.totalMax ?? ((conf.max_possible ?? data.max_score) as number | undefined);
  const minScore = breakdown?.minScore ?? (data.min_score as number | undefined);
  const tp1Rr = firstFiniteNumber(conf.execution_rr1, data.execution_rr1, data.rr1);
  const runnerRr = firstFiniteNumber(conf.execution_rr2, data.execution_rr2, conf.rr_used_for_gate, data.rr);
  const rrUsedForGate = firstFiniteNumber(conf.rr_used_for_gate, data.rr_used_for_gate, runnerRr);
  const tp1MinRr = firstFiniteNumber(conf.tp1_min_rr, data.tp1_min_rr);
  const runnerMinRr = firstFiniteNumber(conf.rr_required, data.rr_required, data.min_rr);
  const verdict = data.structural_verdict || '—';
  const dirBg =
    data.direction === 'LONG' ? 'bg-long/20 text-long' : data.direction === 'SHORT' ? 'bg-short/20 text-short' : 'bg-muted/40 text-muted-foreground';

  const gates = readEngineBCanonicalGatesFromNaked(data)!;
  const levels = executableLevels(data, gates, pair, type);
  const macro_ok = Boolean(checklist.macro_ok);
  const d1_conflict = (checklist.d1_conflict ?? data.d1_conflict) as unknown;

  const hardFails =
    (conf.hard_fail_reasons as string[] | undefined) || (data.hard_fail_reasons as string[] | undefined) || [];
  const canonicalReasons =
    (conf.engine_b_rejection_reasons as string[] | undefined)
    || (data.engine_b_rejection_reasons as string[] | undefined)
    || [];
  const softWarns =
    (conf.soft_warnings as string[] | undefined) || (data.soft_warnings as string[] | undefined) || [];
  const diagNotes =
    (conf.diagnostic_notes as string[] | undefined) || (data.diagnostic_notes as string[] | undefined) || [];

  const profileCtx = (
    (conf.profile_context as EngineBNakedResult['profile_context'])
    || (data.profile_context as EngineBNakedResult['profile_context'])
  );
  const profilePoints = (conf.profile_points ?? data.profile_points) as number | undefined;
  const profileOk = Boolean(conf.profile_ok ?? data.profile_ok);
  const profileUnavailable = profileCtx?.trusted === false;
  const profileActive = Boolean(profileCtx?.enabled);

  const confidenceBadgeClass =
    gates.confidenceDisplayLabel === 'CONFIDENCE PASSED'
      ? 'bg-long/20 text-long'
      : gates.confidenceDisplayLabel === 'SCORE PASSED / GATE FAILED'
        ? 'bg-warning/20 text-warning'
        : 'bg-short/20 text-short';

  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className={compact ? 'p-3 space-y-2' : 'p-4 space-y-3'}>
        {/* Header */}
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            <Badge className={cn('text-[10px]', confidenceBadgeClass)}>
              {gates.confidenceDisplayLabel}
            </Badge>
            {!gates.canonicalTradeOk && (
              <Badge variant="outline" className="text-[10px] bg-short/10 text-short border-short/30">
                NO ENTRY
              </Badge>
            )}
            {data.direction && <Badge className={cn('text-[10px]', dirBg)}>{data.direction}</Badge>}
            {data.style && (
              <Badge variant="outline" className="text-[10px]">
                {data.style}
              </Badge>
            )}
            {profileActive && profileOk && typeof profilePoints === 'number' && profilePoints > 0 && (
              <Badge variant="outline" className="text-[10px] bg-long/10 text-long border-long/30">
                Profile +{fmtNum(profilePoints, 2)}
              </Badge>
            )}
            {gates.canonicalStatus && (
              <Badge
                variant="outline"
                className={cn(
                  'text-[10px]',
                  gates.canonicalTradeOk ? 'bg-long/10 text-long border-long/30' : 'bg-short/10 text-short border-short/30',
                )}
              >
                {gates.canonicalStatus}
              </Badge>
            )}
          </div>
          <div className="text-[10px] text-muted-foreground font-mono text-right">
            <div>
              Gate {fmtNum(score, 2)} / {fmtNum(max, 2)}
            </div>
            {totalScore != null && (
              <div className="text-[9px]">
                Total {fmtNum(totalScore, 2)} / {fmtNum(totalMax, 2)}
                {minScore != null && <span> (min {fmtNum(minScore, 2)})</span>}
                {breakdown?.bonusPoints != null && breakdown.bonusPoints !== 0 && (
                  <span> · bonus {breakdown.bonusPoints >= 0 ? '+' : ''}{fmtNum(breakdown.bonusPoints, 2)}</span>
                )}
              </div>
            )}
            {breakdown?.scoreFloorPasses && !breakdown.confidencePasses && (
              <div className="text-[9px] text-warning">
                Total clears the score floor; one or more mandatory gates failed.
              </div>
            )}
          </div>
        </div>

        {/* Structural verdict + sequences */}
        <div className="grid grid-cols-3 gap-2">
          <SmallStat label="Structural Verdict" value={String(verdict)} />
          <SmallStat label="Swing Seq" value={String(data.current_swing_sequence || '—')} />
          <SmallStat label="Macro Seq" value={String(data.macro_swing_sequence || '—')} />
        </div>

        {profileUnavailable && (
          <div className="text-[10px] text-muted-foreground border border-border/40 rounded-md p-2 leading-snug">
            Volume profile (POC/VAH/VAL) not used for this asset — unreliable volume feed.
          </div>
        )}

        {/* Checklist gates — canonical only */}
        <div className="grid grid-cols-2 gap-2">
          <Gate label="Structure" ok={gates.canonicalStructureOk} />
          <Gate label="Location" ok={gates.canonicalLocationOk} />
          <Gate label="Entry / Trigger" ok={gates.canonicalTriggerOk} />
          <Gate label="Room / RR" ok={gates.canonicalRoomRrOk} />
          {checklist.macro_ok != null && <Gate label="Macro" ok={macro_ok} />}
          {d1_conflict != null && (
            <Gate
              label="D1 Conflict"
              ok={!d1_conflict || d1_conflict === 'no_conflict'}
              dangerLabel={typeof d1_conflict === 'string' ? d1_conflict : 'CONFLICT'}
            />
          )}
        </div>

        {/* Levels */}
        <div className="grid grid-cols-4 gap-2">
          <SmallStat label="Live" value={fmtPrice(livePrice, pair, type)} accent="primary" meta={fmtLiveQuoteMeta(livePriceAgeSec, livePriceSource)} />
          <SmallStat
            label="Entry"
            value={levels.showExecutable ? fmtPrice(levels.entry, pair, type) : '—'}
          />
          <SmallStat
            label="SL"
            value={levels.showExecutable ? fmtPrice(levels.sl, pair, type) : '—'}
            accent="short"
          />
          <SmallStat
            label="TP"
            value={levels.showExecutable ? fmtPrice(levels.tp, pair, type) : '—'}
            accent="long"
          />
          <SmallStat
            label="TP1 R:R"
            value={levels.showExecutable ? fmtNum(tp1Rr, 2) : '—'}
            accent={
              levels.showExecutable
              && tp1Rr != null
              && (tp1MinRr == null || tp1Rr >= tp1MinRr)
                ? 'long'
                : 'muted'
            }
            meta={tp1MinRr != null ? `min ${fmtNum(tp1MinRr, 2)}` : undefined}
          />
          <SmallStat
            label="Runner R:R"
            value={levels.showExecutable ? fmtNum(runnerRr, 2) : '—'}
            accent={
              levels.showExecutable
              && runnerRr != null
              && (runnerMinRr == null || runnerRr >= runnerMinRr)
                ? 'long'
                : 'muted'
            }
            meta={runnerMinRr != null ? `min ${fmtNum(runnerMinRr, 2)}` : undefined}
          />
          {rrUsedForGate != null && rrUsedForGate !== runnerRr && (
            <SmallStat label="Gate R:R" value={fmtNum(rrUsedForGate, 2)} />
          )}
        </div>

        {!levels.showExecutable
          && (levels.diagnosticEntry != null || levels.diagnosticSl != null || levels.diagnosticTp != null) && (
          <div className="text-[10px] text-muted-foreground border border-border/40 rounded-md p-2 space-y-1">
            <p className="uppercase font-semibold text-warning">Rejected diagnostic levels — not executable</p>
            <p className="font-mono">
              Entry {fmtPrice(levels.diagnosticEntry, pair, type)}
              {' · '}SL {fmtPrice(levels.diagnosticSl, pair, type)}
              {' · '}TP {fmtPrice(levels.diagnosticTp, pair, type)}
            </p>
          </div>
        )}

        {/* Active FVGs */}
        {data.active_fvgs && data.active_fvgs.length > 0 && (
          <div className="space-y-1">
            <p className="text-[10px] uppercase text-muted-foreground">Active FVGs</p>
            <div className="space-y-1">
              {data.active_fvgs.slice(0, 4).map((f, i) => (
                <div key={i} className="flex items-center justify-between text-[10px] font-mono p-2 rounded-md bg-muted/30">
                  <span>
                    {f.direction || ''} [{fmtPrice(f.bottom, pair, type)} → {fmtPrice(f.top, pair, type)}]
                  </span>
                  <span className="text-muted-foreground">strength {fmtNum(f.strength, 2)}{f.mitigated ? ' · MITIGATED' : ''}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Hard fail / soft warn / diagnostic */}
        {gates.canonicalPrimaryRejectReason && (
          <ReasonRow
            icon={<X className="w-3 h-3" />}
            className="text-short bg-short/10"
            label="Primary reject"
            items={[gates.canonicalPrimaryRejectReason]}
          />
        )}
        {gates.canonicalSecondaryRejectReasons.length > 0 && (
          <ReasonRow
            icon={<X className="w-3 h-3" />}
            className="text-short/80 bg-short/5"
            label="Secondary rejects"
            items={gates.canonicalSecondaryRejectReasons}
          />
        )}
        {canonicalReasons.length > 0 && (
          <ReasonRow
            icon={<X className="w-3 h-3" />}
            className="text-short bg-short/10"
            label="Canonical rejection"
            items={canonicalReasons}
          />
        )}
        {hardFails.length > 0 && (
          <ReasonRow icon={<X className="w-3 h-3" />} className="text-short bg-short/10" label="Hard fail" items={hardFails} />
        )}
        {softWarns.length > 0 && (
          <ReasonRow icon={<AlertTriangle className="w-3 h-3" />} className="text-warning bg-warning/10" label="Soft warning" items={softWarns} />
        )}
        {diagNotes.length > 0 && (
          <ReasonRow icon={<Info className="w-3 h-3" />} className="text-muted-foreground bg-muted/30" label="Diagnostic" items={diagNotes} />
        )}

        {data.no_trigger_classification && (
          <div className="text-[10px] text-warning">
            No-trigger classification: <span className="font-mono">{data.no_trigger_classification}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Gate({ label, ok, dangerLabel }: { label: string; ok: boolean; dangerLabel?: string }) {
  return (
    <div
      className={cn(
        'flex items-center gap-2 p-2 rounded-md',
        ok ? 'bg-long/10 text-long' : 'bg-short/10 text-short',
      )}
    >
      {ok ? <Check className="w-3.5 h-3.5" /> : <X className="w-3.5 h-3.5" />}
      <span className="text-xs font-medium flex-1">{label}</span>
      <span className="text-[10px] font-mono">{ok ? 'OK' : dangerLabel || 'FAIL'}</span>
    </div>
  );
}

function SmallStat({ label, value, accent, meta }: { label: string; value: string; accent?: 'short' | 'long' | 'muted' | 'primary'; meta?: string }) {
  const fg = accent === 'long' ? 'text-long' : accent === 'short' ? 'text-short' : accent === 'primary' ? 'text-primary' : 'text-foreground';
  return (
    <div className="p-2 rounded-md bg-muted/30">
      <p className="text-[10px] uppercase text-muted-foreground">{label}</p>
      <p className={cn('text-xs font-mono font-bold', fg)}>{value}</p>
      {meta && <p className="text-[9px] font-mono text-muted-foreground truncate">{meta}</p>}
    </div>
  );
}

function ReasonRow({
  icon,
  label,
  items,
  className,
}: {
  icon: React.ReactNode;
  label: string;
  items: string[];
  className: string;
}) {
  return (
    <div className={cn('p-2 rounded-md', className)}>
      <div className="flex items-center gap-1 text-[10px] uppercase font-semibold mb-1">
        {icon} {label}
      </div>
      <ul className="text-[10px] font-mono leading-relaxed list-disc pl-4">
        {items.slice(0, 6).map((it) => (
          <li key={it}>{it}</li>
        ))}
      </ul>
    </div>
  );
}
