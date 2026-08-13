import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from 'lightweight-charts';
import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import {
  layoutRightEdgeLabels,
  RIGHT_EDGE_LABEL_HEIGHT_PX,
  RIGHT_EDGE_LABEL_PRIORITY,
  type LayoutRightEdgeLabel,
  type RightEdgeLabel,
} from '@/lib/chartRightEdgeLabels';

const LABEL_FONT = '500 9px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace';
const LABEL_PAD_X = 3;
const RIGHT_MARGIN_PX = 68;

class ChartRightEdgeLabelRenderer implements IPrimitivePaneRenderer {
  private readonly primitive: ChartRightEdgeLabelPrimitive;

  constructor(primitive: ChartRightEdgeLabelPrimitive) {
    this.primitive = primitive;
  }

  draw(target: CanvasRenderingTarget2D): void {
    const labels = this.primitive.layoutLabels();
    if (labels.length === 0) return;

    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      for (const label of labels) {
        const y = label.layoutY;
        context.save();
        context.font = LABEL_FONT;
        const textWidth = context.measureText(label.text).width;
        const boxWidth = textWidth + LABEL_PAD_X * 2;
        const boxHeight = RIGHT_EDGE_LABEL_HEIGHT_PX;
        // The current-price label sits next to the price-axis value and obscures
        // the most recent candles; anchor it to the left edge instead.
        const isCurrentPrice = label.priority === RIGHT_EDGE_LABEL_PRIORITY.currentPrice;
        const x = isCurrentPrice ? 6 : mediaSize.width - 6 - boxWidth;
        const yTop = y - boxHeight / 2;

        // Collision stacking can lift a chip off its own level. Draw a leader
        // back to the true price so a displaced chip never reads as if the level
        // were where the chip sits.
        if (!isCurrentPrice && Math.abs(label.y - y) > 1) {
          context.globalAlpha = 0.5;
          context.strokeStyle = label.color;
          context.lineWidth = 1;
          context.beginPath();
          context.moveTo(x - 9, label.y);
          context.lineTo(x - 1, y);
          context.stroke();
          context.globalAlpha = 1;
        }

        context.fillStyle = 'rgba(13, 18, 26, 0.72)';
        context.strokeStyle = label.color;
        context.lineWidth = 1;
        context.beginPath();
        context.roundRect(x, yTop, boxWidth, boxHeight, 4);
        context.fill();
        context.stroke();

        context.fillStyle = label.color;
        context.textBaseline = 'middle';
        context.textAlign = 'left';
        context.fillText(label.text, x + LABEL_PAD_X, y);
        context.restore();
      }
    });
  }
}

class ChartRightEdgeLabelPaneView implements IPrimitivePaneView {
  private readonly primitive: ChartRightEdgeLabelPrimitive;

  constructor(primitive: ChartRightEdgeLabelPrimitive) {
    this.primitive = primitive;
  }

  zOrder(): 'top' {
    return 'top';
  }

  renderer(): IPrimitivePaneRenderer | null {
    return new ChartRightEdgeLabelRenderer(this.primitive);
  }
}

export class ChartRightEdgeLabelPrimitive implements ISeriesPrimitive<Time> {
  private _series: ISeriesApi<'Candlestick', Time> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _labels: RightEdgeLabel[] = [];
  private _paneHeightPx = 0;
  private readonly _paneViews: readonly IPrimitivePaneView[];

  constructor() {
    this._paneViews = [new ChartRightEdgeLabelPaneView(this)];
  }

  attached({ series, requestUpdate }: SeriesAttachedParameter<Time>): void {
    this._series = series as ISeriesApi<'Candlestick', Time>;
    this._requestUpdate = requestUpdate;
  }

  detached(): void {
    this._series = null;
    this._requestUpdate = null;
  }

  setLabels(labels: RightEdgeLabel[], paneHeightPx = 0): void {
    this._labels = labels;
    this._paneHeightPx = paneHeightPx;
    this._requestUpdate?.();
  }

  layoutLabels(): LayoutRightEdgeLabel[] {
    return layoutRightEdgeLabels(this._labels, {
      paneHeightPx: this._paneHeightPx > 0 ? this._paneHeightPx : undefined,
    });
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

export { RIGHT_MARGIN_PX };
