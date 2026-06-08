import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from 'lightweight-charts';
import type { EngineBOverlayLike } from './scalpWorkbenchChart/layers';

export type EngineBZoneCategory =
  | 'support'
  | 'resistance'
  | 'fvg_bull'
  | 'fvg_bear'
  | 'ob_bull'
  | 'ob_bear';

export interface EngineBZoneFill {
  top: number;
  bottom: number;
  fill: string;
  stroke: string;
  category: EngineBZoneCategory;
}

export const ENGINE_B_ZONE_STYLE: Record<EngineBZoneCategory, { fill: string; stroke: string }> = {
  support: { fill: 'rgba(16, 185, 129, 0.18)', stroke: 'rgba(16, 185, 129, 0.55)' },
  resistance: { fill: 'rgba(244, 63, 94, 0.18)', stroke: 'rgba(244, 63, 94, 0.55)' },
  fvg_bull: { fill: 'rgba(34, 197, 94, 0.16)', stroke: 'rgba(34, 197, 94, 0.50)' },
  fvg_bear: { fill: 'rgba(248, 113, 113, 0.16)', stroke: 'rgba(248, 113, 113, 0.50)' },
  ob_bull: { fill: 'rgba(20, 184, 166, 0.14)', stroke: 'rgba(20, 184, 166, 0.50)' },
  ob_bear: { fill: 'rgba(251, 113, 133, 0.14)', stroke: 'rgba(251, 113, 133, 0.50)' },
};

export function pushZoneFromPair(
  zones: EngineBZoneFill[],
  category: EngineBZoneCategory,
  upper: number | null,
  lower: number | null,
) {
  if (upper == null && lower == null) return;
  const top = upper ?? lower!;
  const bottom = lower ?? upper!;
  if (!Number.isFinite(top) || !Number.isFinite(bottom)) return;
  const style = ENGINE_B_ZONE_STYLE[category];
  zones.push({
    top: Math.max(top, bottom),
    bottom: Math.min(top, bottom),
    fill: style.fill,
    stroke: style.stroke,
    category,
  });
}

function firstNumber(...values: (number | string | null | undefined)[]): number | null {
  for (const v of values) {
    if (typeof v === 'number' && Number.isFinite(v)) return v;
    if (typeof v === 'string') {
      const parsed = parseFloat(v);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

export function buildEngineBZones(payload: EngineBOverlayLike | null, enabled: boolean): EngineBZoneFill[] {
  if (!enabled || !payload || (payload as any).overlay_source !== 'engine_b') return [];
  const zones: EngineBZoneFill[] = [];

  const support = payload.nearest_support_zone;
  if (support) {
    pushZoneFromPair(
      zones,
      'support',
      firstNumber(support.upper, support.level),
      firstNumber(support.lower, support.level),
    );
  }

  const resistance = payload.nearest_resistance_zone;
  if (resistance) {
    pushZoneFromPair(
      zones,
      'resistance',
      firstNumber(resistance.upper, resistance.level),
      firstNumber(resistance.lower, resistance.level),
    );
  }

  for (const zone of (payload.active_fvgs || []).filter((item: any) => !item?.mitigated).slice(0, 2)) {
    const top = firstNumber(zone.top);
    const bottom = firstNumber(zone.bottom);
    if (top == null || bottom == null) continue;
    const type = typeof zone.type === 'string' ? zone.type.toLowerCase() : '';
    pushZoneFromPair(zones, type.includes('bull') ? 'fvg_bull' : 'fvg_bear', top, bottom);
  }

  for (const zone of (payload.order_blocks || []).filter((item: any) => !item?.mitigated).slice(0, 2)) {
    const top = firstNumber(zone.top);
    const bottom = firstNumber(zone.bottom);
    if (top == null || bottom == null) continue;
    const type = typeof zone.type === 'string' ? zone.type.toLowerCase() : '';
    pushZoneFromPair(zones, type.includes('bull') ? 'ob_bull' : 'ob_bear', top, bottom);
  }

  return zones;
}

export class EngineBZonePaneView implements IPrimitivePaneView {
  private readonly primitive: EngineBZonePrimitive;

  constructor(primitive: EngineBZonePrimitive) {
    this.primitive = primitive;
  }

  zOrder(): 'bottom' { return 'bottom'; }

  renderer(): IPrimitivePaneRenderer | null {
    return new EngineBZoneRenderer(this.primitive);
  }
}

export class EngineBZoneRenderer implements IPrimitivePaneRenderer {
  private readonly primitive: EngineBZonePrimitive;

  constructor(primitive: EngineBZonePrimitive) {
    this.primitive = primitive;
  }

  draw(target: CanvasRenderingTarget2D): void {
    const zones = this.primitive.zones();
    const series = this.primitive.series();
    if (!series || zones.length === 0) return;

    target.useMediaCoordinateSpace(({ context, mediaSize }: { context: any, mediaSize: any }) => {
      for (const zone of zones) {
        const yTop = series.priceToCoordinate(zone.top);
        const yBot = series.priceToCoordinate(zone.bottom);
        if (yTop == null || yBot == null) continue;
        const y1 = Math.min(yTop, yBot);
        const y2 = Math.max(yTop, yBot);
        const h = Math.max(1, y2 - y1);
        context.save();
        context.fillStyle = zone.fill;
        context.fillRect(0, y1, mediaSize.width, h);
        context.strokeStyle = zone.stroke;
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(0, y1 + 0.5);
        context.lineTo(mediaSize.width, y1 + 0.5);
        context.moveTo(0, y2 - 0.5);
        context.lineTo(mediaSize.width, y2 - 0.5);
        context.stroke();
        context.restore();
      }
    });
  }
}

export class EngineBZonePrimitive implements ISeriesPrimitive<Time> {
  private _series: ISeriesApi<'Candlestick', Time> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _zones: EngineBZoneFill[] = [];
  private readonly _paneViews: readonly IPrimitivePaneView[];

  constructor() {
    this._paneViews = [new EngineBZonePaneView(this)];
  }

  attached({ series, requestUpdate }: SeriesAttachedParameter<Time>): void {
    this._series = series as ISeriesApi<'Candlestick', Time>;
    this._requestUpdate = requestUpdate;
  }

  detached(): void {
    this._series = null;
    this._requestUpdate = null;
  }

  setZones(zones: EngineBZoneFill[]): void {
    this._zones = zones;
    this._requestUpdate?.();
  }

  zones(): EngineBZoneFill[] { return this._zones; }
  series(): ISeriesApi<'Candlestick', Time> | null { return this._series; }

  updateAllViews(): void { /* views read directly from primitive state */ }

  paneViews(): readonly IPrimitivePaneView[] { return this._paneViews; }
}
