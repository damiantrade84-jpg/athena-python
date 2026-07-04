import { Landmark } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { FX_ACCENT } from './forexFactor/shared';
import OverviewTab from './forexFactor/OverviewTab';
import FactorSignalsTab from './forexFactor/FactorSignalsTab';
import SwapAuditTab from './forexFactor/SwapAuditTab';
import TotalReturnTab from './forexFactor/TotalReturnTab';
import CotPositioningTab from './forexFactor/CotPositioningTab';
import ReerValueTab from './forexFactor/ReerValueTab';
import VolCostGatesTab from './forexFactor/VolCostGatesTab';
import BacktestingTab from './forexFactor/BacktestingTab';
import ExecutionTab from './forexFactor/ExecutionTab';
import ValidationTab from './forexFactor/ValidationTab';
import DiagnosticsTab from './forexFactor/DiagnosticsTab';

export default function ForexFactorPanel() {
  return (
    <div className="p-4 space-y-4 h-full min-h-0 flex flex-col">
      <div className="flex items-center gap-2 shrink-0">
        <Landmark className="h-5 w-5" style={{ color: FX_ACCENT }} />
        <h2 className="text-lg font-semibold">Forex Trading &amp; Backtesting</h2>
        <Badge className="badge-neutral">RESEARCH</Badge>
        <Badge variant="outline" className="text-[10px]">Demo execution</Badge>
      </div>

      <p className="text-[11px] text-muted-foreground shrink-0">
        G8 factor lane — carry, momentum, value, COT overlay, vol/cost gates, backtests,
        and optional demo MT5 execution (config-gated).
      </p>

      <Tabs defaultValue="overview" className="flex-1 min-h-0 flex flex-col">
        <TabsList className="flex flex-wrap h-auto gap-0.5 shrink-0">
          <TabsTrigger value="overview" className="text-xs">Overview</TabsTrigger>
          <TabsTrigger value="signals" className="text-xs">Factor Signals</TabsTrigger>
          <TabsTrigger value="swap" className="text-xs">Swap Audit</TabsTrigger>
          <TabsTrigger value="totalReturn" className="text-xs">Total Return</TabsTrigger>
          <TabsTrigger value="cot" className="text-xs">COT</TabsTrigger>
          <TabsTrigger value="reer" className="text-xs">Value / REER</TabsTrigger>
          <TabsTrigger value="gates" className="text-xs">Vol &amp; Cost</TabsTrigger>
          <TabsTrigger value="backtest" className="text-xs">Backtesting</TabsTrigger>
          <TabsTrigger value="execution" className="text-xs">Execution</TabsTrigger>
          <TabsTrigger value="validation" className="text-xs">Validation</TabsTrigger>
          <TabsTrigger value="diagnostics" className="text-xs">Diagnostics</TabsTrigger>
        </TabsList>

        <div className="flex-1 min-h-0 overflow-auto mt-2">
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
