import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Check, X, AlertTriangle, Info } from 'lucide-react';
import { cn, fmtNum } from '@/lib/utils';
import { fmtLiveQuoteMeta, fmtPrice } from '@/lib/athenaFormat';
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
  const checklist = (data.checklist || conf.checklist || {}) as Record<string, unknown>;
  const passed = Boolean(conf.passed ?? data.passed);
  const score = (conf.score ?? data.confidence_score ?? data.score) as number | undefined;
  const max = (conf.max_score ?? data.confidence_max ?? data.max_score) as number | undefined;
  const minScore = data.min_score as number | undefined;
  const minRr = data.min_rr as number | undefined;
  const verdict = data.structural_verdict || '—';
  const dirBg =
    data.direction === 'LONG' ? 'bg-long/20 text-long' : data.direction === 'SHORT' ? 'bg-short/20 text-short' : 'bg-muted/40 text-muted-foreground';

  // Read all the checklist fields with both shapes (analyze_pair vs naked-analysis)
  const struct_ok = Boolean(checklist.structure_ok ?? data.structure_ok);
  const loc_ok = Boolean(checklist.location_ok ?? data.location_ok);
  const entry_ok = Boolean(checklist.entry_ok ?? checklist.trigger_ok ?? data.entry_ok);
  const room_ok = Boolean(checklist.room_rr_ok ?? checklist.room_ok ?? data.room_rr_ok);
  const rr_ok = Boolean(checklist.rr_ok ?? data.rr);
  const macro_ok = Boolean(checklist.macro_ok);
  const d1_conflict = (checklist.d1_conflict ?? data.d1_conflict) as unknown;

  const hardFails =
    (conf.hard_fail_reasons as string[] | undefined) || (data.hard_fail_reasons as string[] | undefined) || [];
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

  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className={compact ? 'p-3 space-y-2' : 'p-4 space-y-3'}>
        {/* Header */}
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            <Badge className={cn('text-[10px]', passed ? 'bg-long/20 text-long' : 'bg-short/20 text-short')}>
              {passed ? 'CONFIDENCE PASSED' : 'CONFIDENCE FAILED'}
            </Badge>
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
          </div>
          <div className="text-[10px] text-muted-foreground font-mono">
            {fmtNum(score, 2)} / {fmtNum(max, 2)} {minScore != null && <span>(min {fmtNum(minScore, 2)})</span>}
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

        {/* Checklist gates */}
        <div className="grid grid-cols-2 gap-2">
          <Gate label="Structure" ok={struct_ok} />
          <Gate label="Location" ok={loc_ok} />
          <Gate label="Entry / Trigger" ok={entry_ok} />
          <Gate label="Room / RR" ok={room_ok || rr_ok} />
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
        {(data.entry != null || data.sl != null || data.tp != null) && (
          <div className="grid grid-cols-4 gap-2">
            <SmallStat label="Live" value={fmtPrice(livePrice, pair, type)} accent="primary" meta={fmtLiveQuoteMeta(livePriceAgeSec, livePriceSource)} />
            <SmallStat label="Entry" value={fmtPrice(data.entry, pair, type)} />
            <SmallStat label="SL" value={fmtPrice(data.sl, pair, type)} accent="short" />
            <SmallStat label="TP" value={fmtPrice(data.tp, pair, type)} accent="long" />
            <SmallStat label="R:R" value={fmtNum(data.rr, 2)} accent={typeof data.rr === 'number' && typeof minRr === 'number' && data.rr >= minRr ? 'long' : 'muted'} />
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
