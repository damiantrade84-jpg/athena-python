import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Hourglass } from 'lucide-react';
import { useNowTick } from '@/hooks/useNowTick';
import {
  QUALITY_SEGMENTS, QUALITY_META,
  currentSegment, nextSegment, groupStatuses,
  refMinutesOfDay, refMinToLocalLabel, fmtCountdown,
} from '@/lib/primeWindows';

const MIN_PER_DAY = 1440;

const SEGMENT_FILL: Record<string, string> = {
  prime: 'linear-gradient(180deg, hsl(var(--gold-light) / 0.95), hsl(var(--gold) / 0.60))',
  high: 'linear-gradient(180deg, hsl(var(--long) / 0.60), hsl(var(--long) / 0.35))',
  medium: 'hsl(var(--warning) / 0.28)',
  low: 'hsl(var(--muted) / 0.40)',
  avoid: 'hsl(var(--short) / 0.15)',
};

export default function PrimeTimeCard() {
  const now = useNowTick(30_000);
  const seg = currentSegment(now);
  const next = nextSegment(now);
  const groups = groupStatuses(now);
  const meta = QUALITY_META[seg.quality];
  const nowPct = (refMinutesOfDay(now) / MIN_PER_DAY) * 100;
  const isPrime = seg.quality === 'prime';

  return (
    <Card
      className="border-border/70 relative overflow-hidden stat-card-top-line"
      style={{
        background: 'linear-gradient(180deg, hsl(var(--card) / 0.92), hsl(var(--background) / 0.55))',
        backdropFilter: 'blur(8px)',
      }}
    >
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
            <Hourglass className="w-4 h-4 text-primary" /> Prime Execution Windows
          </CardTitle>
          <div
            className={`flex items-center gap-2 px-3 py-1 rounded-md border text-[10px] font-mono tracking-[0.2em] ${isPrime ? 'animate-glow-gold' : ''}`}
            style={{ color: meta.color, background: meta.bg, borderColor: meta.border }}
            title={`${seg.label} · ${seg.markets}`}
          >
            <span className={`w-1.5 h-1.5 rounded-full bg-current ${isPrime ? 'animate-pulse' : ''}`} />
            {meta.label} WINDOW
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Current window hero */}
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div className="min-w-0">
            <p className="text-lg font-bold leading-tight" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.06em', color: meta.color }}>
              {seg.label}
            </p>
            <p className="text-[11px] text-muted-foreground mt-0.5">{seg.markets}</p>
          </div>
          <div className="text-right shrink-0">
            <p className="text-[11px] font-mono" style={{ color: meta.color }}>
              ends {refMinToLocalLabel(seg.endMin)} · in {fmtCountdown(next.minutesUntil)}
            </p>
            <p className="text-[10px] text-muted-foreground font-mono mt-0.5">
              next: {next.segment.label} ({QUALITY_META[next.segment.quality].label}) {refMinToLocalLabel(next.segment.startMin)}
            </p>
          </div>
        </div>

        {/* 24h quality timeline */}
        <div>
          <div className="relative">
            <div className="flex h-3 rounded-full overflow-hidden border border-border/50">
              {QUALITY_SEGMENTS.map(s => (
                <div
                  key={s.startMin}
                  className="h-full"
                  style={{
                    width: `${((s.endMin - s.startMin) / MIN_PER_DAY) * 100}%`,
                    background: SEGMENT_FILL[s.quality],
                  }}
                  title={`${s.label} · ${QUALITY_META[s.quality].label} · ${refMinToLocalLabel(s.startMin)}–${refMinToLocalLabel(s.endMin)} · ${s.markets}`}
                />
              ))}
            </div>
            {/* Now cursor */}
            <div
              className="absolute -top-1 -bottom-1 w-[2px] rounded-full pointer-events-none"
              style={{
                left: `${nowPct}%`,
                background: 'hsl(var(--foreground))',
                boxShadow: '0 0 8px hsl(var(--gold) / 0.9), 0 0 2px hsl(var(--foreground))',
              }}
            />
          </div>
          <div className="relative h-4 mt-1">
            {[0, 3, 6, 9, 12, 15, 18, 21].map(hour => (
              <span
                key={hour}
                className="absolute text-[9px] font-mono text-muted-foreground -translate-x-1/2"
                style={{ left: `${(hour / 24) * 100}%` }}
              >
                {refMinToLocalLabel(hour * 60)}
              </span>
            ))}
          </div>
        </div>

        {/* Per-asset-group status */}
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-2">
          {groups.map(({ group, active, minutes, next: nextWin }) => {
            const live = active != null;
            const livePrime = live && active.tier === 'prime';
            const dotColor = live ? 'hsl(var(--gold-light))' : 'hsl(var(--muted-foreground) / 0.5)';
            return (
              <div
                key={group.id}
                className="flex items-center justify-between gap-2 px-2.5 py-2 rounded-lg border transition-colors"
                style={{
                  borderColor: livePrime ? 'hsl(var(--gold) / 0.40)' : live ? 'hsl(var(--gold) / 0.22)' : 'hsl(var(--border) / 0.40)',
                  background: livePrime ? 'hsl(var(--gold) / 0.10)' : live ? 'hsl(var(--gold) / 0.05)' : 'hsl(var(--secondary) / 0.35)',
                }}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className={`w-1.5 h-1.5 rounded-full shrink-0 ${livePrime ? 'animate-pulse' : ''}`}
                    style={{ background: dotColor, boxShadow: live ? '0 0 6px hsl(var(--gold) / 0.7)' : 'none' }}
                  />
                  <span className="text-[10px] font-medium uppercase tracking-wide truncate">{group.label}</span>
                </div>
                <span className="text-[9px] font-mono shrink-0 text-right" style={{ color: live ? 'hsl(var(--gold-light))' : 'hsl(var(--muted-foreground))' }}>
                  {live
                    ? `${livePrime ? 'PRIME' : 'ACTIVE'} · ends ${refMinToLocalLabel(active.endMin)}`
                    : `next ${refMinToLocalLabel(nextWin.startMin)} · in ${fmtCountdown(minutes)}`}
                </span>
              </div>
            );
          })}
        </div>

        <p className="text-[9px] text-muted-foreground/70">
          Liquidity windows only — setup quality, freshness, spread, RR and all gates still apply.
          Anchored GMT+2 (summer DST alignment); times shown in your local clock.
        </p>
      </CardContent>
    </Card>
  );
}
