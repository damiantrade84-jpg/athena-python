import { useStore } from '@/hooks/useStore';
import { Badge } from '@/components/ui/badge';
import { useEffect, useState } from 'react';
import { Activity, Wifi, WifiOff } from 'lucide-react';

export default function Header() {
  const { guardian } = useStore();
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const statusColor =
    guardian.overall === 'healthy' ? 'bg-long' :
    guardian.overall === 'warning' ? 'bg-warning' : 'bg-short';

  return (
    <header className="h-11 border-b border-border bg-card flex items-center justify-between px-5 shrink-0 relative">
      <div className="absolute bottom-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-primary/25 to-transparent pointer-events-none" />
      <div className="flex items-center gap-3">
        <h1 className="text-sm font-bold tracking-widest font-sans text-foreground" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.14em' }}>SENTINEL PRO</h1>
        <Badge variant="outline" className="text-[9px] h-4 px-1 border-primary/40 text-primary bg-primary/10">v4.0</Badge>
      </div>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 px-2.5 py-1 rounded-sm border text-[10px] font-mono tracking-wider"
          style={{ background: 'hsl(var(--long) / 0.08)', borderColor: 'hsl(var(--long) / 0.25)', color: 'hsl(var(--long))' }}>
          <div className={`w-1.5 h-1.5 rounded-full bg-current ${guardian.overall !== 'healthy' ? 'animate-pulse' : ''}`} />
          GUARDIAN · {guardian.overall?.toUpperCase() || 'UNKNOWN'}
        </div>
        <div className="flex items-center gap-1.5">
          <Activity className="w-3 h-3 text-muted-foreground" />
          <span className="text-[10px] text-muted-foreground">MT5</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Wifi className="w-3 h-3 text-muted-foreground" />
          <span className="text-[10px] text-muted-foreground">Bybit</span>
        </div>
        <span className="text-[10px] font-mono text-muted-foreground">
          {time.toLocaleTimeString()}
        </span>
      </div>
    </header>
  );
}
