import { useMemo, useState } from 'react';
import { BarChart3, Layers, SlidersHorizontal } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { useStore } from '@/hooks/useStore';
import { fmtNum } from '@/lib/utils';
import type { EngineASignal } from '@/types/athena';

const TIMEFRAMES = ['1', '5', '15', '30', '60', '240', 'D', 'W'];

export const TV_STUDY_IDS = {
  ema: 'MAExp@tv-basicstudies',
  dema: 'DoubleEMA@tv-basicstudies',
  atr: 'ATR@tv-basicstudies',
  rsi: 'RSI@tv-basicstudies',
} as const;

type StudyOptions = {
  ema20: boolean;
  ema50: boolean;
  ema200: boolean;
  dema200: boolean;
  atr14: boolean;
  rsi14: boolean;
};

type TradingViewStudy =
  | string
  | {
      id: string;
      inputs?: Record<string, number | string | boolean>;
    };

type TradingViewWidgetConfig = {
  autosize: boolean;
  symbol: string;
  interval: string;
  timezone: string;
  theme: string;
  style: string;
  locale: string;
  withdateranges: boolean;
  hide_side_toolbar: boolean;
  allow_symbol_change: boolean;
  details: boolean;
  hotlist: boolean;
  calendar: boolean;
  studies: TradingViewStudy[];
  support_host: string;
};

function formatSymbol(input: string): string {
  const clean = input.toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (clean.endsWith('USDT')) return `BINANCE:${clean}`;
  if (clean.length === 6 && /^[A-Z]{6}$/.test(clean)) return `FX:${clean}`;
  return clean.includes(':') ? clean : `FX:${clean}`;
}

function displaySymbol(signal: EngineASignal | null): string | null {
  if (!signal) return null;
  return signal.display || signal.symbol || signal.pair || null;
}

function normalizeDirection(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const normalized = value.toUpperCase();
  if (normalized === 'LONG' || normalized === 'BUY') return 'LONG';
  if (normalized === 'SHORT' || normalized === 'SELL') return 'SHORT';
  return normalized || null;
}

function firstNumber(...values: unknown[]): number | null {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() !== '') {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === 'string' && value.trim() !== '') return value;
  }
  return null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function pickEngineACandidate(rows: unknown[]): EngineASignal | null {
  const candidates = rows.filter((row): row is EngineASignal => Boolean(row && typeof row === 'object'));
  return candidates.find((row) => Boolean(normalizeDirection(row.direction))) || candidates[0] || null;
}

function symbolKey(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const upper = value.trim().toUpperCase();
  if (!upper) return null;
  const withoutProvider = upper.includes(':') ? upper.split(':').pop() || upper : upper;
  const withoutYahooFxSuffix = withoutProvider.replace(/=X$/, '');
  const key = withoutYahooFxSuffix.replace(/[^A-Z0-9]/g, '');
  return key || null;
}

function findEngineACandidateForSymbol(rows: EngineASignal[], symbol: string): EngineASignal | null {
  const chartKey = symbolKey(symbol);
  if (!chartKey) return null;
  return rows.find((row) => {
    const keys = [displaySymbol(row), row.symbol, row.pair].map(symbolKey);
    return keys.includes(chartKey);
  }) || null;
}

function reviewTimeframeFor(signal: EngineASignal | null): string {
  const rawTimeframe = firstString(signal?.timeframe, signal?.tf, signal?.interval)?.toUpperCase();
  if (rawTimeframe === 'H1' || rawTimeframe === '1H' || rawTimeframe === '60') return '60';
  if (rawTimeframe === 'H4' || rawTimeframe === '4H' || rawTimeframe === '240') return '240';
  const style = firstString(signal?.style, signal?.trade_style)?.toLowerCase();
  return style === 'scalp' || style === 'intraday' ? '60' : '240';
}

export function buildTradingViewStudies(options: StudyOptions): TradingViewStudy[] {
  const studies: TradingViewStudy[] = [];
  if (options.ema20) studies.push({ id: TV_STUDY_IDS.ema, inputs: { length: 20 } });
  if (options.ema50) studies.push({ id: TV_STUDY_IDS.ema, inputs: { length: 50 } });
  if (options.ema200) studies.push({ id: TV_STUDY_IDS.ema, inputs: { length: 200 } });
  if (options.dema200) studies.push({ id: TV_STUDY_IDS.dema, inputs: { length: 200 } });
  if (options.atr14) studies.push({ id: TV_STUDY_IDS.atr, inputs: { length: 14 } });
  if (options.rsi14) studies.push({ id: TV_STUDY_IDS.rsi, inputs: { length: 14 } });
  return studies;
}

export function buildTradingViewWidgetConfig(
  symbol: string,
  interval: string,
  studies: TradingViewStudy[],
): TradingViewWidgetConfig {
  return {
    autosize: true,
    symbol,
    interval,
    timezone: 'Etc/UTC',
    theme: 'dark',
    style: '1',
    locale: 'en',
    withdateranges: true,
    hide_side_toolbar: false,
    allow_symbol_change: true,
    details: true,
    hotlist: false,
    calendar: false,
    studies,
    support_host: 'https://www.tradingview.com',
  };
}

export function buildTradingViewWidgetHtml(config: TradingViewWidgetConfig): string {
  const payload = JSON.stringify(config).replace(/</g, '\\u003c');
  return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <style>
      html, body, .tradingview-widget-container, .tradingview-widget-container__widget {
        height: 100%;
        margin: 0;
        background: #0b0f14;
      }
    </style>
  </head>
  <body>
    <div class="tradingview-widget-container">
      <div class="tradingview-widget-container__widget"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>
      ${payload}
      </script>
    </div>
  </body>
</html>`;
}

function NumberRow({ label, value }: { label: string; value: unknown }) {
  const numeric = firstNumber(value);
  return (
    <div className="flex items-center justify-between gap-3 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono text-foreground">{numeric === null ? 'Unavailable' : fmtNum(numeric, 5)}</span>
    </div>
  );
}

function TextRow({ label, value }: { label: string; value: unknown }) {
  const text = firstString(value);
  return (
    <div className="flex items-center justify-between gap-3 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="break-words text-right text-foreground">{text || 'Unavailable'}</span>
    </div>
  );
}

function DiagnosticBlock({ label, value }: { label: string; value: unknown }) {
  const record = asRecord(value);
  if (!Object.keys(record).length) return null;
  return (
    <div className="space-y-1 text-xs">
      <div className="text-muted-foreground">{label}</div>
      <pre className="whitespace-pre-wrap break-words rounded border border-border/50 bg-background/60 p-2 text-[11px] text-foreground">
        {JSON.stringify(record, null, 2)}
      </pre>
    </div>
  );
}

function EngineASidePanel({ signal }: { signal: EngineASignal | null }) {
  const factorScores = asRecord(signal?.factorScores);
  const diagnostics = asRecord(signal?.factorDiagnostics);
  const trendCoherence = asRecord(diagnostics.trendCoherence);
  const feedStatus = asRecord(diagnostics.feedStatus);
  const engineAAssetDiagnostics = asRecord(diagnostics.engineAAssetDiagnostics);
  const momentumQuality = firstNumber(diagnostics.momentumQuality, diagnostics.momentum_quality, factorScores.momentum);
  const addonUnsupported = Boolean(diagnostics.addonUnsupported || diagnostics.addon_unsupported);
  const addonValue = firstNumber(diagnostics.addon_value, diagnostics.addonValue, factorScores.addon);
  const addonStatus = addonUnsupported
    ? 'UNAVAILABLE'
    : firstString(feedStatus.addon) || (addonValue === null ? 'missing' : addonValue > 0 ? 'confirming' : addonValue < 0 ? 'opposing' : 'neutral');
  const addonLabel = firstString(signal?.type)?.toLowerCase() === 'forex' ? 'Carry addon' : 'Addon';
  const atrDiagnostics = asRecord(signal?.atrDiagnostics);
  const direction = normalizeDirection(signal?.direction);
  const score = firstNumber(signal?.confluenceScore, signal?.score, signal?.final_score);
  const maxScore = firstNumber(signal?.maxScore, signal?.max_score) ?? 3;
  const threshold = firstNumber(signal?.threshold, signal?.scoreThreshold, signal?.score_threshold);
  const swingLevels = Array.isArray(signal?.swingLevels) ? signal?.swingLevels : Array.isArray(signal?.priorSwingLevels) ? signal?.priorSwingLevels : [];

  return (
    <aside className="min-w-0 space-y-3 rounded-md border bg-card/70 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs uppercase tracking-wide text-muted-foreground">Engine A Candidate</p>
          <h3 className="truncate text-sm font-semibold">{displaySymbol(signal) || 'No candidate'}</h3>
        </div>
        <Badge variant={direction === 'SHORT' ? 'destructive' : 'secondary'}>{direction || 'UNAVAILABLE'}</Badge>
      </div>

      <section className="space-y-2 rounded-md border border-border/60 p-2">
        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">Score</span>
          <span className="font-mono">{score === null ? 'Unavailable' : `${fmtNum(score, 2)} / ${fmtNum(maxScore, 1)}`}</span>
        </div>
        <NumberRow label="Threshold" value={threshold} />
        <NumberRow label="Entry" value={firstNumber(signal?.entry, signal?.price)} />
        <NumberRow label="SL" value={signal?.sl} />
        <NumberRow label="TP" value={firstNumber(signal?.tp, signal?.tp1)} />
      </section>

      <section className="space-y-2 rounded-md border border-border/60 p-2">
        <div className="flex items-center gap-2 text-xs font-semibold">
          <BarChart3 className="h-3.5 w-3.5" />
          Trend
        </div>
        <NumberRow label="Trend score" value={factorScores.trend} />
        <NumberRow
          label="Directional ramp"
          value={firstNumber(diagnostics.directionalRampMult, diagnostics.directionalRampMultiplier, diagnostics.directional_ramp_multiplier)}
        />
        <NumberRow label="Min directional" value={firstNumber(diagnostics.minDirectional, diagnostics.minDirectionalThreshold, diagnostics.min_directional_threshold)} />
        <NumberRow label="Effective min directional" value={firstNumber(diagnostics.effectiveMinDirectional, diagnostics.effective_min_directional)} />
        <NumberRow label="Agreement count" value={trendCoherence.agreement_count} />
        <NumberRow label="Coherence ratio" value={trendCoherence.coherence_ratio} />
        <DiagnosticBlock label="Trend coherence" value={trendCoherence} />
      </section>

      <section className="space-y-2 rounded-md border border-border/60 p-2">
        <div className="flex items-center gap-2 text-xs font-semibold">
          <Layers className="h-3.5 w-3.5" />
          Momentum / Addon
        </div>
        <NumberRow label="Momentum quality" value={momentumQuality} />
        <NumberRow label="Momentum score" value={factorScores.momentum} />
        <NumberRow label={`${addonLabel} value`} value={addonValue} />
        <TextRow label={`${addonLabel} status`} value={addonStatus} />
        <TextRow label="Feed addon" value={feedStatus.addon} />
        <DiagnosticBlock label="Feed status" value={feedStatus} />
        <DiagnosticBlock label="Engine A asset diagnostics" value={engineAAssetDiagnostics} />
      </section>

      <section className="space-y-2 rounded-md border border-border/60 p-2">
        <div className="text-xs font-semibold">Risk</div>
        <NumberRow label="ATR" value={firstNumber(signal?.atr, atrDiagnostics.atr, atrDiagnostics.atr_value)} />
        <TextRow label="ATR timeframe" value={firstString(atrDiagnostics.atr_tf, atrDiagnostics.atrTimeframe)} />
        <TextRow label="ATR source" value={firstString(atrDiagnostics.atr_source, atrDiagnostics.atrSource)} />
        <NumberRow label="RR" value={firstNumber(signal?.rr, signal?.rr1)} />
        <DiagnosticBlock label="ATR diagnostics" value={atrDiagnostics} />
      </section>

      <section className="space-y-2 rounded-md border border-border/60 p-2">
        <div className="text-xs font-semibold">Prior Swing Levels</div>
        {swingLevels.length > 0 ? (
          swingLevels.slice(0, 4).map((level, index) => (
            <TextRow key={`${index}-${JSON.stringify(level)}`} label={`Level ${index + 1}`} value={JSON.stringify(level)} />
          ))
        ) : (
          <p className="text-xs text-muted-foreground">Unavailable — Engine A SL/TP is ATR-based; no structural swing levels supplied.</p>
        )}
      </section>

      <p className="text-[11px] leading-4 text-muted-foreground">
        Entry, SL, TP, and swing levels are side-panel values only. The TradingView iframe is not drawing custom levels.
      </p>
    </aside>
  );
}

function IndicatorSwitch({
  label,
  checked,
  onCheckedChange,
}: {
  label: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-xs">
      <Switch checked={checked} onCheckedChange={onCheckedChange} />
      {label}
    </label>
  );
}

export default function TVChartPanel() {
  const { scanCacheA } = useStore();
  const [pair, setPair] = useState('EURUSD');
  const [timeframe, setTimeframe] = useState('60');
  const [ema20, setEma20] = useState(true);
  const [ema50, setEma50] = useState(true);
  const [ema200, setEma200] = useState(false);
  const [dema200, setDema200] = useState(false);
  const [atr14, setAtr14] = useState(false);
  const [rsi14, setRsi14] = useState(false);

  const candidateRows = useMemo(
    () => (Array.isArray(scanCacheA) ? scanCacheA.filter((row): row is EngineASignal => Boolean(row && typeof row === 'object')) : []),
    [scanCacheA],
  );
  const defaultCandidate = useMemo(() => pickEngineACandidate(candidateRows), [candidateRows]);
  const chartCandidate = useMemo(() => findEngineACandidateForSymbol(candidateRows, pair), [candidateRows, pair]);

  const tvSymbol = useMemo(() => formatSymbol(pair), [pair]);
  const studies = useMemo(
    () => buildTradingViewStudies({ ema20, ema50, ema200, dema200, atr14, rsi14 }),
    [ema20, ema50, ema200, dema200, atr14, rsi14],
  );
  const widgetConfig = useMemo(() => buildTradingViewWidgetConfig(tvSymbol, timeframe, studies), [tvSymbol, timeframe, studies]);
  const widgetHtml = useMemo(() => buildTradingViewWidgetHtml(widgetConfig), [widgetConfig]);
  const frameKey = useMemo(() => JSON.stringify(widgetConfig), [widgetConfig]);

  const applyEngineAReviewLayout = () => {
    const candidate = chartCandidate || defaultCandidate;
    const candidateSymbol = displaySymbol(candidate);
    if (candidateSymbol) setPair(candidateSymbol);
    setTimeframe(reviewTimeframeFor(candidate));
    setEma20(true);
    setEma50(true);
    setEma200(true);
    setDema200(false);
    setAtr14(true);
    setRsi14(true);
  };

  return (
    <Card className="h-full">
      <CardHeader className="pb-2">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <BarChart3 className="h-5 w-5" />
            TV Chart
          </CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={pair}
              onChange={(event) => setPair(event.target.value)}
              className="h-8 w-32 text-xs"
              aria-label="TradingView symbol"
            />
            <select
              value={displaySymbol(chartCandidate) || ''}
              onChange={(event) => {
                if (event.target.value) setPair(event.target.value);
              }}
              className="h-8 w-40 rounded-md border border-input bg-background px-2 text-xs"
              aria-label="Engine A candidate"
            >
              <option value="">No candidate selected</option>
              {candidateRows.map((candidate, index) => {
                const candidateSymbol = displaySymbol(candidate) || `Candidate ${index + 1}`;
                const direction = normalizeDirection(candidate.direction);
                return (
                  <option key={`${candidateSymbol}-${index}`} value={candidateSymbol}>
                    {candidateSymbol}{direction ? ` ${direction}` : ''}
                  </option>
                );
              })}
            </select>
            <div className="flex flex-wrap gap-1">
              {TIMEFRAMES.map((tf) => (
                <Button
                  key={tf}
                  size="sm"
                  variant={timeframe === tf ? 'default' : 'outline'}
                  className="h-8 px-2 text-xs"
                  onClick={() => setTimeframe(tf)}
                >
                  {tf}
                </Button>
              ))}
            </div>
            <Button size="sm" variant="secondary" className="h-8 gap-2 text-xs" onClick={applyEngineAReviewLayout}>
              <SlidersHorizontal className="h-3.5 w-3.5" />
              Engine A Review Layout
            </Button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3 pt-2">
          <IndicatorSwitch label="EMA20" checked={ema20} onCheckedChange={setEma20} />
          <IndicatorSwitch label="EMA50" checked={ema50} onCheckedChange={setEma50} />
          <IndicatorSwitch label="EMA200" checked={ema200} onCheckedChange={setEma200} />
          <IndicatorSwitch label="DEMA200" checked={dema200} onCheckedChange={setDema200} />
          <IndicatorSwitch label="ATR14" checked={atr14} onCheckedChange={setAtr14} />
          <IndicatorSwitch label="RSI14" checked={rsi14} onCheckedChange={setRsi14} />
        </div>
      </CardHeader>
      <CardContent className="h-[calc(100%-120px)] min-h-[620px]">
        <div className="grid h-full gap-3 xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="relative min-h-[560px] overflow-hidden rounded-md border bg-background">
            <iframe
              key={frameKey}
              title="TradingView Advanced Chart"
              srcDoc={widgetHtml}
              className="h-full w-full border-0"
              allow="fullscreen"
            />
          </div>
          <EngineASidePanel signal={chartCandidate} />
        </div>
      </CardContent>
    </Card>
  );
}
