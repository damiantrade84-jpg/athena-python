import { useStore } from '@/hooks/useStore';
import { Badge } from '@/components/ui/badge';
import { useEffect, useState } from 'react';
import { Activity, Wifi } from 'lucide-react';

export default function Header() {
  const { guardian } = useStore();
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const isHealthy = guardian.overall === 'healthy';
  const isWarning = guardian.overall === 'warning';
  const guardianColor = isHealthy
    ? 'hsl(var(--long))'
    : isWarning
    ? 'hsl(var(--warning))'
    : 'hsl(var(--short))';
  const guardianBg = isHealthy
    ? 'hsl(var(--long) / 0.08)'
    : isWarning
    ? 'hsl(var(--warning) / 0.08)'
    : 'hsl(var(--short) / 0.08)';
  const guardianBorder = isHealthy
    ? 'hsl(var(--long) / 0.30)'
    : isWarning
    ? 'hsl(var(--warning) / 0.30)'
    : 'hsl(var(--short) / 0.30)';

  return (
    <header
      className="h-11 border-b border-border flex items-center justify-between px-5 shrink-0 relative header-gold-line"
      style={{ background: 'hsl(248 45% 5%)' }}
    >
      {/* Left — Logo */}
      <div className="flex items-center gap-3">
        <h1
          className="text-sm font-bold tracking-widest"
          style={{
            fontFamily: "'Rajdhani', sans-serif",
            letterSpacing: '0.14em',
            color: 'hsl(var(--gold-light))',
            textShadow: '0 0 14px hsl(43 90% 46% / 0.45)',
          }}
        >
          SENTINEL PRO
        </h1>
        <Badge
          variant="outline"
          className="text-[9px] h-4 px-1"
          style={{
            borderColor: 'hsl(var(--gold) / 0.50)',
            color: 'hsl(var(--gold-light))',
            background: 'hsl(var(--gold) / 0.10)',
          }}
        >
          v4.0
        </Badge>
      </div>

      {/* Right — Status chips */}
      <div className="flex items-center gap-4">
        {/* Guardian status pill */}
        <div
          className="flex items-center gap-2 px-2.5 py-1 rounded-md border text-[10px] font-mono tracking-wider"
          style={{ background: guardianBg, borderColor: guardianBorder, color: guardianColor }}
        >
          <div
            className={`w-1.5 h-1.5 rounded-full bg-current ${!isHealthy ? 'animate-pulse' : ''}`}
            style={isHealthy ? { boxShadow: '0 0 5px hsl(var(--long) / 0.7)' } : {}}
          />
          GUARDIAN · {guardian.overall?.toUpperCase() || 'UNKNOWN'}
        </div>

        {/* MT5 chip */}
        <div className="flex items-center gap-1.5">
          <Activity className="w-3 h-3" style={{ color: 'hsl(var(--muted-foreground))' }} />
          <span className="text-[10px]" style={{ color: 'hsl(var(--muted-foreground))' }}>MT5</span>
        </div>

        {/* Bybit chip */}
        <div className="flex items-center gap-1.5">
          <Wifi className="w-3 h-3" style={{ color: 'hsl(var(--muted-foreground))' }} />
          <span className="text-[10px]" style={{ color: 'hsl(var(--muted-foreground))' }}>Bybit</span>
        </div>

        {/* Clock */}
        <span
          className="text-[10px] font-mono"
          style={{ color: 'hsl(var(--gold) / 0.70)' }}
        >
          {time.toLocaleTimeString()}
        </span>
      </div>
    </header>
  );
}
