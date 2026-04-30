import { useStore } from '@/hooks/useStore';
import MainLayout from '@/components/layout/MainLayout';
import DashboardPanel from '@/components/panels/DashboardPanel';
import SignalsPanel from '@/components/panels/SignalsPanel';
import PairBrowserPanel from '@/components/panels/PairBrowserPanel';
import ScanConfigPanel from '@/components/panels/ScanConfigPanel';
import TradesPanel from '@/components/panels/TradesPanel';
import EngineCPanel from '@/components/panels/EngineCPanel';
import ScalpLabPanel from '@/components/panels/ScalpLabPanel';
import TVChartPanel from '@/components/panels/TVChartPanel';
import BacktestPanel from '@/components/panels/BacktestPanel';
import ScreenerPanel from '@/components/panels/ScreenerPanel';
import LotteryLabPanel from '@/components/panels/LotteryLabPanel';
import ResearchLabPanel from '@/components/panels/ResearchLabPanel';
import PerformancePanel from '@/components/panels/PerformancePanel';
import MarketsPanel from '@/components/panels/MarketsPanel';
import GuardianPanel from '@/components/panels/GuardianPanel';

const panels: Record<string, React.ComponentType> = {
  dashboard: DashboardPanel,
  signals: SignalsPanel,
  pairBrowser: PairBrowserPanel,
  scanConfig: ScanConfigPanel,
  trades: TradesPanel,
  engineC: EngineCPanel,
  scalpLab: ScalpLabPanel,
  tvChart: TVChartPanel,
  backtest: BacktestPanel,
  screener: ScreenerPanel,
  lotteryLab: LotteryLabPanel,
  researchLab: ResearchLabPanel,
  performance: PerformancePanel,
  markets: MarketsPanel,
  guardian: GuardianPanel,
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
