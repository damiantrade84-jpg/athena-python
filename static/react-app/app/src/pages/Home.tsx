import { useStore } from '@/hooks/useStore';
import MainLayout from '@/components/layout/MainLayout';
import DashboardPanel from '@/components/panels/DashboardPanel';
import SignalsPanel from '@/components/panels/SignalsPanel';
import PairBrowserPanel from '@/components/panels/PairBrowserPanel';
import EngineBHotBenchPanel from '@/components/panels/EngineBHotBenchPanel';
import LiveCockpitPanel from '@/components/panels/LiveCockpitPanel';
import ScanConfigPanel from '@/components/panels/ScanConfigPanel';
import TradesPanel from '@/components/panels/TradesPanel';
import EngineCPanel from '@/components/panels/EngineCPanel';
import ScalpLabPanel from '@/components/panels/ScalpLabPanel';
import ScalpWorkbenchPanel from '@/components/panels/ScalpWorkbenchPanel';
import TVChartPanel from '@/components/panels/TVChartPanel';
import BacktestPanel from '@/components/panels/BacktestPanel';
import BacktestV3Panel from '@/components/panels/BacktestV3Panel';
import ExperimentLabPanel from '@/components/panels/ExperimentLabPanel';
import ScreenerPanel from '@/components/panels/ScreenerPanel';
import LotteryLabPanel from '@/components/panels/LotteryLabPanel';
import ResearchLabPanel from '@/components/panels/ResearchLabPanel';
import PerformancePanel from '@/components/panels/PerformancePanel';
import MarketsPanel from '@/components/panels/MarketsPanel';
import GuardianPanel from '@/components/panels/GuardianPanel';
import AiPerformancePanel from '@/components/panels/AiPerformancePanel';
import SuggestedTradesPanel from '@/components/panels/SuggestedTradesPanel';
import ExitStrategyPanel from '@/components/panels/ExitStrategyPanel';
import CascadeScanPanel from '@/components/panels/CascadeScanPanel';
import BulkAiReviewPanel from '@/components/panels/BulkAiReviewPanel';
import ASEPanel from '@/components/panels/ASEPanel';
import TSMOMPanel from '@/components/panels/TSMOMPanel';
import ForexFactorPanel from '@/components/panels/ForexFactorPanel';
import EdgeLabSuggestionsPanel from '@/components/panels/EdgeLabSuggestionsPanel';
import GhostTradePanel from '@/components/panels/GhostTradePanel';
import OpusEnginePanel from '@/components/panels/OpusEnginePanel';
import SolEnginePanel from '@/components/panels/SolEnginePanel';
import KimiEnginePanel from '@/components/panels/KimiEnginePanel';
import GrokEnginePanel from '@/components/panels/GrokEnginePanel';
import OxAlphaPanel from '@/components/panels/OxAlphaPanel';
import OxBookPanel from '@/components/panels/OxBookPanel';

const panels: Record<string, React.ComponentType> = {
  dashboard: DashboardPanel,
  signals: SignalsPanel,
  pairBrowser: PairBrowserPanel,
  engineBHotBench: EngineBHotBenchPanel,
  liveCockpit: LiveCockpitPanel,
  scanConfig: ScanConfigPanel,
  trades: TradesPanel,
  engineC: EngineCPanel,
  scalpLab: ScalpLabPanel,
  scalpWorkbench: ScalpWorkbenchPanel,
  tvChart: TVChartPanel,
  backtest: BacktestPanel,
  backtestV3: BacktestV3Panel,
  experimentLab: ExperimentLabPanel,
  screener: ScreenerPanel,
  lotteryLab: LotteryLabPanel,
  researchLab: ResearchLabPanel,
  performance: PerformancePanel,
  markets: MarketsPanel,
  guardian: GuardianPanel,
  aiPerformance: AiPerformancePanel,
  exitStrategy: ExitStrategyPanel,
  cascadeScan: CascadeScanPanel,
  bulkAiReview: BulkAiReviewPanel,
  suggestedTrades: SuggestedTradesPanel,
  ase: ASEPanel,
  tsmom: TSMOMPanel,
  forexFactor: ForexFactorPanel,
  edgeLab: EdgeLabSuggestionsPanel,
  ghostTrade: GhostTradePanel,
  opusEngine: OpusEnginePanel,
  solEngine: SolEnginePanel,
  kimiEngine: KimiEnginePanel,
  grokEngine: GrokEnginePanel,
  oxAlpha: OxAlphaPanel,
  oxBook: OxBookPanel,
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
