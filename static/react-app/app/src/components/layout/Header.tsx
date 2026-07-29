import { useStore } from '@/hooks/useStore';
import { useEffect, useState } from 'react';
import { Activity, Wifi } from 'lucide-react';
import { currentSegment, nextSegment, QUALITY_META, fmtCountdown } from '@/lib/primeWindows';
import MacroBadge from '@/components/shared/MacroBadge';

/** Status pill: a 5px dot carries the state, the label stays neutral ink. */
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
    <div
      className="flex items-center gap-1.5 text-[11px] text-muted-foreground"
      title={title}
    >
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${pulse ? 'animate-pulse' : ''}`}
        style={{ background: dotColor }}
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
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-sidebar px-4">
      {/* ── Wordmark ── */}
      <div className="flex items-baseline gap-2">
        <span className="text-[13px] font-semibold tracking-[0.16em] text-foreground">
          SENTINEL
        </span>
        <span className="text-[11px] tracking-[0.16em] text-muted-foreground">PRO</span>
        <span className="text-[10px] tracking-wide text-muted-foreground/70">v4.0</span>
      </div>

      {/* ── Status rail ── */}
      <div className="flex items-center gap-5">
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

        <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <Activity className="h-3 w-3" /> MT5
          </span>
          <span className="flex items-center gap-1.5">
            <Wifi className="h-3 w-3" /> Bybit
          </span>
        </div>

        <span className="readout text-[11px] text-muted-foreground">
          {time.toLocaleTimeString()}
        </span>
      </div>
    </header>
  );
}
