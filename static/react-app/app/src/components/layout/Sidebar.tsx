import { useCallback, type ElementType } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
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

const navItems: { id: PanelId; label: string; icon: ElementType; badge?: string }[] = [
  { id: 'dashboard',   label: 'Dashboard',   icon: LayoutDashboard },
  { id: 'signals',     label: 'Signals',      icon: Zap,        badge: 'LIVE' },
  { id: 'pairBrowser', label: 'Pair Browser', icon: Search },
  { id: 'liveCockpit', label: 'Live Cockpit', icon: Radio,      badge: 'CYBER' },
  { id: 'scanConfig',  label: 'Scan Config',  icon: Settings },
  { id: 'trades',      label: 'Trades',       icon: TrendingUp },
  { id: 'engineC',     label: 'Engine C',     icon: Layers },
  { id: 'scalpLab',    label: 'Scalp Lab',    icon: Activity },
  { id: 'tvChart',     label: 'TV Chart',     icon: BarChart2 },
  { id: 'backtest',    label: 'Backtest',     icon: FlaskConical },
  { id: 'screener',    label: 'Screener',     icon: Filter },
  { id: 'lotteryLab',  label: 'Lottery Lab',  icon: Ticket },
  { id: 'researchLab', label: 'Research Lab', icon: Microscope },
  { id: 'performance', label: 'Performance',  icon: PieChart },
  { id: 'markets',     label: 'Markets',      icon: Globe },
  { id: 'guardian',    label: 'Guardian',     icon: ShieldCheck },
];

export default function Sidebar() {
  const { activePanel, setActivePanel, signals, positions, guardian, showToast, isTestMode, toggleTestMode } = useStore();
  const { data: autoTrade, refresh: refreshAutoTrade } = useApiPoll<{ enabled: boolean }>('/api/auto-trade', 15000);
  const { post: postAutoTrade, loading: togglingAutoTrade } = useApiPost<{ enabled: boolean; error?: string }>();

  const activeSignals  = signals.filter(s => s.status === 'active').length;
  const openPositions  = positions.filter(p => p.status === 'open').length;
  const dailyLoss      = toNum(guardian?.dailyLoss);
  const dailyLossLimit = toNum(guardian?.dailyLossLimit, 1);
  const riskPct        = Math.min(100, (Math.abs(dailyLoss) / Math.max(1, dailyLossLimit)) * 100);
  const serverAutoTradeEnabled = Boolean(autoTrade?.enabled);

  const handleAutoTradeToggle = useCallback(async (checked: boolean) => {
    const result = await postAutoTrade('/api/auto-trade', { action: checked ? 'on' : 'off' });
    if (!result) {
      showToast('Auto-trade toggle failed: server did not confirm state', 'error');
      return;
    }
    await refreshAutoTrade();
    showToast(result.enabled ? 'Auto-trade enabled on server' : 'Auto-trade disabled on server', 'info');
  }, [postAutoTrade, refreshAutoTrade, showToast]);

  return (
    <aside className="w-[210px] shrink-0 border-r border-sidebar-border bg-sidebar flex flex-col">

      {/* ── Logo ── */}
      <div className="p-4 border-b border-sidebar-border/60">
        <div className="flex items-center gap-2">
          <span
            className="text-sm font-bold tracking-widest"
            style={{
              fontFamily: "'Rajdhani', sans-serif",
              letterSpacing: '0.14em',
              color: 'hsl(var(--gold-light))',
              textShadow: '0 0 12px hsl(43 90% 46% / 0.5)',
            }}
          >
            SENTINEL PRO
          </span>
          <Badge
            variant="outline"
            className="text-[9px] h-4 px-1"
            style={{ borderColor: 'hsl(var(--gold) / 0.5)', color: 'hsl(var(--gold-light))', background: 'hsl(var(--gold) / 0.10)' }}
          >
            v4.0
          </Badge>
        </div>
      </div>

      {/* ── Stats Bar ── */}
      <div className="p-3 border-b border-sidebar-border/40">
        <div className="grid grid-cols-2 gap-2">
          {[
            { label: 'Signals',   value: activeSignals },
            { label: 'Positions', value: openPositions },
            {
              label: 'Daily P&L',
              value: `${dailyLoss >= 0 ? '+' : ''}$${fmtNum(dailyLoss, 0, '0')}`,
              valueClass: dailyLoss >= 0 ? 'text-long' : 'text-short',
            },
            { label: 'Win Rate', value: '57.3%' },
          ].map(({ label, value, valueClass }) => (
            <div
              key={label}
              className="rounded-md p-2 relative overflow-hidden"
              style={{ background: 'hsl(var(--gold) / 0.06)', border: '1px solid hsl(var(--gold) / 0.15)' }}
            >
              <div className="absolute left-0 top-0 bottom-0 w-0.5 rounded-r" style={{ background: 'hsl(var(--gold))' }} />
              <div className="text-[10px] uppercase tracking-wider" style={{ color: 'hsl(var(--muted-foreground))' }}>{label}</div>
              <div className={cn('text-sm font-mono font-bold leading-tight mt-0.5', valueClass || 'text-primary')}>
                {value}
              </div>
            </div>
          ))}
        </div>
      </div>

      <Separator className="bg-sidebar-border/40" />

      {/* ── Navigation ── */}
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
                    ? 'nav-active-bar border'
                    : 'text-sidebar-foreground/60 hover:text-sidebar-foreground border border-transparent'
                )}
                style={isActive ? {
                  background: 'hsl(var(--gold) / 0.10)',
                  borderColor: 'hsl(var(--gold) / 0.25)',
                  color: 'hsl(var(--gold-light))',
                } : {
                  background: 'transparent',
                }}
              >
                <Icon
                  className={cn('w-4 h-4 shrink-0 transition-colors')}
                  style={{ color: isActive ? 'hsl(var(--gold))' : undefined }}
                />
                <span className="flex-1 text-left">{item.label}</span>

                {/* Special badges */}
                {item.badge && (
                  <Badge
                    variant="outline"
                    className="text-[9px] h-4 px-1"
                    style={item.badge === 'LIVE'
                      ? { borderColor: 'hsl(var(--gold) / 0.5)', color: 'hsl(var(--gold-light))', background: 'hsl(var(--gold) / 0.10)' }
                      : { borderColor: 'hsl(var(--info) / 0.5)', color: 'hsl(var(--info))', background: 'hsl(var(--info) / 0.08)' }
                    }
                  >
                    {item.badge}
                  </Badge>
                )}

                {/* Live count badges */}
                {item.id === 'signals' && activeSignals > 0 && (
                  <span
                    className="w-4 h-4 rounded-full text-[9px] flex items-center justify-center font-mono"
                    style={{ background: 'hsl(var(--gold) / 0.20)', color: 'hsl(var(--gold-light))' }}
                  >
                    {activeSignals}
                  </span>
                )}
                {item.id === 'trades' && openPositions > 0 && (
                  <span
                    className="w-4 h-4 rounded-full text-[9px] flex items-center justify-center font-mono"
                    style={{ background: 'hsl(var(--gold) / 0.20)', color: 'hsl(var(--gold-light))' }}
                  >
                    {openPositions}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </ScrollArea>

      <Separator className="bg-sidebar-border/40" />

      {/* ── Footer Controls ── */}
      <div className="p-3 space-y-3">
        {/* Risk block */}
        <div className="rounded-md p-2.5 space-y-2" style={{ background: 'hsl(var(--gold) / 0.05)', border: '1px solid hsl(var(--gold) / 0.12)' }}>
          <div className="flex items-center justify-between">
            <span className="text-[10px]" style={{ color: 'hsl(var(--muted-foreground))' }}>Circuit Breaker</span>
            <div
              className={`w-1.5 h-1.5 rounded-full ${guardian?.circuitBreaker ? 'bg-short animate-pulse' : 'bg-long'}`}
              style={guardian?.circuitBreaker ? {} : { boxShadow: '0 0 5px hsl(var(--long) / 0.6)' }}
            />
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[10px]" style={{ color: 'hsl(var(--muted-foreground))' }}>Daily Risk</span>
            <span className="text-[10px] font-mono" style={{ color: 'hsl(var(--foreground) / 0.80)' }}>
              ${fmtNum(dailyLoss, 0, '0')}/${fmtNum(dailyLossLimit, 0, '0')}
            </span>
          </div>
          {/* Gold gradient progress bar */}
          <div className="w-full rounded-full h-1 overflow-hidden" style={{ background: 'hsl(var(--border) / 0.5)' }}>
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${riskPct}%`,
                background: 'linear-gradient(90deg, hsl(var(--gold-dark)), hsl(var(--gold-light)))',
              }}
            />
          </div>
        </div>

        {/* Toggles */}
        <div className="flex items-center justify-between px-1">
          <span className="text-[10px]" style={{ color: 'hsl(var(--muted-foreground))' }}>Auto-Trade</span>
          <Switch checked={serverAutoTradeEnabled} onCheckedChange={handleAutoTradeToggle} disabled={togglingAutoTrade} />
        </div>
        <div className="flex items-center justify-between px-1">
          <span className="text-[10px]" style={{ color: 'hsl(var(--muted-foreground))' }}>Test Mode</span>
          <Switch checked={isTestMode} onCheckedChange={toggleTestMode} />
        </div>
      </div>
    </aside>
  );
}
