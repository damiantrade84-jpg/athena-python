import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from 'lightweight-charts';
import type { CanvasRenderingTarget2D } from 'fancy-canvas';

export type OrderFlowMarkerShape = 'arrowUp' | 'arrowDown' | 'circle' | 'square';

/**
 * A single order-flow marker anchored at an exact (time, price) on the chart.
 * Unlike lightweight-charts series markers (which attach above/below a bar by
 * time only), these are drawn at the trade's actual price via a custom
 * ISeriesPrimitive renderer, so absorption / large-trade / initiative events
 * sit where they happened on the price axis.
 */
export interface OrderFlowPrimitiveMarker {
  time: Time;
  price: number;
  color: string;
  shape: OrderFlowMarkerShape;
  text: string;
  /** Where the text label sits relative to the glyph. Defaults to 'above'. */
  labelPlacement?: 'above' | 'below';
}

const LABEL_FONT = '600 10px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace';
const GLYPH_RADIUS = 5;
const LABEL_PAD_X = 3;
const LABEL_GAP = 8;

function nearestBarTime(barTimes: number[], target: number): number | null {
  if (barTimes.length === 0) return null;
  let best = barTimes[0];
  let bestDelta = Math.abs(best - target);
  for (let i = 1; i < barTimes.length; i += 1) {
    const delta = Math.abs(barTimes[i] - target);
    if (delta < bestDelta) {
      best = barTimes[i];
      bestDelta = delta;
    }
  }
  return best;
}

class OrderFlowMarkerRenderer implements IPrimitivePaneRenderer {
  private readonly primitive: OrderFlowMarkerPrimitive;

  constructor(primitive: OrderFlowMarkerPrimitive) {
    this.primitive = primitive;
  }

  draw(target: CanvasRenderingTarget2D): void {
    const markers = this.primitive.markers();
    const series = this.primitive.series();
    const chart = this.primitive.chart();
    if (!series || !chart || markers.length === 0) return;

    const timeScale = chart.timeScale();
    // Bar times (epoch seconds) used to snap sub-bar event times onto the grid
    // so the x coordinate always resolves; the y coordinate stays exact (price).
    const barTimes = (series.data() as ReadonlyArray<{ time: Time }>)
      .map((d) => d.time)
      .filter((t): t is number => typeof t === 'number');

    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      for (const marker of markers) {
        let x = timeScale.timeToCoordinate(marker.time);
        if (x == null && typeof marker.time === 'number') {
          const snapped = nearestBarTime(barTimes, marker.time);
          if (snapped != null) x = timeScale.timeToCoordinate(snapped as Time);
        }
        const y = series.priceToCoordinate(marker.price);
        if (x == null || y == null) continue;
        if (x < 0 || x > mediaSize.width || y < 0 || y > mediaSize.height) continue;

        context.save();
        drawGlyph(context, marker.shape, x, y, marker.color);
        drawLabel(context, marker.text, x, y, marker.color, marker.labelPlacement ?? 'above');
        context.restore();
      }
    });
  }
}

function drawGlyph(
  context: CanvasRenderingContext2D,
  shape: OrderFlowMarkerShape,
  x: number,
  y: number,
  color: string,
): void {
  context.fillStyle = color;
  context.strokeStyle = color;
  context.lineWidth = 1;
  switch (shape) {
    case 'circle':
      context.beginPath();
      context.arc(x, y, GLYPH_RADIUS, 0, Math.PI * 2);
      context.fill();
      break;
    case 'square':
      context.fillRect(x - GLYPH_RADIUS, y - GLYPH_RADIUS, GLYPH_RADIUS * 2, GLYPH_RADIUS * 2);
      break;
    case 'arrowUp':
      context.beginPath();
      context.moveTo(x, y - GLYPH_RADIUS);
      context.lineTo(x + GLYPH_RADIUS, y + GLYPH_RADIUS);
      context.lineTo(x - GLYPH_RADIUS, y + GLYPH_RADIUS);
      context.closePath();
      context.fill();
      break;
    case 'arrowDown':
      context.beginPath();
      context.moveTo(x, y + GLYPH_RADIUS);
      context.lineTo(x + GLYPH_RADIUS, y - GLYPH_RADIUS);
      context.lineTo(x - GLYPH_RADIUS, y - GLYPH_RADIUS);
      context.closePath();
      context.fill();
      break;
  }
}

function drawLabel(
  context: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  color: string,
  placement: 'above' | 'below',
): void {
  if (!text) return;
  context.font = LABEL_FONT;
  const textWidth = context.measureText(text).width;
  const boxWidth = textWidth + LABEL_PAD_X * 2;
  const boxHeight = 14;
  const boxX = x - boxWidth / 2;
  const boxY = placement === 'above' ? y - LABEL_GAP - boxHeight : y + LABEL_GAP;

  context.fillStyle = 'rgba(8, 12, 16, 0.82)';
  context.strokeStyle = color;
  context.lineWidth = 1;
  context.beginPath();
  context.roundRect(boxX, boxY, boxWidth, boxHeight, 3);
  context.fill();
  context.stroke();

  context.fillStyle = color;
  context.textBaseline = 'middle';
  context.textAlign = 'center';
  context.fillText(text, x, boxY + boxHeight / 2);
}

class OrderFlowMarkerPaneView implements IPrimitivePaneView {
  private readonly primitive: OrderFlowMarkerPrimitive;

  constructor(primitive: OrderFlowMarkerPrimitive) {
    this.primitive = primitive;
  }

  zOrder(): 'top' {
    return 'top';
  }

  renderer(): IPrimitivePaneRenderer | null {
    return new OrderFlowMarkerRenderer(this.primitive);
  }
}

export class OrderFlowMarkerPrimitive implements ISeriesPrimitive<Time> {
  private _chart: IChartApi | null = null;
  private _series: ISeriesApi<'Candlestick', Time> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _markers: OrderFlowPrimitiveMarker[] = [];
  private readonly _paneViews: readonly IPrimitivePaneView[];

  constructor() {
    this._paneViews = [new OrderFlowMarkerPaneView(this)];
  }

  attached({ chart, series, requestUpdate }: SeriesAttachedParameter<Time>): void {
    this._chart = chart;
    this._series = series as ISeriesApi<'Candlestick', Time>;
    this._requestUpdate = requestUpdate;
  }

  detached(): void {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  setMarkers(markers: OrderFlowPrimitiveMarker[]): void {
    this._markers = markers;
    this._requestUpdate?.();
  }

  markers(): OrderFlowPrimitiveMarker[] {
    return this._markers;
  }

  chart(): IChartApi | null {
    return this._chart;
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
