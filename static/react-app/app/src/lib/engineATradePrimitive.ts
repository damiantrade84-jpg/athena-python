import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from 'lightweight-charts';

/**
 * TradingView's position tool, as an ISeriesPrimitive: a translucent reward band
 * from entry to the furthest target, a risk band from entry to the stop, and one
 * compact `NAME price` tag per level at the left of the plot.
 *
 * The tags live at the left because entry/stop/target cluster within a few ATR of
 * live price, which sits at the right edge — stacking their tags there buried the
 * most recent candles under a wall of chips.
 */

export type TradeLevelRole = 'entry' | 'stop' | 'target';

export interface TradeLevel {
  role: TradeLevelRole;
  label: string;
  price: number;
}

export const TRADE_POSITION_STYLE: Record<TradeLevelRole, { text: string; band: string }> = {
  // Bands stay at the same 0.10 alpha as the Engine B zone fills so a trade never
  // shouts louder than the structure it sits inside.
  entry: { text: '#7dd3fc', band: 'rgba(56, 189, 248, 0.00)' },
  stop: { text: '#fda4af', band: 'rgba(244, 63, 94, 0.10)' },
  target: { text: '#6ee7b7', band: 'rgba(16, 185, 129, 0.10)' },
};

const LABEL_FONT = '500 9px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace';
const LABEL_HEIGHT_PX = 13;
const LABEL_PAD_X = 4;
const LABEL_LEFT_PX = 8;

export interface TradeLabelRow {
  id: string;
  y: number;
}

export type LaidOutTradeLabelRow = TradeLabelRow & { layoutY: number };

/**
 * Minimal vertical de-overlap: a tag moves only far enough to stop touching the
 * one above it, and never clamps to the pane edge. Unlike the right-edge chip
 * stacker this adds no forced gap, so a tag stays on its own line whenever the
 * levels are more than a tag-height apart.
 */
export function layoutTradeLabelRows(
  rows: TradeLabelRow[],
  labelHeightPx = LABEL_HEIGHT_PX,
): LaidOutTradeLabelRow[] {
  const sorted = [...rows].sort((a, b) => a.y - b.y);
  const placed: LaidOutTradeLabelRow[] = [];
  let lastBottom = Number.NEGATIVE_INFINITY;

  for (const row of sorted) {
    let layoutY = row.y;
    if (layoutY - labelHeightPx / 2 < lastBottom) {
      layoutY = lastBottom + labelHeightPx / 2;
    }
    placed.push({ ...row, layoutY });
    lastBottom = layoutY + labelHeightPx / 2;
  }
  return placed;
}

/** Entry-to-stop and entry-to-furthest-target spans, in price. */
export function tradeBands(levels: TradeLevel[]): { role: TradeLevelRole; from: number; to: number }[] {
  const entry = levels.find((level) => level.role === 'entry');
  if (!entry) return [];
  const bands: { role: TradeLevelRole; from: number; to: number }[] = [];

  const stop = levels.find((level) => level.role === 'stop');
  if (stop) bands.push({ role: 'stop', from: entry.price, to: stop.price });

  const targets = levels.filter((level) => level.role === 'target');
  if (targets.length > 0) {
    const furthest = targets.reduce((best, level) =>
      Math.abs(level.price - entry.price) > Math.abs(best.price - entry.price) ? level : best,
    );
    bands.push({ role: 'target', from: entry.price, to: furthest.price });
  }
  return bands;
}

class TradeBandRenderer implements IPrimitivePaneRenderer {
  private readonly primitive: EngineATradePrimitive;

  constructor(primitive: EngineATradePrimitive) {
    this.primitive = primitive;
  }

  draw(target: CanvasRenderingTarget2D): void {
    const series = this.primitive.series();
    const levels = this.primitive.levels();
    if (!series || levels.length === 0) return;

    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      for (const band of tradeBands(levels)) {
        const yFrom = series.priceToCoordinate(band.from);
        const yTo = series.priceToCoordinate(band.to);
        if (yFrom == null || yTo == null) continue;
        const top = Math.min(yFrom, yTo);
        const height = Math.max(1, Math.abs(yTo - yFrom));
        context.save();
        context.fillStyle = TRADE_POSITION_STYLE[band.role].band;
        context.fillRect(0, top, mediaSize.width, height);
        context.restore();
      }
    });
  }
}

class TradeLabelRenderer implements IPrimitivePaneRenderer {
  private readonly primitive: EngineATradePrimitive;

  constructor(primitive: EngineATradePrimitive) {
    this.primitive = primitive;
  }

  draw(target: CanvasRenderingTarget2D): void {
    const series = this.primitive.series();
    const levels = this.primitive.levels();
    if (!series || levels.length === 0) return;
    const formatPrice = this.primitive.formatPrice();

    target.useMediaCoordinateSpace(({ context }) => {
      const rows: TradeLabelRow[] = [];
      const byId = new Map<string, TradeLevel>();
      for (const [index, level] of levels.entries()) {
        const y = series.priceToCoordinate(level.price);
        if (y == null || !Number.isFinite(y)) continue;
        const id = `${index}-${level.label}`;
        rows.push({ id, y });
        byId.set(id, level);
      }

      for (const row of layoutTradeLabelRows(rows)) {
        const level = byId.get(row.id);
        if (!level) continue;
        const style = TRADE_POSITION_STYLE[level.role];
        const text = `${level.label} ${formatPrice(level.price)}`;
        context.save();
        context.font = LABEL_FONT;
        const boxWidth = context.measureText(text).width + LABEL_PAD_X * 2;
        const yTop = row.layoutY - LABEL_HEIGHT_PX / 2;

        // A de-overlapped tag gets a leader back to its true price so it never
        // reads as if the level were where the tag landed.
        if (Math.abs(row.layoutY - row.y) > 1) {
          context.globalAlpha = 0.5;
          context.strokeStyle = style.text;
          context.lineWidth = 1;
          context.beginPath();
          context.moveTo(LABEL_LEFT_PX + boxWidth + 8, row.y);
          context.lineTo(LABEL_LEFT_PX + boxWidth + 1, row.layoutY);
          context.stroke();
          context.globalAlpha = 1;
        }

        context.fillStyle = 'rgba(9, 13, 20, 0.78)';
        context.beginPath();
        context.roundRect(LABEL_LEFT_PX, yTop, boxWidth, LABEL_HEIGHT_PX, 3);
        context.fill();

        context.fillStyle = style.text;
        context.textBaseline = 'middle';
        context.textAlign = 'left';
        context.fillText(text, LABEL_LEFT_PX + LABEL_PAD_X, row.layoutY);
        context.restore();
      }
    });
  }
}

class TradeBandPaneView implements IPrimitivePaneView {
  private readonly primitive: EngineATradePrimitive;

  constructor(primitive: EngineATradePrimitive) {
    this.primitive = primitive;
  }

  zOrder(): 'bottom' {
    return 'bottom';
  }

  renderer(): IPrimitivePaneRenderer | null {
    return new TradeBandRenderer(this.primitive);
  }
}

class TradeLabelPaneView implements IPrimitivePaneView {
  private readonly primitive: EngineATradePrimitive;

  constructor(primitive: EngineATradePrimitive) {
    this.primitive = primitive;
  }

  zOrder(): 'top' {
    return 'top';
  }

  renderer(): IPrimitivePaneRenderer | null {
    return new TradeLabelRenderer(this.primitive);
  }
}

export class EngineATradePrimitive implements ISeriesPrimitive<Time> {
  private _series: ISeriesApi<'Candlestick', Time> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _levels: TradeLevel[] = [];
  private _formatPrice: (price: number) => string = (price) => String(price);
  private readonly _paneViews: readonly IPrimitivePaneView[];

  constructor() {
    this._paneViews = [new TradeBandPaneView(this), new TradeLabelPaneView(this)];
  }

  attached({ series, requestUpdate }: SeriesAttachedParameter<Time>): void {
    this._series = series as ISeriesApi<'Candlestick', Time>;
    this._requestUpdate = requestUpdate;
  }

  detached(): void {
    this._series = null;
    this._requestUpdate = null;
  }

  setLevels(levels: TradeLevel[], formatPrice: (price: number) => string): void {
    this._levels = levels;
    this._formatPrice = formatPrice;
    this._requestUpdate?.();
  }

  levels(): TradeLevel[] {
    return this._levels;
  }

  formatPrice(): (price: number) => string {
    return this._formatPrice;
  }

  series(): ISeriesApi<'Candlestick', Time> | null {
    return this._series;
  }

  updateAllViews(): void {
    /* views read directly from primitive state */
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews;
  }
}
