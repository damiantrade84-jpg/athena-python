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
    <header className="h-10 border-b border-border/60 bg-card/80 backdrop-blur flex items-center justify-between px-4 shrink-0">
      <div className="flex items-center gap-3">
        <h1 className="text-xs font-bold tracking-wider font-mono text-primary">SENTINEL PRO</h1>
        <Badge variant="outline" className="text-[9px] h-4 px-1 border-primary/40 text-primary bg-primary/10">v4.0</Badge>
      </div>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5">
          <div className={`w-1.5 h-1.5 rounded-full ${statusColor} ${guardian.overall !== 'healthy' ? 'animate-pulse' : ''}`} />
          <span className="text-[10px] text-muted-foreground capitalize">{guardian.overall}</span>
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
