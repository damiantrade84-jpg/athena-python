import { Check, X, AlertTriangle, Info } from 'lucide-react';
import { cn, fmtNum } from '@/lib/utils';
import { fmtLiveQuoteMeta, fmtPrice, engineBScoreBreakdown } from '@/lib/athenaFormat';
import { Chip, Details, DirectionChip, KeyValue, Metric } from '@/components/shared/primitives';
import {
  executableLevels,
  readEngineBCanonicalGatesFromNaked,
} from '@/lib/engineBCanonicalGates';
import type { ASEShadowContext, EngineBNakedResult } from '@/types/athena';
import ASESupportIndicator from './ASESupportIndicator';

interface Props {
  data: EngineBNakedResult | null | undefined;
  pair?: string;
  type?: string;
  livePrice?: number;
  livePriceAgeSec?: number;
  livePriceSource?: string;
  aseShadow?: ASEShadowContext | null;
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

export default function EngineBChecklistCard({
  data,
  pair,
  type,
  livePrice,
  livePriceAgeSec,
  livePriceSource,
  aseShadow,
}: Props) {
  if (!data) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 text-center text-xs text-muted-foreground">
        No Engine B result yet — run the Engine B scan.
      </div>
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

  const confidenceTone =
    gates.confidenceDisplayLabel === 'CONFIDENCE PASSED'
      ? 'long'
      : gates.confidenceDisplayLabel === 'SCORE PASSED / GATE FAILED'
        ? 'warning'
        : 'short';

  const gateRows: Array<{ label: string; ok: boolean; dangerLabel?: string }> = [
    { label: 'Structure', ok: gates.canonicalStructureOk },
    { label: 'Location', ok: gates.canonicalLocationOk },
    { label: 'Entry / Trigger', ok: gates.canonicalTriggerOk },
    { label: 'Room / RR', ok: gates.canonicalRoomRrOk },
  ];
  if (checklist.macro_ok != null) gateRows.push({ label: 'Macro', ok: macro_ok });
  if (d1_conflict != null) {
    gateRows.push({
      label: 'D1 Conflict',
      ok: !d1_conflict || d1_conflict === 'no_conflict',
      dangerLabel: typeof d1_conflict === 'string' ? d1_conflict : 'CONFLICT',
    });
  }

  const hasReasons =
    Boolean(gates.canonicalPrimaryRejectReason)
    || gates.canonicalSecondaryRejectReasons.length > 0
    || canonicalReasons.length > 0
    || hardFails.length > 0
    || softWarns.length > 0
    || diagNotes.length > 0;

  return (
    <div className="rounded-lg border border-border bg-card">
      {/* ── Verdict header ── */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-3 py-2">
        <Chip tone={confidenceTone}>{gates.confidenceDisplayLabel}</Chip>
        {!gates.canonicalTradeOk && <Chip tone="short">NO ENTRY</Chip>}
        {data.direction && <DirectionChip direction={data.direction} />}
        {data.style && <Chip>{data.style}</Chip>}
        {gates.canonicalStatus && (
          <Chip tone={gates.canonicalTradeOk ? 'long' : 'short'}>{gates.canonicalStatus}</Chip>
        )}
        {profileActive && profileOk && typeof profilePoints === 'number' && profilePoints > 0 && (
          <Chip tone="long">Profile +{fmtNum(profilePoints, 2)}</Chip>
        )}
        <span className="readout ml-auto shrink-0 text-[11px] text-muted-foreground">
          Gate {fmtNum(score, 2)} / {fmtNum(max, 2)}
        </span>
      </div>

      <div className="space-y-3 p-3">
        {/* ── The four canonical gates: the primary read ── */}
        <div className="grid grid-cols-2 gap-1.5">
          {gateRows.map(g => (
            <Gate key={g.label} label={g.label} ok={g.ok} dangerLabel={g.dangerLabel} />
          ))}
        </div>

        {aseShadow && <ASESupportIndicator shadow={aseShadow} />}

        {/* ── Executable levels ── */}
        <div className="grid grid-cols-4 gap-3">
          <Metric
            label="Live"
            value={fmtPrice(livePrice, pair, type)}
            tone="accent"
            meta={fmtLiveQuoteMeta(livePriceAgeSec, livePriceSource)}
          />
          <Metric
            label="Entry"
            value={levels.showExecutable ? fmtPrice(levels.entry, pair, type) : '—'}
          />
          <Metric
            label="SL"
            value={levels.showExecutable ? fmtPrice(levels.sl, pair, type) : '—'}
            tone="short"
          />
          <Metric
            label="TP"
            value={levels.showExecutable ? fmtPrice(levels.tp, pair, type) : '—'}
            tone="long"
          />
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <Chip
            tone={
              levels.showExecutable && tp1Rr != null && (tp1MinRr == null || tp1Rr >= tp1MinRr)
                ? 'long' : 'default'
            }
          >
            TP1 R:R {levels.showExecutable ? fmtNum(tp1Rr, 2) : '—'}
            {tp1MinRr != null ? ` ≥ ${fmtNum(tp1MinRr, 2)}` : ''}
          </Chip>
          <Chip
            tone={
              levels.showExecutable && runnerRr != null && (runnerMinRr == null || runnerRr >= runnerMinRr)
                ? 'long' : 'default'
            }
          >
            Runner R:R {levels.showExecutable ? fmtNum(runnerRr, 2) : '—'}
            {runnerMinRr != null ? ` ≥ ${fmtNum(runnerMinRr, 2)}` : ''}
          </Chip>
          {rrUsedForGate != null && rrUsedForGate !== runnerRr && (
            <Chip>Gate R:R {fmtNum(rrUsedForGate, 2)}</Chip>
          )}
        </div>

        {/* ── State that changes what you do stays uncollapsed ── */}
        {breakdown?.scoreFloorPasses && !breakdown.confidencePasses && (
          <p className="note text-warning">
            Total clears the score floor; one or more mandatory gates failed.
          </p>
        )}
        {gates.canonicalPrimaryRejectReason && (
          <ReasonBlock
            icon={<X className="h-3 w-3" />}
            tone="short"
            label="Primary reject"
            items={[gates.canonicalPrimaryRejectReason]}
          />
        )}
        {data.no_trigger_classification && (
          <p className="note text-warning">
            No-trigger classification: <span className="readout">{data.no_trigger_classification}</span>
          </p>
        )}
        {!levels.showExecutable
          && (levels.diagnosticEntry != null || levels.diagnosticSl != null || levels.diagnosticTp != null) && (
          <div className="rounded border border-warning/30 bg-warning/5 p-2">
            <p className="label mb-1 text-warning">Rejected diagnostic levels — not executable</p>
            <p className="readout text-[11px] text-muted-foreground">
              Entry {fmtPrice(levels.diagnosticEntry, pair, type)}
              {' · '}SL {fmtPrice(levels.diagnosticSl, pair, type)}
              {' · '}TP {fmtPrice(levels.diagnosticTp, pair, type)}
            </p>
          </div>
        )}

        {/* ══ Structure detail, score composition, and the reason lists ══ */}
        <Details summary="Details" className="border-t border-border pt-0.5">
          <div className="space-y-3">
            <div className="space-y-0.5">
              <KeyValue label="Structural verdict" value={String(verdict)} />
              <KeyValue label="Swing sequence" value={String(data.current_swing_sequence || '—')} />
              <KeyValue label="Macro sequence" value={String(data.macro_swing_sequence || '—')} />
            </div>

            <div className="space-y-0.5">
              <KeyValue label="Gate score" value={`${fmtNum(score, 2)} / ${fmtNum(max, 2)}`} />
              {totalScore != null && (
                <KeyValue
                  label="Total score"
                  value={`${fmtNum(totalScore, 2)} / ${fmtNum(totalMax, 2)}`}
                  meta={minScore != null ? `min ${fmtNum(minScore, 2)}` : undefined}
                />
              )}
              {breakdown?.bonusPoints != null && breakdown.bonusPoints !== 0 && (
                <KeyValue
                  label="Bonus"
                  value={`${breakdown.bonusPoints >= 0 ? '+' : ''}${fmtNum(breakdown.bonusPoints, 2)}`}
                  tone={breakdown.bonusPoints >= 0 ? 'long' : 'short'}
                />
              )}
            </div>

            {profileUnavailable && (
              <p className="note">
                Volume profile (POC/VAH/VAL) not used for this asset — unreliable volume feed.
              </p>
            )}

            {data.active_fvgs && data.active_fvgs.length > 0 && (
              <div>
                <p className="label mb-1">Active FVGs</p>
                <div className="space-y-0.5">
                  {data.active_fvgs.slice(0, 4).map((f, i) => (
                    <KeyValue
                      key={i}
                      label={`${f.direction || ''} ${fmtPrice(f.bottom, pair, type)} → ${fmtPrice(f.top, pair, type)}`}
                      value={fmtNum(f.strength, 2)}
                      meta={f.mitigated ? 'mitigated' : undefined}
                    />
                  ))}
                </div>
              </div>
            )}

            {hasReasons && (
              <div className="space-y-2">
                {gates.canonicalSecondaryRejectReasons.length > 0 && (
                  <ReasonBlock
                    icon={<X className="h-3 w-3" />}
                    tone="short"
                    label="Secondary rejects"
                    items={gates.canonicalSecondaryRejectReasons}
                  />
                )}
                {canonicalReasons.length > 0 && (
                  <ReasonBlock
                    icon={<X className="h-3 w-3" />}
                    tone="short"
                    label="Canonical rejection"
                    items={canonicalReasons}
                  />
                )}
                {hardFails.length > 0 && (
                  <ReasonBlock
                    icon={<X className="h-3 w-3" />}
                    tone="short"
                    label="Hard fail"
                    items={hardFails}
                  />
                )}
                {softWarns.length > 0 && (
                  <ReasonBlock
                    icon={<AlertTriangle className="h-3 w-3" />}
                    tone="warning"
                    label="Soft warning"
                    items={softWarns}
                  />
                )}
                {diagNotes.length > 0 && (
                  <ReasonBlock
                    icon={<Info className="h-3 w-3" />}
                    tone="muted"
                    label="Diagnostic"
                    items={diagNotes}
                  />
                )}
              </div>
            )}
          </div>
        </Details>
      </div>
    </div>
  );
}

/**
 * A pass/fail gate. Reads as a checklist line, not a filled colour block —
 * the icon and the trailing state carry the signal, so six of them side by
 * side no longer form a wall of green/red panels.
 */
function Gate({ label, ok, dangerLabel }: { label: string; ok: boolean; dangerLabel?: string }) {
  return (
    <div className="flex items-center gap-2 rounded border border-border px-2 py-1.5">
      {ok
        ? <Check className="h-3.5 w-3.5 shrink-0 text-long" />
        : <X className="h-3.5 w-3.5 shrink-0 text-short" />}
      <span className="flex-1 truncate text-xs text-foreground">{label}</span>
      <span className={cn('readout shrink-0 text-[10px]', ok ? 'text-long' : 'text-short')}>
        {ok ? 'OK' : dangerLabel || 'FAIL'}
      </span>
    </div>
  );
}

function ReasonBlock({
  icon,
  label,
  items,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  items: string[];
  tone: 'short' | 'warning' | 'muted';
}) {
  const toneClass =
    tone === 'short' ? 'text-short' : tone === 'warning' ? 'text-warning' : 'text-muted-foreground';
  return (
    <div className="rounded border border-border p-2">
      <div className={cn('label mb-1 flex items-center gap-1', toneClass)}>
        {icon} {label}
      </div>
      <ul className="space-y-0.5">
        {items.slice(0, 6).map((it) => (
          <li key={it} className="readout text-[11px] leading-relaxed text-muted-foreground">
            {it}
          </li>
        ))}
      </ul>
    </div>
  );
}
