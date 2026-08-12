import { useEffect, useMemo, useRef, useState } from 'react';
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
  type Time,
  type CandlestickData,
  type LineData,
} from 'lightweight-charts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ErrorBanner } from '@/components/shared';
import { BarChart2 } from 'lucide-react';
import apiClient from '@/lib/apiClient';
import type { EngineASignal } from '@/types/athena';
import { fmtNum, toNum } from '@/lib/utils';

interface RawCandle {
  t?: string | number;
  time?: string | number;
  o?: number | string;
  open?: number | string;
  h?: number | string;
  high?: number | string;
  l?: number | string;
  low?: number | string;
  c?: number | string;
  close?: number | string;
  v?: number | string;
  volume?: number | string;
}

interface CandlesApiResp {
  candles?: RawCandle[];
  symbol?: string;
  tf?: string;
  error?: string;
}

interface Props {
  signal: EngineASignal | null;
}

/** H4 history bar count — matches the prior pair-scan chart contract (~240 bars). */
const H4_CHART_LIMIT = 240;

function toTimestamp(raw: string | number | undefined): UTCTimestamp | null {
  if (raw == null) return null;
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    // Already a unix epoch — accept seconds or ms.
    return (raw > 1e12 ? Math.floor(raw / 1000) : Math.floor(raw)) as UTCTimestamp;
  }
  if (typeof raw === 'string' && raw.length > 0) {
    const ms = Date.parse(raw);
    if (Number.isFinite(ms)) return Math.floor(ms / 1000) as UTCTimestamp;
  }
  return null;
}

function candleField(
  c: RawCandle,
  shortKey: 't' | 'o' | 'h' | 'l' | 'c' | 'v',
  longKey: 'time' | 'open' | 'high' | 'low' | 'close' | 'volume',
): string | number | undefined {
  const short = c[shortKey];
  if (short != null && short !== '') return short;
  return c[longKey];
}

function ema(values: number[], period: number): (number | null)[] {
  if (period <= 1 || values.length === 0) return values.map(() => null);
  const k = 2 / (period + 1);
  const out: (number | null)[] = [];
  let prev: number | null = null;
  let seed = 0;
  let seedCount = 0;
  for (let i = 0; i < values.length; i += 1) {
    const v = values[i];
    if (!Number.isFinite(v)) {
      out.push(prev);
      continue;
    }
    if (i < period) {
      seed += v;
      seedCount += 1;
      if (i === period - 1) {
        prev = seed / seedCount;
        out.push(prev);
      } else {
        out.push(null);
      }
      continue;
    }
    if (prev == null) {
      prev = v;
    } else {
      prev = v * k + prev * (1 - k);
    }
    out.push(prev);
  }
  return out;
}

/**
 * H4 candlestick chart for a selected signal.
 *
 * Fetches /api/candles?tf=H4 (same market-data path as TV chart / Vision).
 * pair-scan deliberately omits OHLC series (attach_chart_candles is AI-review
 * only), so re-running analyze_pair for the chart always returned empty
 * h4Candles. Renders candles with EMA21/50 overlays and horizontal price
 * lines for Entry / SL / TP1 / TP2 from the selected signal. Purely visual —
 * does not call any execution endpoint or alter scoring.
 */
export default function H4ChartCard({ signal }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const ema21SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ema50SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  const [candles, setCandles] = useState<RawCandle[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const symbol = useMemo(
    () => signal?.symbol || signal?.pair || signal?.display || '',
    [signal?.symbol, signal?.pair, signal?.display],
  );

  // Re-fetch candles whenever the selected signal symbol changes.
  // The loading + error resets are the canonical "fetch on dep change"
  // pattern documented by React; the strict set-state-in-effect rule
  // does not apply here because we are starting an async side-effect,
  // not mirroring derived state.
  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);
    const url =
      `/api/candles?symbol=${encodeURIComponent(symbol)}` +
      `&tf=H4&limit=${H4_CHART_LIMIT}`;
    apiClient
      .getJson(url)
      .then((res) => {
        if (cancelled) return;
        const data = res as CandlesApiResp;
        if (data?.error) {
          setError(data.error);
          setCandles(null);
          return;
        }
        const list = data?.candles;
        if (Array.isArray(list) && list.length > 0) {
          setCandles(list);
        } else {
          setCandles([]);
          setError(`No H4 candles returned for ${symbol}`);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Failed to load chart');
        setCandles(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  // Create / dispose the chart instance tied to the container.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 360,
      layout: {
        background: { color: 'transparent' },
        textColor: 'rgba(245, 240, 232, 0.65)',
        fontFamily: "'IBM Plex Mono', monospace",
      },
      grid: {
        vertLines: { color: 'rgba(212, 160, 23, 0.06)' },
        horzLines: { color: 'rgba(212, 160, 23, 0.06)' },
      },
      rightPriceScale: {
        borderColor: 'rgba(212, 160, 23, 0.18)',
      },
      timeScale: {
        borderColor: 'rgba(212, 160, 23, 0.18)',
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: 1,
      },
    });
    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: 'hsl(160, 84%, 39%)',
      downColor: 'hsl(343, 96%, 60%)',
      borderUpColor: 'hsl(160, 84%, 39%)',
      borderDownColor: 'hsl(343, 96%, 60%)',
      wickUpColor: 'hsl(160, 84%, 39%)',
      wickDownColor: 'hsl(343, 96%, 60%)',
      priceLineVisible: false,
    });
    candleSeriesRef.current = candleSeries;

    const ema21 = chart.addSeries(LineSeries, {
      color: 'hsl(200, 95%, 55%)',
      lineWidth: 2,
      lastValueVisible: false,
      priceLineVisible: false,
    });
    ema21SeriesRef.current = ema21;

    const ema50 = chart.addSeries(LineSeries, {
      color: 'hsl(45, 95%, 58%)',
      lineWidth: 2,
      lastValueVisible: false,
      priceLineVisible: false,
    });
    ema50SeriesRef.current = ema50;

    const ro = new ResizeObserver(() => {
      const w = container.clientWidth;
      if (w > 0) chart.applyOptions({ width: w });
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      ema21SeriesRef.current = null;
      ema50SeriesRef.current = null;
    };
  }, []);

  // Push candle + EMA + price-line data whenever candles or signal levels change.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const ema21Series = ema21SeriesRef.current;
    const ema50Series = ema50SeriesRef.current;
    const chart = chartRef.current;
    if (!candleSeries || !ema21Series || !ema50Series || !chart) return;
    if (!candles || candles.length === 0) {
      candleSeries.setData([]);
      ema21Series.setData([]);
      ema50Series.setData([]);
      return;
    }
    const rows: CandlestickData[] = [];
    const closes: number[] = [];
    const times: Time[] = [];
    for (const c of candles) {
      const t = toTimestamp(candleField(c, 't', 'time'));
      const o = toNum(candleField(c, 'o', 'open'), NaN);
      const h = toNum(candleField(c, 'h', 'high'), NaN);
      const l = toNum(candleField(c, 'l', 'low'), NaN);
      const cl = toNum(candleField(c, 'c', 'close'), NaN);
      if (t == null) continue;
      if (![o, h, l, cl].every(Number.isFinite)) continue;
      rows.push({ time: t, open: o, high: h, low: l, close: cl });
      closes.push(cl);
      times.push(t);
    }
    candleSeries.setData(rows);

    const e21 = ema(closes, 21);
    const e50 = ema(closes, 50);
    const e21Data: LineData[] = [];
    const e50Data: LineData[] = [];
    for (let i = 0; i < closes.length; i += 1) {
      if (e21[i] != null) e21Data.push({ time: times[i], value: e21[i] as number });
      if (e50[i] != null) e50Data.push({ time: times[i], value: e50[i] as number });
    }
    ema21Series.setData(e21Data);
    ema50Series.setData(e50Data);

    chart.timeScale().fitContent();
  }, [candles]);

  // Maintain price lines (entry / SL / TPs) — recreated on any signal change.
  const priceLinesRef = useRef<ReturnType<NonNullable<typeof candleSeriesRef.current>['createPriceLine']>[]>([]);
  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;
    // Drop any prior lines first
    for (const pl of priceLinesRef.current) {
      try { series.removePriceLine(pl); } catch { /* noop */ }
    }
    priceLinesRef.current = [];
    if (!signal) return;

    const entry = toNum(signal.entry ?? signal.price, NaN);
    const sl = toNum(signal.sl, NaN);
    const tp1 = toNum(signal.tp ?? signal.tp1, NaN);
    const tp2 = toNum(signal.tp2, NaN);
    const add = (price: number, title: string, color: string) => {
      if (!Number.isFinite(price)) return;
      const pl = series.createPriceLine({
        price,
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title,
      });
      priceLinesRef.current.push(pl);
    };
    add(entry, 'Entry', 'hsl(43, 90%, 46%)');
    add(sl, 'SL', 'hsl(343, 96%, 60%)');
    add(tp1, 'TP1', 'hsl(160, 84%, 39%)');
    add(tp2, 'TP2', 'hsl(160, 84%, 39%)');
  }, [signal]);

  if (!signal) {
    return null;
  }

  return (
    <Card className="border-border/60 bg-card/50">
      <CardHeader className="pb-2">
        <CardTitle
          className="text-xs font-semibold flex items-center justify-between uppercase tracking-wider"
         
        >
          <span className="flex items-center gap-2">
            <BarChart2 className="w-4 h-4 text-primary" />
            H4 Chart · {symbol || '—'}
          </span>
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1 text-[10px] font-normal text-muted-foreground">
              <span className="inline-block w-2 h-2 rounded-full" style={{ background: 'hsl(200, 95%, 55%)' }} />
              EMA21
            </span>
            <span className="flex items-center gap-1 text-[10px] font-normal text-muted-foreground">
              <span className="inline-block w-2 h-2 rounded-full" style={{ background: 'hsl(45, 95%, 58%)' }} />
              EMA50
            </span>
            {Number.isFinite(toNum(signal.entry ?? signal.price)) && (
              <Badge variant="outline" className="text-[10px] font-mono">
                {fmtNum(signal.entry ?? signal.price, 5)}
              </Badge>
            )}
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {error && <ErrorBanner message={error} />}
        <div className="relative w-full">
          <div ref={containerRef} className="w-full" style={{ height: 360 }} />
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center text-[11px] text-muted-foreground bg-card/40 backdrop-blur-sm">
              Loading H4 candles…
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3 text-[10px] text-muted-foreground pt-1">
          <span className="flex items-center gap-1">
            <span className="inline-block w-2.5 h-0.5" style={{ background: 'hsl(43, 90%, 46%)' }} />
            Entry
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-2.5 h-0.5" style={{ background: 'hsl(343, 96%, 60%)' }} />
            SL
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-2.5 h-0.5" style={{ background: 'hsl(160, 84%, 39%)' }} />
            TP
          </span>
          <span className="ml-auto">{H4_CHART_LIMIT} H4 bars · sourced from /api/candles</span>
        </div>
      </CardContent>
    </Card>
  );
}
