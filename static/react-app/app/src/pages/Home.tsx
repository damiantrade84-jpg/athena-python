import { useStore } from '@/hooks/useStore';
import MainLayout from '@/components/layout/MainLayout';
import DashboardPanel from '@/components/panels/DashboardPanel';
import SignalsPanel from '@/components/panels/SignalsPanel';
import PairBrowserPanel from '@/components/panels/PairBrowserPanel';
import LiveCockpitPanel from '@/components/panels/LiveCockpitPanel';
import ScanConfigPanel from '@/components/panels/ScanConfigPanel';
import TradesPanel from '@/components/panels/TradesPanel';
import EngineCPanel from '@/components/panels/EngineCPanel';
import ScalpLabPanel from '@/components/panels/ScalpLabPanel';
import ScalpWorkbenchPanel from '@/components/panels/ScalpWorkbenchPanel';
import TVChartPanel from '@/components/panels/TVChartPanel';
import BacktestPanel from '@/components/panels/BacktestPanel';
import ScreenerPanel from '@/components/panels/ScreenerPanel';
import LotteryLabPanel from '@/components/panels/LotteryLabPanel';
import ResearchLabPanel from '@/components/panels/ResearchLabPanel';
import PerformancePanel from '@/components/panels/PerformancePanel';
import MarketsPanel from '@/components/panels/MarketsPanel';
import GuardianPanel from '@/components/panels/GuardianPanel';
import AiPerformancePanel from '@/components/panels/AiPerformancePanel';
import SuggestedTradesPanel from '@/components/panels/SuggestedTradesPanel';

const panels: Record<string, React.ComponentType> = {
  dashboard: DashboardPanel,
  signals: SignalsPanel,
  pairBrowser: PairBrowserPanel,
  liveCockpit: LiveCockpitPanel,
  scanConfig: ScanConfigPanel,
  trades: TradesPanel,
  engineC: EngineCPanel,
  scalpLab: ScalpLabPanel,
  scalpWorkbench: ScalpWorkbenchPanel,
  tvChart: TVChartPanel,
  backtest: BacktestPanel,
  screener: ScreenerPanel,
  lotteryLab: LotteryLabPanel,
  researchLab: ResearchLabPanel,
  performance: PerformancePanel,
  markets: MarketsPanel,
  guardian: GuardianPanel,
  aiPerformance: AiPerformancePanel,
  suggestedTrades: SuggestedTradesPanel,
};

export default function Home() {
  const { activePanel } = useStore();

  const PanelComponent = panels[activePanel] || DashboardPanel;

  return (
    <MainLayout>
      <PanelComponent />
    </MainLayout>
  );
}
