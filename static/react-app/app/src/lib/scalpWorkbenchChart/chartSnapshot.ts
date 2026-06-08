import type { ScalpChartSnapshot } from '@/types/athena';
import type {
  EngineBOverlayLike,
  LiquidityContextLike,
  MarketLocationLike,
  OrderFlowPayloadLike,
  ScalpOverlayToggles,
  ScalpSetupLike,
} from './layers';

export interface BuildChartSnapshotArgs {
  symbol: string | null;
  timeframe: string;
  visibleRange: { from: string | null; to: string | null };
  latestCandleTs?: string | null;
  setup: ScalpSetupLike & {
    grade?: string | null;
    candidatePhase?: string | null;
    setupType?: string | null;
    setupModel?: string | null;
    rr1?: number | null;
    rr2?: number | null;
  };
  marketLocation: MarketLocationLike & { locationLabel?: string | null };
  liquidity: LiquidityContextLike;
  engineB?: EngineBOverlayLike | null;
  orderFlow?: OrderFlowPayloadLike | null;
  advisory?: {
    oldGateResult?: string | null;
    oldExecutable?: boolean;
    warnings?: string[];
    riskAdvisory?: Record<string, unknown>;
    sourceAdvisory?: Record<string, unknown>;
  };
  renderedLayers: Record<string, boolean>;
  aggressionClassification?: string | null;
  sourceQualityLabel?: string | null;
  marketState?: string | null;
}

export function buildScalpChartSnapshot(args: BuildChartSnapshotArgs): ScalpChartSnapshot {
  const loc = args.marketLocation;
  return {
    symbol: args.symbol,
    timeframe: args.timeframe,
    chartCapturedAt: new Date().toISOString(),
    visibleRange: args.visibleRange,
    latestCandleTs: args.latestCandleTs ?? null,
    selectedSignal: {
      direction: args.setup.direction ?? null,
      setupType: args.setup.setupType ?? null,
      entry: args.setup.entry ?? null,
      stopLoss: args.setup.stopLoss ?? null,
      tp1: args.setup.tp1 ?? null,
      tp2: args.setup.tp2 ?? null,
      rr1: args.setup.rr1 ?? null,
      rr2: args.setup.rr2 ?? null,
      grade: args.setup.grade ?? null,
      candidate_phase: args.setup.candidatePhase ?? null,
      setup_type: args.setup.setupModel ?? args.setup.setupType ?? null,
    },
    profile: {
      poc: loc.poc ?? null,
      vah: loc.vah ?? null,
      val: loc.val ?? null,
      priorPoc: loc.priorPoc ?? null,
      priorVah: loc.priorVah ?? null,
      priorVal: loc.priorVal ?? null,
      lvns: [...(loc.lvnLevelsNear || loc.lvnLevels || [])],
      hvns: [...(loc.hvnLevelsNear || loc.hvnLevels || [])],
    },
    liquidity: { ...args.liquidity },
    engineBContext: args.engineB
      ? {
          nearestSupport: args.engineB.nearest_support_zone ?? null,
          nearestResistance: args.engineB.nearest_resistance_zone ?? null,
          orderBlocks: args.engineB.order_blocks ?? [],
          fvgs: args.engineB.active_fvgs ?? [],
          breakerBlock: args.engineB.breaker_block ?? null,
          bosLevel:
            args.engineB.bos_data?.last_broken_high
            ?? args.engineB.bos_data?.last_broken_low
            ?? args.engineB.bos_data?.level
            ?? null,
          chochLevel: args.engineB.choch_data?.choch_level ?? args.engineB.choch_data?.level ?? null,
          swingSequence: args.engineB.current_swing_sequence ?? null,
        }
      : {
          nearestSupport: null,
          nearestResistance: null,
          orderBlocks: [],
          fvgs: [],
          breakerBlock: null,
          bosLevel: null,
          chochLevel: null,
          swingSequence: null,
        },
    orderFlow: {
      source: args.orderFlow?.source ?? 'UNAVAILABLE',
      largeTradeEvents: args.orderFlow?.largeTradeEvents ?? [],
      absorptionEvents: args.orderFlow?.absorptionEvents ?? [],
      initiativeEvents: args.orderFlow?.initiativeEvents ?? [],
      exhaustionEvents: args.orderFlow?.exhaustionEvents ?? [],
      cvdSeriesAvailable: Boolean(args.orderFlow?.cvd?.length),
      liquidationEventsAvailable: false,
    },
    advisory: {
      oldGateResult: args.advisory?.oldGateResult ?? null,
      oldExecutable: args.advisory?.oldExecutable ?? false,
      warnings: args.advisory?.warnings ?? [],
      riskAdvisory: args.advisory?.riskAdvisory ?? {},
      sourceAdvisory: args.advisory?.sourceAdvisory ?? {},
    },
    renderedLayers: args.renderedLayers,
    thesisBadge: {
      grade: args.setup.grade ?? null,
      candidatePhase: args.setup.candidatePhase ?? null,
      setupModel: args.setup.setupModel ?? null,
      marketState: args.marketState ?? null,
      location: loc.locationLabel ?? null,
      aggressionClassification: args.aggressionClassification ?? null,
      sourceQuality: args.sourceQualityLabel ?? null,
    },
  };
}

export function buildRenderedLayers(
  toggles: ScalpOverlayToggles,
  args: {
    candlesLoaded: boolean;
    profileAvailable: boolean;
    liquidityAvailable: boolean;
    engineBAvailable: boolean;
    orderFlowAvailable: boolean;
    candidateAvailable: boolean;
    advisoryAvailable: boolean;
  },
): Record<string, boolean> {
  return {
    candles: args.candlesLoaded,
    profile: toggles.auctionLevels && args.profileAvailable,
    liquidity: toggles.liquidity && args.liquidityAvailable,
    engineB: toggles.engineB && args.engineBAvailable,
    orderFlow: toggles.orderFlow && args.orderFlowAvailable,
    candidateLevels: toggles.candidateLevels && args.candidateAvailable,
    advisoryBadge: toggles.warnings && args.advisoryAvailable,
    aiVerdict: toggles.aiVerdict,
  };
}
