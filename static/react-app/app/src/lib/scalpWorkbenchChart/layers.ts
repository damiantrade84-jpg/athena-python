import { LineStyle } from 'lightweight-charts';
import type { Time } from 'lightweight-charts';
import type { OrderFlowPrimitiveMarker } from './orderFlowMarkerPrimitive';

export interface ScalpPriceLevel {
  label: string;
  price: number;
  color: string;
  style?: LineStyle;
  layer?: string;
}

export interface ScalpOverlayToggles {
  auctionLevels: boolean;
  liquidity: boolean;
  engineB: boolean;
  orderFlow: boolean;
  candidateLevels: boolean;
  aiVerdict: boolean;
  warnings: boolean;
}

export const DEFAULT_SCALP_OVERLAY_TOGGLES: ScalpOverlayToggles = {
  auctionLevels: true,
  liquidity: true,
  engineB: true,
  orderFlow: true,
  candidateLevels: true,
  aiVerdict: true,
  warnings: true,
};

export interface LiquidityContextLike {
  priorDayHigh?: number | null;
  priorDayLow?: number | null;
  sessionHigh?: number | null;
  sessionLow?: number | null;
  premarketHigh?: number | null;
  premarketLow?: number | null;
  asiaHigh?: number | null;
  asiaLow?: number | null;
  londonHigh?: number | null;
  londonLow?: number | null;
  sweepLevel?: number | null;
  reclaimLevel?: number | null;
}

export interface MarketLocationLike {
  poc?: number | null;
  vah?: number | null;
  val?: number | null;
  priorPoc?: number | null;
  priorVah?: number | null;
  priorVal?: number | null;
  lvnLevels?: number[];
  hvnLevels?: number[];
  lvnLevelsNear?: number[];
  hvnLevelsNear?: number[];
}

export interface ScalpSetupLike {
  entry?: number | null;
  stopLoss?: number | null;
  tp1?: number | null;
  tp2?: number | null;
  direction?: string | null;
}

type EngineBZoneLike = {
  lower?: number;
  upper?: number;
  low?: number;
  high?: number;
};

type EngineBBlockLike = {
  top?: number;
  bottom?: number;
  low?: number;
  high?: number;
  mitigated?: boolean;
};

export interface EngineBOverlayLike {
  nearest_support_zone?: EngineBZoneLike | null;
  nearest_resistance_zone?: EngineBZoneLike | null;
  order_blocks?: Array<EngineBBlockLike>;
  active_fvgs?: Array<EngineBBlockLike>;
  bos_data?: { last_broken_high?: number; last_broken_low?: number; level?: number };
  choch_data?: { choch_level?: number; level?: number };
  current_swing_sequence?: string;
}

export interface OrderFlowPayloadLike {
  source?: string;
  largeTradeEvents?: Array<{ ts?: string; price?: number; side?: string }>;
  absorptionEvents?: Array<{ ts?: string; price?: number; sideAbsorbed?: string }>;
  initiativeEvents?: Array<{ ts?: string; price?: number; side?: string }>;
  exhaustionEvents?: Array<{ ts?: string; price?: number }>;
  cvd?: Array<{ ts?: string; value?: number }>;
  warnings?: string[];
}

export interface BuildLayersArgs {
  toggles: ScalpOverlayToggles;
  marketLocation: MarketLocationLike;
  liquidity: LiquidityContextLike;
  setup: ScalpSetupLike;
  structuralTp?: number | null;
  engineB?: EngineBOverlayLike | null;
  engineBSimplified?: boolean;
  aiVerdict?: {
    decision?: string | null;
    waitZoneLow?: number | null;
    waitZoneHigh?: number | null;
    acceptanceLevel?: number | null;
  } | null;
  anchorPrice?: number | null;
}

function pushLevel(
  levels: ScalpPriceLevel[],
  label: string,
  price: number | null | undefined,
  color: string,
  style?: LineStyle,
  layer?: string,
) {
  if (price == null || !Number.isFinite(price)) return;
  levels.push({ label, price, color, style, layer });
}

function zoneLow(zone: EngineBZoneLike | EngineBBlockLike): number | null | undefined {
  return zone.lower ?? zone.bottom ?? zone.low;
}

function zoneHigh(zone: EngineBZoneLike | EngineBBlockLike): number | null | undefined {
  return zone.upper ?? zone.top ?? zone.high;
}

function pushZoneLevels(
  levels: ScalpPriceLevel[],
  prefix: string,
  zone: EngineBZoneLike | EngineBBlockLike | null | undefined,
  color: string,
  layer: string,
) {
  if (!zone) return;
  pushLevel(levels, `${prefix} low`, zoneLow(zone), color, LineStyle.Dotted, layer);
  pushLevel(levels, `${prefix} high`, zoneHigh(zone), color, LineStyle.Dotted, layer);
}

export function buildScalpChartLevels(args: BuildLayersArgs): ScalpPriceLevel[] {
  const levels: ScalpPriceLevel[] = [];
  const { toggles, marketLocation: loc, liquidity, setup } = args;

  if (toggles.auctionLevels) {
    pushLevel(levels, 'POC', loc.poc, 'hsl(200, 95%, 55%)', undefined, 'auction');
    pushLevel(levels, 'VAH', loc.vah, 'hsl(265, 80%, 68%)', LineStyle.Dashed, 'auction');
    pushLevel(levels, 'VAL', loc.val, 'hsl(265, 80%, 68%)', LineStyle.Dashed, 'auction');
    pushLevel(levels, 'pPOC', loc.priorPoc, 'hsl(200, 70%, 45%)', LineStyle.Dotted, 'auction');
    pushLevel(levels, 'pVAH', loc.priorVah, 'hsl(265, 60%, 55%)', LineStyle.Dotted, 'auction');
    pushLevel(levels, 'pVAL', loc.priorVal, 'hsl(265, 60%, 55%)', LineStyle.Dotted, 'auction');
    const lvns = loc.lvnLevelsNear?.length ? loc.lvnLevelsNear : loc.lvnLevels || [];
    const hvns = loc.hvnLevelsNear?.length ? loc.hvnLevelsNear : loc.hvnLevels || [];
    lvns.forEach((price, index) => {
      pushLevel(levels, `LVN`, price, 'hsl(30, 92%, 58%)', LineStyle.Dotted, 'auction');
      if (index > 0) levels[levels.length - 1].label = `LVN ${index + 1}`;
    });
    hvns.forEach((price, index) => {
      pushLevel(levels, `HVN`, price, 'hsl(190, 82%, 52%)', LineStyle.Dotted, 'auction');
      if (index > 0) levels[levels.length - 1].label = `HVN ${index + 1}`;
    });
  }

  if (toggles.liquidity) {
    pushLevel(levels, 'PDH', liquidity.priorDayHigh, 'hsl(340, 75%, 62%)', LineStyle.Dashed, 'liquidity');
    pushLevel(levels, 'PDL', liquidity.priorDayLow, 'hsl(340, 75%, 62%)', LineStyle.Dashed, 'liquidity');
    pushLevel(levels, 'Session High', liquidity.sessionHigh, 'hsl(45, 90%, 55%)', LineStyle.Dashed, 'liquidity');
    pushLevel(levels, 'Session Low', liquidity.sessionLow, 'hsl(45, 90%, 55%)', LineStyle.Dashed, 'liquidity');
    pushLevel(levels, 'PMH', liquidity.premarketHigh, 'hsl(280, 70%, 65%)', LineStyle.Dotted, 'liquidity');
    pushLevel(levels, 'PML', liquidity.premarketLow, 'hsl(280, 70%, 65%)', LineStyle.Dotted, 'liquidity');
    pushLevel(levels, 'Asia H', liquidity.asiaHigh, 'hsl(170, 70%, 50%)', LineStyle.Dotted, 'liquidity');
    pushLevel(levels, 'Asia L', liquidity.asiaLow, 'hsl(170, 70%, 50%)', LineStyle.Dotted, 'liquidity');
    pushLevel(levels, 'London H', liquidity.londonHigh, 'hsl(210, 70%, 55%)', LineStyle.Dotted, 'liquidity');
    pushLevel(levels, 'London L', liquidity.londonLow, 'hsl(210, 70%, 55%)', LineStyle.Dotted, 'liquidity');
    pushLevel(levels, 'Sweep Level', liquidity.sweepLevel, 'hsl(0, 85%, 58%)', undefined, 'liquidity');
    pushLevel(levels, 'Reclaim Level', liquidity.reclaimLevel, 'hsl(140, 75%, 48%)', undefined, 'liquidity');
  }

  if (toggles.engineB && args.engineB) {
    const eb = args.engineB;
    pushZoneLevels(levels, 'Support', eb.nearest_support_zone, 'rgba(16,185,129,0.55)', 'engineB');
    pushZoneLevels(levels, 'Resistance', eb.nearest_resistance_zone, 'rgba(244,63,94,0.55)', 'engineB');
    if (!args.engineBSimplified) {
      for (const ob of (eb.order_blocks || []).filter((z) => !z?.mitigated).slice(0, 2)) {
        pushZoneLevels(levels, 'OB', ob, 'rgba(59,130,246,0.45)', 'engineB');
      }
      for (const fvg of (eb.active_fvgs || []).filter((z) => !z?.mitigated).slice(0, 2)) {
        pushZoneLevels(levels, 'FVG', fvg, 'rgba(168,85,247,0.45)', 'engineB');
      }
      pushLevel(
        levels,
        'BOS',
        eb.bos_data?.last_broken_high ?? eb.bos_data?.last_broken_low ?? eb.bos_data?.level,
        'rgba(250,204,21,0.7)',
        LineStyle.Dashed,
        'engineB',
      );
      pushLevel(
        levels,
        'CHOCH',
        eb.choch_data?.choch_level ?? eb.choch_data?.level,
        'rgba(251,146,60,0.7)',
        LineStyle.Dashed,
        'engineB',
      );
    }
  }

  if (toggles.candidateLevels) {
    pushLevel(levels, 'Entry', setup.entry, 'hsl(45, 95%, 58%)', undefined, 'candidate');
    pushLevel(levels, 'SL', setup.stopLoss, 'hsl(343, 96%, 60%)', undefined, 'candidate');
    pushLevel(levels, 'TP1', setup.tp1, 'hsl(160, 84%, 39%)', undefined, 'candidate');
    pushLevel(levels, 'TP2', setup.tp2, 'hsl(160, 84%, 48%)', LineStyle.Dotted, 'candidate');
    pushLevel(levels, 'Structural Target', args.structuralTp, 'hsl(160, 70%, 42%)', LineStyle.Dotted, 'candidate');
    if (setup.entry != null && setup.tp1 != null) {
      pushLevel(levels, 'BE', setup.entry, 'hsl(200, 80%, 55%)', LineStyle.Dotted, 'candidate');
    }
    pushLevel(levels, 'Invalidation', setup.stopLoss, 'hsl(343, 80%, 45%)', LineStyle.Dashed, 'candidate');
  }

  if (toggles.aiVerdict && args.aiVerdict) {
    const v = args.aiVerdict;
    if (v.decision === 'WAIT_FOR_PULLBACK') {
      pushLevel(levels, 'WAIT ZONE low', v.waitZoneLow, 'hsl(45, 90%, 50%)', LineStyle.Dashed, 'aiVerdict');
      pushLevel(levels, 'WAIT ZONE high', v.waitZoneHigh, 'hsl(45, 90%, 50%)', LineStyle.Dashed, 'aiVerdict');
    }
    if (v.decision === 'WAIT_FOR_ACCEPTANCE') {
      pushLevel(levels, 'ACCEPTANCE NEEDED', v.acceptanceLevel, 'hsl(200, 90%, 55%)', undefined, 'aiVerdict');
    }
  }

  return levels;
}

function parseMarkerTime(ts: string | undefined): Time | null {
  if (!ts) return null;
  const ms = Date.parse(ts);
  if (!Number.isFinite(ms)) return null;
  return Math.floor(ms / 1000) as Time;
}

function isFinitePrice(price: number | undefined | null): price is number {
  return typeof price === 'number' && Number.isFinite(price);
}

/**
 * Build price-anchored order-flow markers for the OrderFlowMarkerPrimitive.
 * Each marker is placed at the event's actual (time, price) rather than merely
 * above/below the bar; events without a finite price are skipped because they
 * cannot be price-anchored.
 */
export function buildOrderFlowPrimitiveMarkers(
  payload: OrderFlowPayloadLike | null | undefined,
): OrderFlowPrimitiveMarker[] {
  if (!payload) return [];
  const markers: OrderFlowPrimitiveMarker[] = [];
  for (const event of payload.largeTradeEvents || []) {
    const time = parseMarkerTime(event.ts);
    if (time == null || !isFinitePrice(event.price)) continue;
    const isSell = event.side === 'SELL';
    markers.push({
      time,
      price: event.price,
      color: isSell ? 'hsl(343, 96%, 60%)' : 'hsl(160, 84%, 39%)',
      shape: 'circle',
      text: isSell ? 'SELL' : 'BUY',
      labelPlacement: isSell ? 'above' : 'below',
    });
  }
  for (const event of payload.absorptionEvents || []) {
    const time = parseMarkerTime(event.ts);
    if (time == null || !isFinitePrice(event.price)) continue;
    const side = String(event.sideAbsorbed || '').toUpperCase();
    const text = side === 'BUY' || side === 'SELL' ? `ABSORPTION ${side}` : 'ABSORPTION';
    markers.push({
      time,
      price: event.price,
      color: 'hsl(280, 80%, 60%)',
      shape: 'square',
      text,
    });
  }
  for (const event of payload.initiativeEvents || []) {
    const time = parseMarkerTime(event.ts);
    if (time == null || !isFinitePrice(event.price)) continue;
    const isSell = event.side === 'SELL';
    markers.push({
      time,
      price: event.price,
      color: 'hsl(45, 95%, 58%)',
      shape: isSell ? 'arrowDown' : 'arrowUp',
      text: isSell ? 'INITIATIVE SELL' : 'INITIATIVE BUY',
      labelPlacement: isSell ? 'above' : 'below',
    });
  }
  for (const event of payload.exhaustionEvents || []) {
    const time = parseMarkerTime(event.ts);
    if (time == null || !isFinitePrice(event.price)) continue;
    markers.push({
      time,
      price: event.price,
      color: 'hsl(200, 70%, 55%)',
      shape: 'circle',
      text: 'EXHAUSTION',
    });
  }
  return markers;
}
