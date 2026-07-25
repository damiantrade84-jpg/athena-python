import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ArrowUpRight, Play } from 'lucide-react';
import { cn, fmtNum, toNum } from '@/lib/utils';
import {
  fmtPrice,
  fmtLiveQuoteMeta,
  priceDecimals,
  confluencePct,
  confluenceThresholdPct,
  convictionTier,
  engineAThreshold,
  engineAScoreBreakdown,
  engineBScoreBreakdown,
  regimeLabel,
  sessionLabel,
} from '@/lib/athenaFormat';
import type { EngineASignal } from '@/types/athena';
import { isEngineAV3Signal, resolveEngineAV3Signal } from '@/lib/engineAV3';

interface Props {
  signal: EngineASignal;
  onExecute?: (sig: EngineASignal) => void;
  onSelect?: (sig: EngineASignal) => void;
  selected?: boolean;
  executeDisabled?: boolean;
  executeLabel?: string;
  compact?: boolean;
}

const ORTHO_LABELS: Record<string, string> = {
  carry: 'Carry',
  cot: 'COT',
  funding: 'Funding',
  oi_divergence: 'OI Div',
  vol_skew: 'Vol Skew',
  location: 'Location',
  volume: 'Volume',
  intermarket: 'Intermkt',
  sentiment: 'Sentiment',
  macro: 'Macro',
  microstructure: 'Micro',
};

function firstFiniteNumber(...values: unknown[]): number | undefined {
  for (const value of values) {
    if (value == null || value === '' || typeof value === 'boolean') continue;
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

export default function EngineASignalCard({
  signal,
  onExecute,
  onSelect,
  selected,
  executeDisabled,
  executeLabel,
  compact,
}: Props) {
  const raw = signal as Record<string, unknown>;
  if (isEngineAV3Signal(signal)) {
    return (
      <EngineAV3SignalCard
        signal={resolveEngineAV3Signal(signal)}
        onExecute={onExecute}
        onSelect={onSelect}
        selected={selected}
        executeDisabled={executeDisabled}
        executeLabel={executeLabel}
        compact={compact}
      />
    );
  }
  const engineSource = String(raw.engine_source ?? raw.engine ?? '').toUpperCase();
  const isNakedScan = Boolean(signal.is_naked);
  const isEngineBOnlyStub = engineSource === 'ENGINE_B' || (
    engineSource === 'B' && raw.engine_a_present === false
  );
  const isEngineBOnly = isEngineBOnlyStub || isNakedScan;
  const engineBResult = (
    signal.naked_data && typeof signal.naked_data === 'object'
      ? signal.naked_data
      : signal.engine_b && typeof signal.engine_b === 'object'
        ? signal.engine_b
        : {}
  ) as Record<string, unknown>;
  const engineBStatus = (
    raw.engine_b_status && typeof raw.engine_b_status === 'object'
      ? raw.engine_b_status
      : engineBResult.confidence && typeof engineBResult.confidence === 'object'
        ? engineBResult.confidence
        : {}
  ) as Record<string, unknown>;
  const engineABlockReason = String(raw.engine_a_block_reason ?? '').trim();
  const isLong  = signal.direction === 'LONG';
  const isShort = signal.direction === 'SHORT';
  const dirStyle = isLong
    ? { background: 'hsl(var(--long) / 0.18)', color: 'hsl(var(--long))' }
    : isShort
    ? { background: 'hsl(var(--short) / 0.18)', color: 'hsl(var(--short))' }
    : { background: 'hsl(var(--muted) / 0.40)', color: 'hsl(var(--muted-foreground))' };
  const scoreBreakdown = engineAScoreBreakdown(signal);
  const bBreakdown = isEngineBOnly
    ? engineBScoreBreakdown(raw)
    : null;
  // Headline % for Engine B is the earned quality layer. The two alternatives
  // are both near-constant for anything Engine B passes: gate_pct is 100 by
  // definition (every mandatory gate must pass to emit) and the total ratio
  // score/max_possible floors near 83% because gate_score == gate_max on pass.
  // Only quality_pct spans a usable range, so it leads and the others are
  // ordered strictly as fallbacks for older payloads.
  const nakedQualityPct = isEngineBOnly
    ? toNum(
        raw.engine_b_quality_pct
          ?? engineBStatus.quality_pct
          ?? engineBResult.quality_pct
          ?? raw.quality_pct,
        NaN,
      )
    : NaN;
  const nakedTotalPct = isEngineBOnly
    ? toNum(raw.engine_b_pct ?? engineBStatus.pct ?? engineBResult.pct, NaN)
    : NaN;
  const nakedGatePct = isEngineBOnly
    ? toNum(raw.engine_b_gate_pct ?? engineBStatus.gate_pct ?? engineBResult.gate_pct, NaN)
    : NaN;
  const conf = isEngineBOnly
    ? (Number.isFinite(nakedQualityPct)
        ? Math.max(0, Math.min(100, Math.round(nakedQualityPct)))
        : Number.isFinite(nakedTotalPct)
          ? Math.max(0, Math.min(100, Math.round(nakedTotalPct)))
          : bBreakdown?.totalScore != null && bBreakdown.totalMax
            ? Math.max(0, Math.min(100, Math.round((bBreakdown.totalScore / bBreakdown.totalMax) * 100)))
            : Number.isFinite(nakedGatePct)
              ? Math.max(0, Math.min(100, Math.round(nakedGatePct)))
              : null)
    : confluencePct(signal);
  const confLabel = isEngineBOnly && Number.isFinite(nakedQualityPct)
    ? 'quality'
    : undefined;
  const thresholdPct = isEngineBOnly ? null : confluenceThresholdPct(signal);
  const conv = toNum(signal.conviction, NaN);
  const convT = convictionTier(Number.isFinite(conv) ? conv : null);
  const score = scoreBreakdown?.displayScore ?? toNum(signal.confluenceScore ?? signal.score, NaN);
  const max = toNum(
    isEngineBOnlyStub ? raw.engine_a_maxScore ?? signal.maxScore : signal.maxScore,
    NaN,
  );
  const threshold = scoreBreakdown?.threshold ?? engineAThreshold(signal);
  const passed = isEngineBOnly && bBreakdown
    ? bBreakdown.confidencePasses
    : !isEngineBOnly && Number.isFinite(score) && threshold != null && (
      isEngineAV3Signal(signal) && scoreBreakdown?.hasAdjustments
        ? Boolean(scoreBreakdown.decisionPasses)
        : score >= threshold
    );
  const fs = ((
    (isEngineBOnlyStub && raw.engine_a_factorScores && typeof raw.engine_a_factorScores === 'object')
      ? raw.engine_a_factorScores
      : signal.factorScores
  ) || {}) as NonNullable<EngineASignal['factorScores']>;
  const orthoScores = (
    fs.ortho && typeof fs.ortho === 'object' && !Array.isArray(fs.ortho)
      ? fs.ortho
      : undefined
  );
  const hasOrthoScores = Boolean(orthoScores && Object.keys(orthoScores).length > 0);
  const fd = ((
    (isEngineBOnlyStub && raw.engine_a_factorDiagnostics && typeof raw.engine_a_factorDiagnostics === 'object')
      ? raw.engine_a_factorDiagnostics
      : signal.factorDiagnostics
  ) || {}) as NonNullable<EngineASignal['factorDiagnostics']>;
  const diAlign = toNum(fd.diAlignMult, NaN);
  const adxMult = toNum(fd.adxMultiplier, NaN);
  const dirRamp = toNum(fd.directionalRampMult, NaN);
  const trendCoherence = (fd.trendCoherence || {}) as Record<string, unknown>;
  const trendAbortError = String(trendCoherence.error || '').trim();
  const showDiChain =
    !trendAbortError
    && (Number.isFinite(diAlign) || Number.isFinite(adxMult) || Number.isFinite(dirRamp));
  const trendAbortNote = (() => {
    if (trendAbortError === 'weighted_tf_tie') {
      const d1 = trendCoherence.d1;
      const h4 = trendCoherence.h4;
      const h1 = trendCoherence.h1;
      const parts = [
        d1 ? `D1 ${d1}` : null,
        h4 ? `H4 ${h4}` : null,
        h1 ? `H1 ${h1}` : null,
      ].filter(Boolean);
      return parts.length
        ? `Multi-TF EMA vote tie (${parts.join(', ')}) — Engine A cannot pick LONG vs SHORT.`
        : 'Multi-TF EMA vote tie — Engine A cannot pick LONG vs SHORT.';
    }
    if (trendAbortError === 'no_ema_data') {
      return 'Missing EMA data on one or more timeframes.';
    }
    if (engineABlockReason.includes('indeterminate_trend')) {
      return 'Engine A trend vote did not produce a tradable direction.';
    }
    return null;
  })();
  const isResearchOnly = signal.engineATradeEnabled === false;
  const tradeGateReason = String(signal.engineATradeGate?.reason || '').trim();
  const pair = signal.display || signal.pair || signal.symbol || '—';
  const type = signal.type;
  const livePrice = toNum(signal.livePrice, NaN);
  const livePriceMeta = fmtLiveQuoteMeta(signal.livePriceAgeSec, signal.livePriceSource);
  const displayPrice = Number.isFinite(livePrice) ? livePrice : signal.entry ?? signal.price;
  const decimals = priceDecimals(pair, type);
  const intermarketEntries = intermarketConfirmationEntries(signal.intermarketConfirmation);
  const levelEntry = isEngineBOnly
    ? firstFiniteNumber(signal.entry, signal.price, engineBResult.current_price)
    : signal.entry ?? signal.price;
  const levelSl = isEngineBOnly
    ? firstFiniteNumber(raw.engine_b_execution_sl, engineBStatus.execution_sl, engineBResult.execution_sl, signal.sl)
    : signal.sl;
  const levelTp1 = isEngineBOnly
    ? firstFiniteNumber(raw.engine_b_execution_tp1, engineBStatus.execution_tp1, engineBResult.execution_tp1, signal.tp, signal.tp1)
    : signal.tp ?? signal.tp1;
  const levelTp2 = isEngineBOnly
    ? firstFiniteNumber(raw.engine_b_execution_tp2, engineBStatus.execution_tp2, engineBResult.execution_tp2, signal.tp2)
    : signal.tp2;
  const rr1 = isEngineBOnly
    ? firstFiniteNumber(raw.engine_b_execution_rr1, engineBStatus.execution_rr1, engineBResult.execution_rr1, signal.rr1)
    : firstFiniteNumber(signal.rr1, signal.rr);
  const rr2 = isEngineBOnly
    ? firstFiniteNumber(raw.engine_b_execution_rr2, engineBStatus.execution_rr2, engineBResult.execution_rr2, signal.rr2, signal.rr)
    : firstFiniteNumber(signal.rr2);
  const rrUsedForGate = isEngineBOnly
    ? firstFiniteNumber(raw.engine_b_rr_used_for_gate, engineBStatus.rr_used_for_gate, engineBResult.rr_used_for_gate, rr2)
    : undefined;
  const tp1MinRr = isEngineBOnly
    ? firstFiniteNumber(engineBStatus.tp1_min_rr, engineBResult.tp1_min_rr)
    : undefined;
  const runnerMinRr = isEngineBOnly
    ? firstFiniteNumber(raw.engine_b_rr_required, engineBStatus.rr_required, engineBResult.rr_required, signal.min_rr)
    : undefined;
  void displayPrice;

  return (
    <Card
      className={cn(
        'transition-all duration-200',
        isLong  ? 'signal-long-border'  : '',
        isShort ? 'signal-short-border' : '',
      )}
      style={selected
        ? { background: 'hsl(var(--card) / 0.70)', border: '1px solid hsl(var(--gold) / 0.45)', boxShadow: '0 0 12px hsl(var(--gold) / 0.18)' }
        : { background: 'hsl(var(--card) / 0.50)', border: '1px solid hsl(var(--border) / 0.60)' }
      }
      onClick={() => onSelect?.(signal)}
    >
      <CardContent className={compact ? 'p-3 space-y-2' : 'p-4 space-y-3'}>
        {/* Header */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm font-mono font-bold truncate">{pair}</span>
            {/* Direction badge — pill */}
            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={dirStyle}>
              {signal.direction || '—'}
            </span>
            {signal.signalClass && (
              <Badge variant="outline" className="text-[9px] uppercase">
                {String(signal.signalClass)}
              </Badge>
            )}
            {signal.style && (
              <Badge variant="outline" className="text-[9px]">
                {signal.style}
              </Badge>
            )}
            {isResearchOnly && (
              <Badge variant="outline" className="text-[9px] text-muted-foreground">
                Research-only
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <Badge variant="outline" className={cn('text-[10px] font-mono', convT.color)}>
              {convT.tier}
            </Badge>
            {Number.isFinite(conv) && (
              <span className="text-[10px] text-muted-foreground font-mono">{(conv * 100).toFixed(0)}%</span>
            )}
          </div>
        </div>

        <TimeframePolicyLine signal={signal} compact={compact} />

        {/* Score + threshold + bar */}
        <div className="space-y-1">
          <div className="flex items-center justify-between text-[10px] text-muted-foreground">
            <span>
              {/* Engine B leads with the quality layer: gate_score/gate_max is
                  5/5 for every executable row, so it belongs in the sub-line as
                  a pass badge rather than as the headline number. */}
              {isEngineBOnly ? 'Engine B quality' : 'Confluence'}{' '}
              <span className={cn('font-mono', passed ? 'text-long' : 'text-muted-foreground')}>
                {isEngineBOnly
                  ? bBreakdown?.bonusPoints != null && bBreakdown.totalMax != null && bBreakdown.gateMax != null
                    ? `${fmtNum(bBreakdown.bonusPoints, 2)}/${fmtNum(bBreakdown.totalMax - bBreakdown.gateMax, 2)}`
                    : conf != null ? `${conf.toFixed(0)}%` : '—'
                  : `${fmtNum(score, 2)}/${fmtNum(max, 2)}`}
              </span>
              {threshold != null && !isEngineBOnly ? (
                <span className="ml-1">≥ {fmtNum(threshold, 2)}</span>
              ) : null}
            </span>
            <span className="font-mono">
              {conf != null ? `${conf.toFixed(0)}%` : '—'}
              {confLabel ? <span className="ml-1 text-[9px] opacity-70">{confLabel}</span> : null}
            </span>
          </div>
          {isEngineBOnly && bBreakdown?.totalScore != null && (
            <p className="text-[9px] text-muted-foreground leading-snug">
              Gates{' '}
              <span className={cn('font-mono', passed ? 'text-long' : 'text-muted-foreground')}>
                {fmtNum(bBreakdown.gateScore, 2)}/{fmtNum(bBreakdown.gateMax ?? max, 2)}
              </span>
              {' · total '}
              <span className="font-mono text-foreground">
                {fmtNum(bBreakdown.totalScore, 2)}/{fmtNum(bBreakdown.totalMax ?? max, 2)}
              </span>
              {bBreakdown.minScore != null && (
                <span> ≥ {fmtNum(bBreakdown.minScore, 2)}</span>
              )}
              {bBreakdown.minScoreFloorBinding === false && (
                <span className="opacity-70"> (floor non-binding)</span>
              )}
              {bBreakdown.scoreFloorPasses && !bBreakdown.confidencePasses && (
                <span className="text-warning"> — score clears floor; one or more mandatory gates failed</span>
              )}
            </p>
          )}
          {!isEngineBOnly && scoreBreakdown?.hasAdjustments && (
            <EngineAScoreAdjustmentNote breakdown={scoreBreakdown} />
          )}
          {/* Violet gradient bar — glows when score passes threshold. The axis is
              score/max, so the threshold is drawn as a tick rather than by
              rescaling the axis (which made groups incomparable). */}
          <div className="relative w-full rounded-full h-2 overflow-hidden" style={{ background: 'hsl(var(--border) / 0.55)' }}>
            <div
              className={`h-full rounded-full transition-all duration-300 ${passed ? 'glow-gold-sm' : ''}`}
              style={{
                width: `${conf ?? 0}%`,
                background: passed
                  ? 'linear-gradient(90deg, hsl(var(--gold-dark)), hsl(var(--long)))'
                  : 'linear-gradient(90deg, hsl(var(--gold-dark)), hsl(var(--gold-light)))',
              }}
            />
            {thresholdPct != null && (
              <div
                className="absolute top-0 h-full"
                style={{
                  left: `${thresholdPct}%`,
                  width: '2px',
                  background: 'hsl(var(--foreground) / 0.65)',
                }}
                title={`Trade threshold ${fmtNum(threshold, 2)} of ${fmtNum(max, 2)}`}
              />
            )}
          </div>
          {isEngineBOnlyStub ? (
            <p className="text-[9px] text-muted-foreground leading-snug">
              Engine B-only watchlist. Engine A blocked: {engineABlockReason || 'no Engine A trade signal'}.
            </p>
          ) : isEngineBOnly ? (
            <p className="text-[9px] text-muted-foreground leading-snug">
              Engine B structure scan — confidence from Naked Engine gates (structure, location, trigger, RR).
            </p>
          ) : (
            <p className="text-[9px] text-muted-foreground leading-snug">
              Quality score (0–3.0): weighted multi-timeframe trend + momentum (RSI/MACD/DI·ADX),
              entry location and volatility regime, plus intermarket, carry, COT, microstructure and
              volume — each shown below. The bar fills to ~67% at this group&apos;s TRADE threshold.
            </p>
          )}
          {trendAbortNote && (
            <p className="text-[9px] text-muted-foreground leading-snug">{trendAbortNote}</p>
          )}
          {isResearchOnly && (
            <p className="text-[9px] text-muted-foreground leading-snug">
              Research-only: trend engine disabled for trade-ready status on this instrument.
              {tradeGateReason ? ` ${tradeGateReason}` : ''}
            </p>
          )}
          {showDiChain && (
            <p className="text-[9px] text-muted-foreground leading-snug">
              Score chain:{' '}
              {Number.isFinite(diAlign) && (
                <span className={diAlign <= 0.35 ? 'text-short font-medium' : 'text-foreground/80'}>
                  DI×{fmtNum(diAlign, 2)}
                </span>
              )}
              {Number.isFinite(adxMult) && (
                <span>{Number.isFinite(diAlign) ? ' · ' : ''}ADX×{fmtNum(adxMult, 2)}</span>
              )}
              {Number.isFinite(dirRamp) && (
                <span>{Number.isFinite(diAlign) || Number.isFinite(adxMult) ? ' · ' : ''}ramp×{fmtNum(dirRamp, 2)}</span>
              )}
              {Number.isFinite(diAlign) && diAlign <= 0.35 && (
                <span className="text-short"> — +DI/-DI opposes EMA trend (main forex suppressor)</span>
              )}
            </p>
          )}
          {!isEngineBOnly && signal.engine_b != null && (
            <p className="text-[9px] text-muted-foreground leading-snug border-t border-border/40 pt-1 mt-1">
              Engine B attached — see detail panel
            </p>
          )}
        </div>

        {/* Factor breakdown */}
        {(fs.trend != null || fs.momentum != null || fs.addon != null || hasOrthoScores) && (
          hasOrthoScores && orthoScores ? (
            <div className="grid grid-cols-2 gap-1 text-center">
              <FactorBox label="Trend" value={fs.trend} accent="long" />
              <FactorBox label="Momentum" value={fs.momentum} accent="primary" />
              {Object.entries(orthoScores).map(([name, v]) => (
                <FactorBox
                  key={name}
                  label={ORTHO_LABELS[name] ?? name}
                  value={v}
                  accent={v >= 0 ? 'long' : 'short'}
                />
              ))}
              <FactorBox label="Ortho Σ" value={fs.ortho_term} accent="warning" />
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-2 text-center">
              <FactorBox label="Trend" value={fs.trend} accent="long" />
              <FactorBox label="Momentum" value={fs.momentum} accent="primary" />
              <FactorBox label="Addon" value={fs.addon} accent="warning" />
            </div>
          )
        )}

        {!compact && intermarketEntries.length > 0 && (
          <div className="rounded-md border border-border/50 bg-muted/20 p-2 space-y-1">
            <p className="text-[10px] uppercase text-muted-foreground">Intermarket confirmation</p>
            <div className="grid grid-cols-2 gap-1">
              {intermarketEntries.map(([label, value]) => (
                <div key={label} className="text-[10px] min-w-0">
                  <span className="text-muted-foreground">{label}: </span>
                  <span className="font-mono text-foreground break-words">{value}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Levels */}
        <div className="grid grid-cols-4 gap-2">
          <Level
            label="Live"
            value={Number.isFinite(livePrice) ? livePrice : undefined}
            pair={pair}
            type={type}
            accent="primary"
            decimals={decimals}
            meta={livePriceMeta}
          />
          <Level label="Entry" value={levelEntry} pair={pair} type={type} accent="muted" decimals={decimals} />
          <Level label="SL" value={levelSl} pair={pair} type={type} accent="short" decimals={decimals} />
          <Level label={isEngineBOnly ? 'TP1' : 'TP'} value={levelTp1} pair={pair} type={type} accent="long" decimals={decimals} />
        </div>

        {(levelTp2 != null && Number.isFinite(Number(levelTp2))) && (
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <ArrowUpRight className="w-3 h-3 text-long" />
            TP2: <span className="font-mono text-foreground">{fmtPrice(levelTp2, pair, type)}</span>
            {rr1 != null && (
              <span className="ml-2">{isEngineBOnly ? 'TP1 R:R' : 'R:R'} {fmtNum(rr1, 2)}{tp1MinRr != null ? ` ≥ ${fmtNum(tp1MinRr, 2)}` : ''}</span>
            )}
            {rr2 != null && (
              <span className="ml-1">{isEngineBOnly ? 'Runner R:R' : 'TP2 R:R'} {fmtNum(rr2, 2)}{runnerMinRr != null ? ` ≥ ${fmtNum(runnerMinRr, 2)}` : ''}</span>
            )}
            {isEngineBOnly && rrUsedForGate != null && rrUsedForGate !== rr2 && (
              <span className="ml-1">Gate R:R {fmtNum(rrUsedForGate, 2)}</span>
            )}
          </div>
        )}

        {/* Context row */}
        {!compact && (
          <div className="flex items-center justify-between text-[10px] text-muted-foreground gap-2 flex-wrap">
            <span>Regime: <span className="text-foreground font-mono">{regimeLabel(signal.regime)}</span></span>
            <span>Session: <span className="text-foreground font-mono">{sessionLabel(signal.session)}</span></span>
            {signal.factorDiagnostics?.adxValue != null && (
              <span>ADX: <span className="text-foreground font-mono">{fmtNum(signal.factorDiagnostics.adxValue, 1)}</span></span>
            )}
            {signal.atr != null && (
              <span>ATR: <span className="text-foreground font-mono">{fmtPrice(signal.atr, pair, type)}</span></span>
            )}
          </div>
        )}

        {/* Warnings */}
        {!compact && signal.warnings && signal.warnings.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {signal.warnings.slice(0, 6).map((w) => (
              <Badge key={w} variant="outline" className="text-[9px] text-warning border-warning/40">
                {w}
              </Badge>
            ))}
          </div>
        )}

        {onExecute && (
          <Button
            size="sm"
            className="w-full gap-2"
            style={{
              background: 'linear-gradient(135deg, hsl(var(--gold-dark)), hsl(var(--gold)))',
              color: 'hsl(var(--primary-foreground))',
              border: 'none',
            }}
            onClick={(e) => {
              e.stopPropagation();
              onExecute(signal);
            }}
            disabled={executeDisabled}
          >
            <Play className="w-3.5 h-3.5" />
            {executeLabel || 'Execute'}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function EngineAV3SignalCard({
  signal,
  onExecute,
  onSelect,
  selected,
  executeDisabled,
  executeLabel,
  compact,
}: Props) {
  const pair = signal.display || signal.pair || signal.symbol || '-';
  const type = signal.type;
  const isLong = signal.direction === 'LONG';
  const isShort = signal.direction === 'SHORT';
  const decision = String(signal.decision || 'NO_SIGNAL');
  const entryZone = Array.isArray(signal.entryZone) ? signal.entryZone : [];
  const passedPredicates = (signal.predicates || []).filter((predicate) => predicate.passed);
  const decisionClass = decision === 'TRADE'
    ? 'text-long border-long/40'
    : decision === 'WATCH'
    ? 'text-warning border-warning/40'
    : 'text-muted-foreground border-border/60';
  const scoreBreakdown = engineAScoreBreakdown(signal);
  const score = scoreBreakdown?.decisionScore ?? toNum(signal.confluenceScore ?? signal.score, NaN);
  const max = toNum(signal.maxScore, NaN);
  const threshold = scoreBreakdown?.threshold ?? engineAThreshold(signal);
  const passed = Number.isFinite(score) && threshold != null && score >= threshold;
  const conf = confluencePct(signal);
  const thresholdPct = confluenceThresholdPct(signal);
  const conv = toNum(signal.conviction, NaN);
  const convT = convictionTier(Number.isFinite(conv) ? conv : null);
  const hasQuality = Number.isFinite(score) || conf != null || Number.isFinite(conv);
  const components = signal.componentScores || {};
  const showAdjustmentMismatch = (
    decision === 'WATCH'
    && scoreBreakdown?.displayPasses
    && !scoreBreakdown.decisionPasses
  );
  const showDecisionPassButWatch = (
    decision === 'WATCH'
    && scoreBreakdown?.decisionPasses
  );

  return (
    <Card
      className={cn(
        'transition-all duration-200',
        isLong ? 'signal-long-border' : '',
        isShort ? 'signal-short-border' : '',
      )}
      style={selected
        ? { background: 'hsl(var(--card) / 0.70)', border: '1px solid hsl(var(--gold) / 0.45)' }
        : { background: 'hsl(var(--card) / 0.50)', border: '1px solid hsl(var(--border) / 0.60)' }}
      onClick={() => onSelect?.(signal)}
    >
      <CardContent className={compact ? 'p-3 space-y-2' : 'p-4 space-y-3'}>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-mono font-bold">{pair}</span>
              <Badge variant="outline" className={cn('text-[10px]', decisionClass)}>{decision}</Badge>
              <Badge variant="outline" className="text-[10px]">{signal.horizon || '-'}</Badge>
              <Badge variant="outline" className={cn(
                'text-[10px]',
                signal.validationStatus === 'PROMOTED' ? 'text-long border-long/40' : 'text-warning border-warning/40',
              )}>{signal.validationStatus || 'UNAVAILABLE'}</Badge>
              <Badge variant="outline" className="text-[10px]">{signal.exitPolicy || 'SINGLE_TP1'}</Badge>
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground break-words">
              {signal.setupId || 'No qualified setup'} · {signal.family || '-'} / {signal.subclass || '-'}
            </p>
          </div>
          <Badge variant={isShort ? 'destructive' : 'secondary'}>{signal.direction || '-'}</Badge>
        </div>

        <TimeframePolicyLine signal={signal} compact={compact} />

        {hasQuality && (
          <div className="space-y-1">
            <div className="flex items-center justify-between text-[10px] text-muted-foreground">
              <span>
                Decision score{' '}
                <span className={cn('font-mono', passed ? 'text-long' : 'text-muted-foreground')}>
                  {fmtNum(score, 2)}/{fmtNum(max, 2)}
                </span>
                {threshold != null && <span className="ml-1">≥ {fmtNum(threshold, 2)}</span>}
              </span>
              <div className="flex items-center gap-1 shrink-0">
                <Badge variant="outline" className={cn('text-[10px] font-mono', convT.color)}>{convT.tier}</Badge>
                <span className="font-mono">{conf != null ? `${conf.toFixed(0)}%` : '—'}</span>
              </div>
            </div>
            {scoreBreakdown?.hasAdjustments && (
              <EngineAScoreAdjustmentNote breakdown={scoreBreakdown} />
            )}
            {showAdjustmentMismatch && (
              <p className="text-[9px] text-warning leading-snug">
                WATCH: adjusted score clears threshold, but decision used the pre-blend score.
              </p>
            )}
            {showDecisionPassButWatch && (
              <p className="text-[9px] text-warning leading-snug">
                WATCH: score clears threshold — awaiting setup confirmation or execution eligibility.
              </p>
            )}
            <div className="relative w-full rounded-full h-2 overflow-hidden" style={{ background: 'hsl(var(--border) / 0.55)' }}>
              <div
                className={`h-full rounded-full transition-all duration-300 ${passed ? 'glow-gold-sm' : ''}`}
                style={{
                  width: `${conf ?? 0}%`,
                  background: passed
                    ? 'linear-gradient(90deg, hsl(var(--gold-dark)), hsl(var(--long)))'
                    : 'linear-gradient(90deg, hsl(var(--gold-dark)), hsl(var(--gold-light)))',
                }}
              />
              {thresholdPct != null && (
                <div
                  className="absolute top-0 h-full"
                  style={{
                    left: `${thresholdPct}%`,
                    width: '2px',
                    background: 'hsl(var(--foreground) / 0.65)',
                  }}
                  title={`Trade threshold ${fmtNum(threshold, 2)} of ${fmtNum(max, 2)}`}
                />
              )}
            </div>
            {!compact && (
              <p className="text-[9px] text-muted-foreground leading-snug">
                Bar uses the decision-time score (before intermarket/news blends). Tier follows the V3
                {` ${decision}`} badge, not the adjusted display score.
              </p>
            )}
          </div>
        )}

        <div className="grid grid-cols-4 gap-2">
          <Level label="Entry" value={signal.price} pair={pair} type={type} accent="muted" />
          <Level label="Invalidation" value={signal.invalidation ?? signal.sl} pair={pair} type={type} accent="short" />
          <Level label="TP1" value={signal.tp1} pair={pair} type={type} accent="long" />
          <Level label="TP2" value={signal.tp2} pair={pair} type={type} accent="long" />
        </div>

        {entryZone.length >= 2 && (
          <p className="text-[10px] text-muted-foreground">
            Trigger zone: <span className="font-mono text-foreground">
              {fmtPrice(entryZone[0], pair, type)} - {fmtPrice(entryZone[1], pair, type)}
            </span>
          </p>
        )}

        {!compact && Object.keys(components).length > 0 && (
          <div className="grid grid-cols-2 gap-1">
            {Object.entries(components).map(([name, component]) => (
              <div key={name} className="rounded border border-border/50 bg-muted/20 p-1.5 text-[10px]">
                <div className="flex justify-between"><span className="capitalize">{name}</span><span className="font-mono">{component.available === false ? 'N/A' : fmtNum(component.contribution, 3)}</span></div>
                <div className="text-muted-foreground font-mono">s {fmtNum(component.signal, 2)} · q {fmtNum(component.quality, 2)} · w {fmtNum(component.weight, 2)}</div>
              </div>
            ))}
          </div>
        )}

        {!compact && (
          <div className="rounded-md border border-border/50 bg-muted/20 p-2 space-y-1">
            <p className="text-[10px] uppercase text-muted-foreground">
              Component diagnostics {passedPredicates.length}/{signal.predicates?.length || 0} aligned
            </p>
            {(signal.predicates || []).slice(0, 8).map((predicate) => (
              <div key={predicate.name} className="flex items-start justify-between gap-2 text-[10px]">
                <span className={predicate.passed ? 'text-long' : 'text-warning'}>
                  {predicate.passed ? 'PASS' : 'FAIL'} {predicate.name}
                </span>
                <span className="text-right text-muted-foreground">{predicate.expected}</span>
              </div>
            ))}
          </div>
        )}

        {(signal.rejectionReasons || []).length > 0 && (
          <div className="flex flex-wrap gap-1">
            {signal.rejectionReasons?.map((reason) => (
              <Badge key={reason} variant="outline" className="text-[9px] text-warning border-warning/40">
                {reason}
              </Badge>
            ))}
          </div>
        )}

        {!compact && signal.validationArtifact && (
          <p className="text-[10px] text-muted-foreground break-all">
            Validation: <span className="font-mono text-foreground">
              {signal.validationArtifact.artifactId} · {signal.validationArtifact.status}
            </span>
            {signal.scoringProfile?.profileSha256 && (
              <span className="block font-mono text-foreground">
                Profile {signal.scoringProfile.profileId} · {signal.scoringProfile.profileSha256.slice(0, 12)}
              </span>
            )}
          </p>
        )}

        {onExecute && (
          <Button
            size="sm"
            className="w-full gap-2"
            onClick={(event) => {
              event.stopPropagation();
              onExecute(signal);
            }}
            disabled={
              executeDisabled
              || decision !== 'TRADE'
              || signal.qualified !== true
              || signal.engineATradeEnabled !== true
            }
          >
            <Play className="w-3.5 h-3.5" />
            {executeLabel || 'Execute demo'}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function TimeframePolicyLine({ signal, compact }: { signal: EngineASignal; compact?: boolean }) {
  if (!signal.timeframePolicyVersion) return null;
  const roles = [
    signal.regimeTf ? `${signal.regimeTf} regime` : null,
    signal.biasTf ? `${signal.biasTf} bias` : null,
    signal.structureTf ? `${signal.structureTf} structure` : null,
    signal.setupTf ? `${signal.setupTf} setup` : null,
    signal.triggerTf ? `${signal.triggerTf} trigger` : null,
    signal.atrTimeframe ? `${signal.atrTimeframe} ATR` : null,
    signal.executionTf ? `${signal.executionTf} execution` : null,
  ].filter(Boolean).join(' · ');
  const speed = signal.liveSpeedClass || signal.baselineSpeedClass || 'UNKNOWN';
  const profile = signal.timeframeProfile || 'SAFE_FALLBACK';
  return (
    <div className="rounded border border-border/50 bg-muted/20 px-2 py-1 text-[10px] text-muted-foreground">
      <p className="font-mono text-foreground">{roles}</p>
      {!compact && (
        <p>
          Speed {speed} · baseline {profile}
          {signal.entryReadiness && (
            <span className={signal.entryReadiness === 'READY' ? 'text-long' : 'text-warning'}>
              {' '}· entry {signal.entryReadiness}
            </span>
          )}
        </p>
      )}
      {!compact && signal.policyScore != null && (
        <p>
          Policy score: <span className="font-mono text-foreground">{fmtNum(signal.policyScore, 2)}</span>
          {' '}· Legacy score: <span className="font-mono text-foreground">{fmtNum(signal.legacyScore, 2)}</span>
          {' '}· Authoritative: <span className="font-mono text-foreground">{signal.authoritativeScoreSource || 'LEGACY'}</span>
        </p>
      )}
    </div>
  );
}

function EngineAScoreAdjustmentNote({
  breakdown,
}: {
  breakdown: NonNullable<ReturnType<typeof engineAScoreBreakdown>>;
}) {
  if (!breakdown.hasAdjustments || breakdown.displayScore == null) return null;
  const parts: string[] = [];
  if (Math.abs(breakdown.intermarketDelta) > 0.0001) {
    parts.push(`intermarket ${breakdown.intermarketDelta >= 0 ? '+' : ''}${fmtNum(breakdown.intermarketDelta, 2)}`);
  }
  if (Math.abs(breakdown.newsAdjustment) > 0.0001) {
    parts.push(`news ${breakdown.newsAdjustment >= 0 ? '+' : ''}${fmtNum(breakdown.newsAdjustment, 2)}`);
  }
  return (
    <p className="text-[9px] text-muted-foreground leading-snug">
      Adjusted display{' '}
      <span className="font-mono text-foreground">{fmtNum(breakdown.displayScore, 2)}</span>
      {parts.length > 0 && (
        <span> ({parts.join(', ')})</span>
      )}
    </p>
  );
}

function FactorBox({
  label,
  value,
  accent,
}: {
  label: string;
  value: number | undefined;
  accent: 'long' | 'short' | 'primary' | 'warning';
}) {
  const fg =
    accent === 'long' ? 'text-long'
    : accent === 'short' ? 'text-short'
    : accent === 'warning' ? 'text-warning'
    : 'text-primary';
  const bg =
    accent === 'long' ? 'hsl(var(--long) / 0.10)'
    : accent === 'short' ? 'hsl(var(--short) / 0.10)'
    : accent === 'warning' ? 'hsl(var(--warning) / 0.10)'
    : 'hsl(var(--gold) / 0.10)';
  return (
    <div className="p-2 rounded-md" style={{ background: bg }}>
      <p className="text-[10px] text-muted-foreground uppercase">{label}</p>
      <p className={cn('text-xs font-mono font-bold', fg)}>
        {fmtNum(value, 2)}
      </p>
    </div>
  );
}

function intermarketConfirmationEntries(value: unknown): Array<[string, string]> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>)
    .filter(([, v]) => v != null && v !== '')
    .slice(0, 6)
    .map(([k, v]) => [k, formatIntermarketValue(v)]);
}

function formatIntermarketValue(value: unknown): string {
  if (typeof value === 'number') return fmtNum(value, 2);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(formatIntermarketValue).join(', ');
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .slice(0, 3)
      .map(([k, v]) => `${k}:${formatIntermarketValue(v)}`)
      .join(' ');
  }
  return String(value ?? '');
}

function Level({
  label,
  value,
  pair,
  type,
  accent,
  decimals,
  meta,
}: {
  label: string;
  value: unknown;
  pair?: string;
  type?: string;
  accent: 'muted' | 'long' | 'short' | 'primary';
  decimals?: number;
  meta?: string;
}) {
  const bgStyle = accent === 'long'
    ? 'hsl(var(--long) / 0.10)'
    : accent === 'short'
    ? 'hsl(var(--short) / 0.10)'
    : accent === 'primary'
    ? 'hsl(var(--gold) / 0.10)'
    : 'hsl(var(--muted) / 0.30)';
  const fg = accent === 'long' ? 'text-long' : accent === 'short' ? 'text-short' : accent === 'primary' ? 'text-primary' : 'text-foreground';
  return (
    <div className="p-2 rounded-md" style={{ background: bgStyle }}>
      <p className={cn('text-[10px] uppercase', accent === 'muted' ? 'text-muted-foreground' : fg)}>{label}</p>
      <p className={cn('text-xs font-mono font-bold', fg)}>
        {decimals != null ? fmtNum(value, decimals) : fmtPrice(value, pair, type)}
      </p>
      {meta && <p className="text-[9px] font-mono text-muted-foreground truncate">{meta}</p>}
    </div>
  );
}
