import { useCallback, type ElementType } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { useSuggestedTradeRunnerStatus } from '@/hooks/useSuggestedTradeRunnerStatus';
import type { PanelId } from '@/types';
import type { PerformanceMetrics } from '@/types';
import { cn, fmtNum, toNum } from '@/lib/utils';
import { runnerBadgeClass, runnerBadgeLabel } from '@/lib/suggestedTradeRunnerDisplay';
import { Separator } from '@/components/ui/separator';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Switch } from '@/components/ui/switch';
import {
  LayoutDashboard, Zap, Search, Settings, TrendingUp,
  Layers, Activity, BarChart2, FlaskConical, Filter, Ticket,
  Microscope, PieChart, Globe, ShieldCheck, Radio, BrainCircuit, ListChecks, LogOut, Workflow, Cpu, Radar, Landmark, Sparkles,
  Ghost, Orbit, Waves, Timer, Gem, BookOpenCheck, Combine, Feather, Anchor,
} from 'lucide-react';

type NavItem = { id: PanelId; label: string; icon: ElementType; badge?: string; title?: string };

type NavSection = {
  title?: string;
  items: NavItem[];
};

const navSections: NavSection[] = [
  {
    items: [
      { id: 'dashboard',   label: 'Dashboard',   icon: LayoutDashboard },
      { id: 'signals',     label: 'Signals',      icon: Zap,        badge: 'LIVE' },
      { id: 'cascadeScan', label: 'Cascade Scan', icon: Workflow,   badge: 'NEW' },
      { id: 'bulkAiReview', label: 'Bulk AI Review', icon: Sparkles, badge: 'AI' },
      { id: 'pairBrowser', label: 'Pair Browser', icon: Search },
      { id: 'engineBHotBench', label: 'Engine B Bench', icon: Radar, badge: 'HOT' },
      { id: 'liveCockpit', label: 'Live Cockpit', icon: Radio,      badge: 'CYBER' },
      { id: 'scanConfig',  label: 'Scan Config',  icon: Settings },
      { id: 'trades',      label: 'Trades',       icon: TrendingUp },
      { id: 'suggestedTrades', label: 'Suggested Trades', icon: ListChecks },
      { id: 'engineC',     label: 'Engine C',     icon: Layers },
      { id: 'ghostTrade',  label: 'Ghost Trade',  icon: Ghost, badge: 'SHADOW' },
      {
        id: 'grokEngine',
        label: 'GROK Engine',
        title: 'GROK — Killzone Displacement Delivery: session raid, void, CISD, paper-first broker execution',
        icon: Timer,
        badge: 'GROK',
      },
      { id: 'solEngine',   label: 'SOL Engine',   icon: Orbit, badge: 'INTRADAY' },
      {
        id: 'opusEngine',
        label: 'OPUS Engine',
        title: 'OPUS — intraday liquidity/auction engine with a cost-aware expectancy gate',
        icon: Waves,
        badge: 'OPUS',
      },
      {
        id: 'kimiEngine',
        label: 'KIMI Engine',
        title: 'KIMI — intraday quant engine: KIMI Score composite, sweep/displacement structure, paper-first execution',
        icon: Sparkles,
        badge: 'KIMI',
      },
      {
        id: 'fableEngine',
        label: 'FABLE Engine',
        title: 'FABLE — Narrative Liquidity Engine: draw, raid, shift, return, chorus fused into a coherence score; demo-gated execution',
        icon: Feather,
        badge: 'FABLE',
      },
      {
        id: 'museEngine',
        label: 'MUSE Engine',
        title: 'MUSE — Meridian Undertow Synthesis: harmonic prism fusion on D1/H4/M15/M5 with tide timing; paper-first execution',
        icon: Anchor,
        badge: 'MUSE',
      },
      {
        id: 'oxAlpha',
        label: 'OX Alpha',
        title: 'OX Alpha — intraday kinetic-liquidity engine: six-axis scoring per score group, demo-gated execution',
        icon: Gem,
        badge: 'OX',
      },
      {
        id: 'oxBook',
        label: 'OX Book',
        title: 'OX Book — evidence-certified daily trend book (gold + nasdaq), demo-gated via TSMOM bridge',
        icon: BookOpenCheck,
        badge: 'DEMO',
      },
      {
        id: 'engineCompile',
        label: 'Scan Board',
        title: 'Compiles last scan results across engines — does not start a scan',
        icon: Combine,
      },
    ],
  },
  {
    title: 'Research',
    items: [
      { id: 'researchLab', label: 'Research Lab', icon: Microscope },
      {
        id: 'edgeLab',
        label: 'EdgeLab',
        title: 'EdgeLab Suggestions — research-only findings and patch reviews',
        icon: Sparkles,
        badge: 'RESEARCH',
      },
    ],
  },
  {
    items: [
      { id: 'ase',         label: 'ASE',          icon: Cpu,        badge: 'SHADOW' },
      { id: 'tsmom',       label: 'TSMOM',        icon: TrendingUp, badge: 'DEMO' },
      { id: 'forexFactor', label: 'Forex Trading', icon: Landmark, badge: 'FX' },
      { id: 'scalpLab',    label: 'Scalp Lab',    icon: Activity },
      { id: 'scalpWorkbench', label: 'Scalp Workbench', icon: BarChart2 },
      { id: 'tvChart',     label: 'TV Chart',     icon: BarChart2 },
      { id: 'backtest',    label: 'Backtesting v2', icon: FlaskConical, badge: 'V2' },
      { id: 'backtestV3',  label: 'Backtest V3',  icon: FlaskConical, badge: 'V3' },
      { id: 'experimentLab', label: 'Tuning Lab', icon: FlaskConical, badge: 'NEW' },
      { id: 'screener',    label: 'Screener',     icon: Filter },
      { id: 'lotteryLab',  label: 'Lottery Lab',  icon: Ticket },
      { id: 'performance', label: 'Performance',  icon: PieChart },
      { id: 'markets',     label: 'Markets',      icon: Globe },
      { id: 'guardian',    label: 'Guardian',     icon: ShieldCheck },
      { id: 'exitStrategy', label: 'Exit Strategy', icon: LogOut },
      { id: 'aiPerformance', label: 'AI Perf',    icon: BrainCircuit },
    ],
  },
];

export default function Sidebar() {
  const { activePanel, setActivePanel, signals, positions, guardian, showToast, isTestMode, toggleTestMode } = useStore();
  const { data: autoTrade, refresh: refreshAutoTrade } = useApiPoll<{ enabled: boolean }>('/api/auto-trade', 15000);
  const { data: perfMetrics } = useApiPoll<PerformanceMetrics>('/api/performance', 60000);
  const { post: postAutoTrade, loading: togglingAutoTrade } = useApiPost<{ enabled: boolean; error?: string }>();
  const { runner: suggestedTradeRunner, readyCount: suggestedReadyCount } = useSuggestedTradeRunnerStatus();

  const activeSignals  = signals.filter(s => s.status === 'active').length;
  const openPositions  = positions.filter(p => p.status === 'open').length;
  const dailyLoss      = toNum(guardian?.dailyLoss);
  const dailyLossLimit = toNum(guardian?.dailyLossLimit, 1);
  const riskPct        = Math.min(100, (Math.abs(dailyLoss) / Math.max(1, dailyLossLimit)) * 100);
  const serverAutoTradeEnabled = Boolean(autoTrade?.enabled);
  const winRate        = perfMetrics?.win_rate;
  const totalTrades    = perfMetrics?.total_trades ?? 0;

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
    <aside className="flex h-full min-h-0 w-[218px] shrink-0 flex-col overflow-hidden border-r border-border/70 bg-sidebar/80 backdrop-blur-sm">

      {/* ── At-a-glance counters ── */}
      <div className="shrink-0 border-b border-border/70 px-3 py-3">
        <div className="grid grid-cols-2 gap-2">
          {[
            { label: 'Signals', value: activeSignals },
            { label: 'Positions', value: openPositions },
            {
              label: 'Daily P&L',
              value: `${dailyLoss >= 0 ? '+' : ''}$${fmtNum(dailyLoss, 0, '0')}`,
              valueClass: dailyLoss >= 0 ? 'text-long' : 'text-short',
            },
            {
              label: 'Win Rate',
              value: winRate != null && totalTrades > 0 ? `${fmtNum(winRate, 1)}%` : '—',
            },
          ].map(({ label, value, valueClass }) => (
            <div key={label} className="surface-inset min-w-0 px-2.5 py-2">
              <div className="label truncate">{label}</div>
              <div className={cn('readout mt-1 truncate text-[14px] font-semibold', valueClass || 'text-foreground')}>
                {value}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Navigation ──
          Section badges (LIVE / NEW / AI / HOT …) were coloured chips on every
          third row, which made the rail compete with the content. They are now
          uniform muted micro-labels; only the live count badge gets the accent. */}
      <ScrollArea className="min-h-0 flex-1 py-2">
        <nav className="space-y-3 px-2">
          {navSections.map((section, sectionIndex) => (
            <div key={section.title ?? `section-${sectionIndex}`} className="space-y-0.5">
              {section.title && (
                <div className="label px-2.5 pb-1 pt-1">{section.title}</div>
              )}
              {section.items.map(item => {
                const isActive = activePanel === item.id;
                const Icon = item.icon;
                const count =
                  item.id === 'signals' ? activeSignals
                  : item.id === 'trades' ? openPositions
                  : 0;
                return (
                  <button
                    key={item.id}
                    type="button"
                    title={item.title || item.label}
                    onClick={() => setActivePanel(item.id)}
                    className={cn(
                      'group relative flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-xs transition-all duration-150',
                      isActive
                        ? 'nav-active-bar font-medium text-foreground'
                        : 'text-sidebar-foreground hover:bg-accent/60 hover:text-foreground',
                    )}
                    style={
                      isActive
                        ? {
                            background:
                              'linear-gradient(90deg, hsl(var(--primary) / 0.16), hsl(var(--primary) / 0.04) 70%, transparent)',
                          }
                        : undefined
                    }
                  >
                    <Icon
                      className={cn(
                        'h-3.5 w-3.5 shrink-0 transition-colors',
                        isActive ? 'text-primary' : 'text-muted-foreground group-hover:text-foreground/80',
                      )}
                    />
                    <span className="flex-1 truncate text-left">{item.label}</span>

                    {item.badge && (
                      <span className="shrink-0 text-[9px] uppercase tracking-wide text-muted-foreground/60">
                        {item.badge}
                      </span>
                    )}

                    {count > 0 && (
                      <span
                        className="readout shrink-0 rounded-md px-1.5 py-px text-[10px] text-primary"
                        style={{
                          background: 'hsl(var(--primary) / 0.14)',
                          boxShadow: 'inset 0 0 0 1px hsl(var(--primary) / 0.30)',
                        }}
                      >
                        {count}
                      </span>
                    )}

                    {item.id === 'suggestedTrades' && suggestedReadyCount > 0 && (
                      <span
                        className="readout shrink-0 rounded-md px-1.5 py-px text-[10px] text-long"
                        style={{
                          background: 'hsl(var(--long) / 0.16)',
                          boxShadow: 'inset 0 0 0 1px hsl(var(--long) / 0.40)',
                        }}
                        title={`${suggestedReadyCount} suggested trade${suggestedReadyCount === 1 ? '' : 's'} ready for review — open Suggested Trades`}
                      >
                        {suggestedReadyCount} ready
                      </span>
                    )}

                    {item.id === 'suggestedTrades' && suggestedTradeRunner && (
                      <span
                        className={cn(
                          'shrink-0 text-[9px] uppercase tracking-wide',
                          runnerBadgeClass(suggestedTradeRunner),
                        )}
                        title={`Suggested trade runner: ${runnerBadgeLabel(suggestedTradeRunner)}`}
                      >
                        {runnerBadgeLabel(suggestedTradeRunner)}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>
      </ScrollArea>

      <Separator className="shrink-0 bg-border/70" />

      {/* ── Footer: risk + toggles ── */}
      <div className="shrink-0 space-y-3 p-3">
        <div className="surface-inset space-y-2 px-2.5 py-2.5">
          <div className="flex items-center justify-between">
            <span className="label">Circuit Breaker</span>
            <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  guardian?.circuitBreaker ? 'animate-pulse bg-short' : 'bg-long',
                )}
                style={{
                  boxShadow: guardian?.circuitBreaker
                    ? '0 0 6px hsl(var(--short))'
                    : '0 0 6px hsl(var(--long) / 0.7)',
                }}
              />
              {guardian?.circuitBreaker ? 'tripped' : 'armed'}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="label">Daily Risk</span>
            <span className="readout text-[11px] text-muted-foreground">
              ${fmtNum(dailyLoss, 0, '0')} / ${fmtNum(dailyLossLimit, 0, '0')}
            </span>
          </div>
          <div className="meter">
            {/* Risk consumed is a warning quantity, not a good one — it uses the
                short/red token once it is meaningfully drawn down. */}
            <div
              className="meter-fill"
              style={{
                width: `${riskPct}%`,
                background: riskPct >= 66 ? 'hsl(var(--short))' : riskPct >= 33 ? 'hsl(var(--warning))' : 'hsl(var(--muted-foreground))',
              }}
            />
          </div>
        </div>

        <div className="space-y-2 border-t border-border/70 pt-2.5">
          <div className="flex items-center justify-between">
            <span className="label">Auto-Trade</span>
            <Switch
              checked={serverAutoTradeEnabled}
              onCheckedChange={handleAutoTradeToggle}
              disabled={togglingAutoTrade}
            />
          </div>
          <div className="flex items-center justify-between">
            <span className="label">Test Mode</span>
            <Switch checked={isTestMode} onCheckedChange={toggleTestMode} />
          </div>
          <p className="text-[10px] leading-snug text-muted-foreground/70">
            Autotrade volume: broker minimum
          </p>
        </div>
      </div>
    </aside>
  );
}
