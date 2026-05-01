import { useStore } from '@/hooks/useStore';
import type { PanelId } from '@/types';
import { cn, fmtNum, toNum } from '@/lib/utils';
import { Separator } from '@/components/ui/separator';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Switch } from '@/components/ui/switch';
import {
  LayoutDashboard, Zap, Search, Settings, TrendingUp,
  Layers, Activity, BarChart2, FlaskConical, Filter, Ticket,
  Microscope, PieChart, Globe, ShieldCheck, Radio,
} from 'lucide-react';

const navItems: { id: PanelId; label: string; icon: React.ElementType; badge?: string }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'signals', label: 'Signals', icon: Zap, badge: 'LIVE' },
  { id: 'pairBrowser', label: 'Pair Browser', icon: Search },
  { id: 'liveCockpit', label: 'Live Cockpit', icon: Radio, badge: 'CYBER' },
  { id: 'scanConfig', label: 'Scan Config', icon: Settings },
  { id: 'trades', label: 'Trades', icon: TrendingUp },
  { id: 'engineC', label: 'Engine C', icon: Layers },
  { id: 'scalpLab', label: 'Scalp Lab', icon: Activity },
  { id: 'tvChart', label: 'TV Chart', icon: BarChart2 },
  { id: 'backtest', label: 'Backtest', icon: FlaskConical },
  { id: 'screener', label: 'Screener', icon: Filter },
  { id: 'lotteryLab', label: 'Lottery Lab', icon: Ticket },
  { id: 'researchLab', label: 'Research Lab', icon: Microscope },
  { id: 'performance', label: 'Performance', icon: PieChart },
  { id: 'markets', label: 'Markets', icon: Globe },
  { id: 'guardian', label: 'Guardian', icon: ShieldCheck },
];

export default function Sidebar() {
  const { activePanel, setActivePanel, signals, positions, guardian, isAutoTrade, toggleAutoTrade, isTestMode, toggleTestMode } = useStore();

  const activeSignals = signals.filter(s => s.status === 'active').length;
  const openPositions = positions.filter(p => p.status === 'open').length;
  const dailyLoss = toNum(guardian?.dailyLoss);
  const dailyLossLimit = toNum(guardian?.dailyLossLimit, 1);

  return (
    <aside className="w-[210px] shrink-0 border-r border-border/60 bg-sidebar flex flex-col">
      {/* Logo */}
      <div className="p-3 border-b border-sidebar-border/40">
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold tracking-wider font-mono text-primary">SENTINEL PRO</span>
          <Badge variant="outline" className="text-[9px] h-4 px-1 border-primary/40 text-primary bg-primary/10">v4.0</Badge>
        </div>
      </div>

      {/* Stats Bar */}
      <div className="p-3 border-b border-sidebar-border/40">
        <div className="grid grid-cols-2 gap-2">
          <div className="bg-sidebar-accent/50 rounded-md p-2">
            <div className="text-[10px] text-sidebar-foreground/60 uppercase tracking-wider">Signals</div>
            <div className="text-lg font-mono font-bold text-primary leading-tight">{activeSignals}</div>
          </div>
          <div className="bg-sidebar-accent/50 rounded-md p-2">
            <div className="text-[10px] text-sidebar-foreground/60 uppercase tracking-wider">Positions</div>
            <div className="text-lg font-mono font-bold text-primary leading-tight">{openPositions}</div>
          </div>
          <div className="bg-sidebar-accent/50 rounded-md p-2">
            <div className="text-[10px] text-sidebar-foreground/60 uppercase tracking-wider">Daily P&L</div>
            <div className={`text-sm font-mono font-bold leading-tight ${dailyLoss >= 0 ? 'text-long' : 'text-short'}`}>
              {dailyLoss >= 0 ? '+' : ''}${fmtNum(dailyLoss, 0, '0')}
            </div>
          </div>
          <div className="bg-sidebar-accent/50 rounded-md p-2">
            <div className="text-[10px] text-sidebar-foreground/60 uppercase tracking-wider">Win Rate</div>
            <div className="text-sm font-mono font-bold text-primary leading-tight">
              57.3%
            </div>
          </div>
        </div>
      </div>

      <Separator className="bg-sidebar-border/40" />

      {/* Navigation */}
      <ScrollArea className="flex-1 py-2">
        <nav className="px-2 space-y-0.5">
          {navItems.map(item => {
            const isActive = activePanel === item.id;
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                onClick={() => setActivePanel(item.id)}
                className={cn(
                  'w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-xs font-medium transition-all duration-150 group relative',
                  isActive
                    ? 'bg-sidebar-primary/15 text-sidebar-primary border-l-2 border-sidebar-primary'
                    : 'text-sidebar-foreground/70 hover:text-sidebar-foreground hover:bg-sidebar-accent/50 border-l-2 border-transparent'
                )}
              >
                <Icon className={cn(
                  'w-4 h-4 shrink-0 transition-colors',
                  isActive ? 'text-sidebar-primary' : 'text-sidebar-foreground/50 group-hover:text-sidebar-foreground/80'
                )} />
                <span className="flex-1 text-left">{item.label}</span>
                {item.badge && (
                  <Badge variant="outline" className="text-[9px] h-4 px-1 border-primary/40 text-primary bg-primary/10">
                    {item.badge}
                  </Badge>
                )}
                {item.id === 'signals' && activeSignals > 0 && (
                  <span className="w-4 h-4 rounded-full bg-primary/20 text-primary text-[9px] flex items-center justify-center font-mono">
                    {activeSignals}
                  </span>
                )}
                {item.id === 'trades' && openPositions > 0 && (
                  <span className="w-4 h-4 rounded-full bg-primary/20 text-primary text-[9px] flex items-center justify-center font-mono">
                    {openPositions}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </ScrollArea>

      <Separator className="bg-sidebar-border/40" />

      {/* Footer Controls */}
      <div className="p-3 space-y-3">
        <div className="bg-sidebar-accent/30 rounded-md p-2.5 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-sidebar-foreground/60">Circuit Breaker</span>
            <div className={`w-1.5 h-1.5 rounded-full ${guardian?.circuitBreaker ? 'bg-short animate-pulse' : 'bg-long'}`} />
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-sidebar-foreground/60">Daily Risk</span>
            <span className="text-[10px] font-mono text-sidebar-foreground/80">
              ${fmtNum(dailyLoss, 0, '0')}/${fmtNum(dailyLossLimit, 0, '0')}
            </span>
          </div>
          <div className="w-full bg-sidebar-border/30 rounded-full h-1 overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500 bg-primary/70"
              style={{ width: `${Math.min(100, (Math.abs(dailyLoss) / Math.max(1, dailyLossLimit)) * 100)}%` }}
            />
          </div>
        </div>

        <div className="flex items-center justify-between px-1">
          <span className="text-[10px] text-sidebar-foreground/60">Auto-Trade</span>
          <Switch checked={isAutoTrade} onCheckedChange={toggleAutoTrade} />
        </div>
        <div className="flex items-center justify-between px-1">
          <span className="text-[10px] text-sidebar-foreground/60">Test Mode</span>
          <Switch checked={isTestMode} onCheckedChange={toggleTestMode} />
        </div>
      </div>
    </aside>
  );
}
