import { useStore } from '@/hooks/useStore';
import { useEffect, useState } from 'react';
import { Activity, Wifi } from 'lucide-react';
import { currentSegment, nextSegment, QUALITY_META, fmtCountdown } from '@/lib/primeWindows';
import MacroBadge from '@/components/shared/MacroBadge';

/** Status pill: a dot carries the state inside a soft glass chip. */
function StatusPill({
  dotColor,
  label,
  title,
  pulse,
}: {
  dotColor: string;
  label: string;
  title?: string;
  pulse?: boolean;
}) {
  return (
    <div className="status-pill" title={title}>
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${pulse ? 'animate-pulse' : ''}`}
        style={{ background: dotColor, boxShadow: `0 0 6px ${dotColor}` }}
      />
      <span className="tracking-tight">{label}</span>
    </div>
  );
}

export default function Header() {
  const { guardian } = useStore();
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const isHealthy = guardian.overall === 'healthy';
  const isWarning = guardian.overall === 'warning';
  const guardianDot = isHealthy
    ? 'hsl(var(--long))'
    : isWarning
    ? 'hsl(var(--warning))'
    : 'hsl(var(--short))';

  const seg = currentSegment(time);
  const next = nextSegment(time);
  const primeMeta = QUALITY_META[seg.quality];

  return (
    <header className="relative flex h-14 shrink-0 items-center justify-between border-b border-border/70 bg-sidebar/70 px-5 backdrop-blur-md">
      {/* Accent hairline under the header */}
      <div
        className="pointer-events-none absolute inset-x-0 -bottom-px h-px"
        style={{
          background:
            'linear-gradient(90deg, transparent, hsl(var(--primary) / 0.45), hsl(var(--primary-2) / 0.35), transparent)',
        }}
      />

      {/* ── Wordmark ── */}
      <div className="flex items-baseline gap-2">
        <span className="font-display text-gradient text-[16px] font-bold tracking-[0.18em]">
          SENTINEL
        </span>
        <span className="font-display text-[12px] font-medium tracking-[0.22em] text-muted-foreground">
          PRO
        </span>
        <span className="chip ml-1">v4.0</span>
      </div>

      {/* ── Status rail ── */}
      <div className="flex items-center gap-2.5">
        <MacroBadge />

        <StatusPill
          dotColor={primeMeta.color}
          label={`${primeMeta.label} · ${fmtCountdown(next.minutesUntil)}`}
          title={`${seg.label} · ${seg.markets} · next: ${next.segment.label} in ${fmtCountdown(next.minutesUntil)}`}
        />

        <StatusPill
          dotColor={guardianDot}
          label={`Guardian ${guardian.overall || 'unknown'}`}
          pulse={!isHealthy}
        />

        <div className="status-pill gap-3">
          <span className="flex items-center gap-1.5">
            <Activity className="h-3 w-3 text-primary/80" /> MT5
          </span>
          <span className="flex items-center gap-1.5">
            <Wifi className="h-3 w-3 text-primary/80" /> Bybit
          </span>
        </div>

        <span className="readout ml-1 text-[12px] font-medium text-foreground/90">
          {time.toLocaleTimeString()}
        </span>
      </div>
    </header>
  );
}
