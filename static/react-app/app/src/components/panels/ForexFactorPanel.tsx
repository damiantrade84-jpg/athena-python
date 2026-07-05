import { useEffect, useMemo, useState } from 'react';
import { Landmark } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { getForexTradingStatus, type ForexTradingStatusData } from '@/lib/forexFactorApi';
import { FX_ACCENT } from './forexFactor/shared';
import OverviewTab from './forexFactor/OverviewTab';
import FactorSignalsTab from './forexFactor/FactorSignalsTab';
import SwapAuditTab from './forexFactor/SwapAuditTab';
import TotalReturnTab from './forexFactor/TotalReturnTab';
import CotPositioningTab from './forexFactor/CotPositioningTab';
import ReerValueTab from './forexFactor/ReerValueTab';
import VolCostGatesTab from './forexFactor/VolCostGatesTab';
import BacktestingTab from './forexFactor/BacktestingTab';
import ScannerTab from './forexFactor/ScannerTab';
import ExecutionTab from './forexFactor/ExecutionTab';
import ValidationTab from './forexFactor/ValidationTab';
import DiagnosticsTab from './forexFactor/DiagnosticsTab';

interface TabDef {
  value: string;
  label: string;
}

interface GroupDef {
  id: string;
  label: string;
  tabs: TabDef[];
}

const GROUPS: GroupDef[] = [
  {
    id: 'trade',
    label: 'Trade',
    tabs: [
      { value: 'scanner', label: 'Scanner' },
      { value: 'execution', label: 'Execution' },
    ],
  },
  {
    id: 'factors',
    label: 'Factors',
    tabs: [
      { value: 'signals', label: 'Factor Signals' },
      { value: 'cot', label: 'COT' },
      { value: 'reer', label: 'Value / REER' },
    ],
  },
  {
    id: 'data',
    label: 'Data & Gates',
    tabs: [
      { value: 'swap', label: 'Swap Audit' },
      { value: 'totalReturn', label: 'Total Return' },
      { value: 'gates', label: 'Vol & Cost' },
    ],
  },
  {
    id: 'backtest',
    label: 'Backtest',
    tabs: [
      { value: 'backtest', label: 'Backtesting' },
      { value: 'validation', label: 'Validation' },
    ],
  },
  {
    id: 'system',
    label: 'System',
    tabs: [
      { value: 'overview', label: 'Overview' },
      { value: 'diagnostics', label: 'Diagnostics' },
    ],
  },
];

function groupForTab(tab: string): string {
  return GROUPS.find((g) => g.tabs.some((t) => t.value === tab))?.id || GROUPS[0].id;
}

export default function ForexFactorPanel() {
  const [activeTab, setActiveTab] = useState('scanner');
  const [status, setStatus] = useState<ForexTradingStatusData | null>(null);
  const activeGroup = useMemo(() => groupForTab(activeTab), [activeTab]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const resp = await getForexTradingStatus();
        if (!cancelled) setStatus(resp.data || null);
      } catch {
        if (!cancelled) setStatus(null);
      }
    };
    void load();
    const id = window.setInterval(load, 60000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const executionOn = Boolean(status?.can_demo_execute);
  const killSwitch = Boolean(status?.kill_switch);

  return (
    <div className="p-4 space-y-3 h-full min-h-0 flex flex-col">
      <div className="flex flex-wrap items-center gap-2 shrink-0">
        <Landmark className="h-5 w-5" style={{ color: FX_ACCENT }} />
        <h2 className="text-lg font-semibold">Forex Trading &amp; Backtesting</h2>
        <Badge className="badge-long">FACTOR LIVE</Badge>
        <Badge variant="outline" className="text-[10px]">SCAN</Badge>
        <Badge variant="outline" className="text-[10px]">DEMO EXECUTION</Badge>
        <Badge
          className={executionOn ? 'badge-long text-[10px]' : 'badge-neutral text-[10px]'}
          title={(status?.block_reasons || []).join(', ') || undefined}
        >
          {executionOn ? 'EXECUTION READY' : 'EXECUTION OFF'}
        </Badge>
        {killSwitch && <Badge className="badge-short text-[10px]">KILL SWITCH</Badge>}
        <span className="ml-auto text-[10px] text-muted-foreground font-mono">
          mode {status?.executor_mode || 'paper'} · open {status?.open_positions?.length ?? 0}
        </span>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab} defaultValue="scanner" className="flex-1 min-h-0 flex flex-col">
        <div className="shrink-0 space-y-1.5">
          <div className="flex flex-wrap gap-1 rounded-md border border-border/60 bg-muted/20 p-1 w-fit">
            {GROUPS.map((group) => {
              const active = group.id === activeGroup;
              return (
                <button
                  key={group.id}
                  type="button"
                  onClick={() => setActiveTab(group.tabs[0].value)}
                  className={`rounded px-3 py-1 text-xs transition-colors ${
                    active
                      ? 'bg-background text-foreground font-medium shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {group.label}
                </button>
              );
            })}
          </div>
          <TabsList className="flex flex-wrap h-auto gap-0.5">
            <TabsTrigger value="scanner" className={`text-xs ${activeGroup === 'trade' ? '' : 'hidden'}`}>Scanner</TabsTrigger>
            <TabsTrigger value="execution" className={`text-xs ${activeGroup === 'trade' ? '' : 'hidden'}`}>Execution</TabsTrigger>
            <TabsTrigger value="signals" className={`text-xs ${activeGroup === 'factors' ? '' : 'hidden'}`}>Factor Signals</TabsTrigger>
            <TabsTrigger value="cot" className={`text-xs ${activeGroup === 'factors' ? '' : 'hidden'}`}>COT</TabsTrigger>
            <TabsTrigger value="reer" className={`text-xs ${activeGroup === 'factors' ? '' : 'hidden'}`}>Value / REER</TabsTrigger>
            <TabsTrigger value="swap" className={`text-xs ${activeGroup === 'data' ? '' : 'hidden'}`}>Swap Audit</TabsTrigger>
            <TabsTrigger value="totalReturn" className={`text-xs ${activeGroup === 'data' ? '' : 'hidden'}`}>Total Return</TabsTrigger>
            <TabsTrigger value="gates" className={`text-xs ${activeGroup === 'data' ? '' : 'hidden'}`}>Vol &amp; Cost</TabsTrigger>
            <TabsTrigger value="backtest" className={`text-xs ${activeGroup === 'backtest' ? '' : 'hidden'}`}>Backtesting</TabsTrigger>
            <TabsTrigger value="validation" className={`text-xs ${activeGroup === 'backtest' ? '' : 'hidden'}`}>Validation</TabsTrigger>
            <TabsTrigger value="overview" className={`text-xs ${activeGroup === 'system' ? '' : 'hidden'}`}>Overview</TabsTrigger>
            <TabsTrigger value="diagnostics" className={`text-xs ${activeGroup === 'system' ? '' : 'hidden'}`}>Diagnostics</TabsTrigger>
          </TabsList>
        </div>

        <div className="flex-1 min-h-0 overflow-auto mt-2">
          <TabsContent value="scanner" className="mt-0"><ScannerTab /></TabsContent>
          <TabsContent value="overview" className="mt-0"><OverviewTab /></TabsContent>
          <TabsContent value="signals" className="mt-0"><FactorSignalsTab /></TabsContent>
          <TabsContent value="swap" className="mt-0"><SwapAuditTab /></TabsContent>
          <TabsContent value="totalReturn" className="mt-0"><TotalReturnTab /></TabsContent>
          <TabsContent value="cot" className="mt-0"><CotPositioningTab /></TabsContent>
          <TabsContent value="reer" className="mt-0"><ReerValueTab /></TabsContent>
          <TabsContent value="gates" className="mt-0"><VolCostGatesTab /></TabsContent>
          <TabsContent value="backtest" className="mt-0"><BacktestingTab /></TabsContent>
          <TabsContent value="execution" className="mt-0"><ExecutionTab /></TabsContent>
          <TabsContent value="validation" className="mt-0"><ValidationTab /></TabsContent>
          <TabsContent value="diagnostics" className="mt-0"><DiagnosticsTab /></TabsContent>
        </div>
      </Tabs>
    </div>
  );
}
