import { Button } from '@/components/ui/button';
import { Play } from 'lucide-react';
import { cn, fmtNum, toNum } from '@/lib/utils';
import { Chip, Details, DirectionChip, KeyValue, Metric, ScoreLine } from '@/components/shared/primitives';
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
import { resolveTrendCoherenceTimeframeRows } from '@/lib/engineADiagnosticsDisplay';

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

/** Card shell shared by both card variants: hairline border, direction edge. */
function CardShell({
  isLong,
  isShort,
  selected,
  onClick,
  children,
}: {
  isLong: boolean;
  isShort: boolean;
  selected?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        'surface-raised animate-fade-up h-full transition-all duration-200',
        selected
          ? 'border-primary/50 shadow-glow-accent'
          : 'hover:-translate-y-0.5 hover:border-primary/25 hover:shadow-elev-2',
        onClick && 'cursor-pointer',
        isLong && 'signal-long-border',
        isShort && 'signal-short-border',
      )}
      onClick={onClick}
    >
      {children}
    </div>
  );
}

/** Stops a disclosure toggle inside a selectable card from also selecting it. */
function stopSelect(e: React.MouseEvent) {
  e.stopPropagation();
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
  // Resolve the Engine B headline number and its caption TOGETHER, so the label
  // can never describe a different quantity than the one on screen.
  //
  // The fallbacks are not interchangeable with quality: `pct` floors near 83%
  // (gate_score == gate_max for every passing signal) and `gate_pct` is 100 by
  // definition. Presenting either under a "quality" caption would render a
  // constant as if it were the earned, discriminating layer — so each fallback
  // is labelled for what it actually is, and quality is shown as "—" when the
  // backend did not compute it (e.g. a zero quality denominator).
  const engineBHeadline = (() => {
    const clamp = (n: number) => Math.max(0, Math.min(100, Math.round(n)));
    if (Number.isFinite(nakedQualityPct)) {
      return { pct: clamp(nakedQualityPct), caption: 'Engine B quality', isQuality: true };
    }
    if (Number.isFinite(nakedTotalPct)) {
      return { pct: clamp(nakedTotalPct), caption: 'Engine B total', isQuality: false };
    }
    if (bBreakdown?.totalScore != null && bBreakdown.totalMax) {
      return {
        pct: clamp((bBreakdown.totalScore / bBreakdown.totalMax) * 100),
        caption: 'Engine B total',
        isQuality: false,
      };
    }
    if (Number.isFinite(nakedGatePct)) {
      return { pct: clamp(nakedGatePct), caption: 'Engine B gates', isQuality: false };
    }
    return { pct: null as number | null, caption: 'Engine B quality', isQuality: true };
  })();
  const conf = isEngineBOnly ? engineBHeadline.pct : confluencePct(signal);
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
      const parts = resolveTrendCoherenceTimeframeRows(fd)
        .map(({ timeframe, direction }) => `${timeframe} ${direction}`);
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

  const hasFactorGrid = fs.trend != null || fs.momentum != null || fs.addon != null || hasOrthoScores;
  const hasWarnings = Boolean(signal.warnings && signal.warnings.length > 0);

  return (
    <CardShell
      isLong={isLong}
      isShort={isShort}
      selected={selected}
      onClick={onSelect ? () => onSelect(signal) : undefined}
    >
      {/* ── Identity ── */}
      <div className="flex items-center justify-between gap-2 border-b border-border/70 px-3.5 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="font-display truncate text-[15px] font-semibold tracking-tight">{pair}</span>
          <DirectionChip direction={signal.direction} />
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {signal.style && <Chip>{signal.style}</Chip>}
          <Chip
            tone={convT.tier === 'HIGH' ? 'accent' : 'default'}
            title="Conviction tier"
          >
            {convT.tier}
            {Number.isFinite(conv) ? ` ${(conv * 100).toFixed(0)}%` : ''}
          </Chip>
        </div>
      </div>

      <div className="space-y-3 p-3">
        {/* ── Headline score ── */}
        <ScoreLine
          caption={isEngineBOnly ? engineBHeadline.caption : 'Confluence'}
          fraction={
            isEngineBOnly
              // Only show a fraction when it is the exact ratio the displayed
              // percent came from. For quality that is net-of-penalty points
              // over the weight denominator; bonus_points is pre-penalty and
              // would print e.g. "0.34" beside "9%" on a penalised signal. For
              // the total/gate fallbacks the percent is already the whole story.
              ? engineBHeadline.isQuality
                && bBreakdown?.qualityPointsNet != null
                && bBreakdown.qualityDenominator
                ? `${fmtNum(bBreakdown.qualityPointsNet, 2)}/${fmtNum(bBreakdown.qualityDenominator, 2)}`
                : undefined
              : `${fmtNum(score, 2)}/${fmtNum(max, 2)}`
          }
          pct={conf}
          thresholdPct={thresholdPct}
          passed={passed}
          thresholdTitle={
            threshold != null ? `Trade threshold ${fmtNum(threshold, 2)} of ${fmtNum(max, 2)}` : undefined
          }
        />

        {/* ── Levels: the numbers actually needed to take the trade ── */}
        <div className="grid grid-cols-4 gap-3">
          <Metric
            label="Live"
            value={Number.isFinite(livePrice) ? fmtNum(livePrice, decimals) : '—'}
            tone="accent"
            meta={compact ? undefined : livePriceMeta}
          />
          <Metric label="Entry" value={fmtNum(levelEntry, decimals)} />
          <Metric label="SL" value={fmtNum(levelSl, decimals)} tone="short" />
          <Metric
            label={isEngineBOnly ? 'TP1' : 'TP'}
            value={fmtNum(levelTp1, decimals)}
            tone="long"
          />
        </div>

        {/* ── Risk/reward strip ── */}
        {(rr1 != null || rr2 != null || levelTp2 != null) && (
          <div className="flex flex-wrap items-center gap-1.5">
            {rr1 != null && (
              <Chip title={isEngineBOnly ? 'TP1 risk/reward' : 'Risk/reward'}>
                {isEngineBOnly ? 'TP1 R:R' : 'R:R'} {fmtNum(rr1, 2)}
                {tp1MinRr != null ? ` ≥ ${fmtNum(tp1MinRr, 2)}` : ''}
              </Chip>
            )}
            {rr2 != null && (
              <Chip title={isEngineBOnly ? 'Runner risk/reward' : 'TP2 risk/reward'}>
                {isEngineBOnly ? 'Runner R:R' : 'TP2 R:R'} {fmtNum(rr2, 2)}
                {runnerMinRr != null ? ` ≥ ${fmtNum(runnerMinRr, 2)}` : ''}
              </Chip>
            )}
            {levelTp2 != null && Number.isFinite(Number(levelTp2)) && (
              <Chip title="Second take-profit">TP2 {fmtPrice(levelTp2, pair, type)}</Chip>
            )}
            {isEngineBOnly && rrUsedForGate != null && rrUsedForGate !== rr2 && (
              <Chip title="R:R value the gate actually used">Gate R:R {fmtNum(rrUsedForGate, 2)}</Chip>
            )}
          </div>
        )}

        {/* Quality was not computed for this payload — say so, rather than let a
            saturated fallback read as an earned score. */}
        {isEngineBOnly && !engineBHeadline.isQuality && (
          <p className="note">
            Quality layer unavailable on this payload — showing the{' '}
            {engineBHeadline.caption.replace('Engine B ', '')} figure, which is
            near-constant for any passing signal and cannot be used to rank.
          </p>
        )}

        {/* ── Blocking state stays on the surface: it changes what you do ── */}
        {isResearchOnly && (
          <p className="note">
            Research-only — trend engine disabled for trade-ready status on this instrument.
            {tradeGateReason ? ` ${tradeGateReason}` : ''}
          </p>
        )}
        {trendAbortNote && <p className="note text-warning">{trendAbortNote}</p>}
        {isEngineBOnlyStub && (
          <p className="note">
            Engine B-only watchlist. Engine A blocked: {engineABlockReason || 'no Engine A trade signal'}.
          </p>
        )}
        {isEngineBOnly && bBreakdown?.scoreFloorPasses && !bBreakdown.confidencePasses && (
          <p className="note text-warning">
            Score clears the floor, but one or more mandatory gates failed.
          </p>
        )}

        {/* ══ Everything diagnostic lives here — present, not deleted ══ */}
        <Details summary="Details" className="border-t border-border pt-0.5" >
          <div onClick={stopSelect} className="space-y-3">
            <TimeframePolicyBlock signal={signal} />

            {/* Score composition */}
            <div className="space-y-0.5">
              {isEngineBOnly && bBreakdown?.totalScore != null ? (
                <>
                  <KeyValue
                    label="Gates"
                    value={`${fmtNum(bBreakdown.gateScore, 2)} / ${fmtNum(bBreakdown.gateMax ?? max, 2)}`}
                    tone={passed ? 'long' : 'muted'}
                  />
                  <KeyValue
                    label="Total"
                    value={`${fmtNum(bBreakdown.totalScore, 2)} / ${fmtNum(bBreakdown.totalMax ?? max, 2)}`}
                  />
                  {bBreakdown.minScore != null && (
                    <KeyValue
                      label="Min score"
                      value={fmtNum(bBreakdown.minScore, 2)}
                      meta={bBreakdown.minScoreFloorBinding === false ? 'floor non-binding' : undefined}
                    />
                  )}
                </>
              ) : (
                <>
                  <KeyValue label="Score" value={`${fmtNum(score, 2)} / ${fmtNum(max, 2)}`} />
                  {threshold != null && (
                    <KeyValue label="Trade threshold" value={`≥ ${fmtNum(threshold, 2)}`} />
                  )}
                </>
              )}
              {!isEngineBOnly && scoreBreakdown?.hasAdjustments && (
                <EngineAScoreAdjustmentRows breakdown={scoreBreakdown} />
              )}
            </div>

            {/* Factor breakdown */}
            {hasFactorGrid && (
              <div>
                <p className="label mb-1.5">Factors</p>
                {hasOrthoScores && orthoScores ? (
                  <div className="grid grid-cols-3 gap-x-3 gap-y-1.5">
                    <FactorCell label="Trend" value={fs.trend} />
                    <FactorCell label="Momentum" value={fs.momentum} />
                    {Object.entries(orthoScores).map(([name, v]) => (
                      <FactorCell key={name} label={ORTHO_LABELS[name] ?? name} value={v} signed />
                    ))}
                    <FactorCell label="Ortho Σ" value={fs.ortho_term} signed />
                  </div>
                ) : (
                  <div className="grid grid-cols-3 gap-3">
                    <FactorCell label="Trend" value={fs.trend} />
                    <FactorCell label="Momentum" value={fs.momentum} />
                    <FactorCell label="Addon" value={fs.addon} />
                  </div>
                )}
              </div>
            )}

            {/* Multiplier chain */}
            {showDiChain && (
              <div>
                <p className="label mb-1">Score chain</p>
                <div className="flex flex-wrap gap-1.5">
                  {Number.isFinite(diAlign) && (
                    <Chip
                      tone={diAlign <= 0.35 ? 'short' : 'default'}
                      title={diAlign <= 0.35 ? '+DI/-DI opposes EMA trend — the main forex suppressor' : undefined}
                    >
                      DI ×{fmtNum(diAlign, 2)}
                    </Chip>
                  )}
                  {Number.isFinite(adxMult) && <Chip>ADX ×{fmtNum(adxMult, 2)}</Chip>}
                  {Number.isFinite(dirRamp) && <Chip>Ramp ×{fmtNum(dirRamp, 2)}</Chip>}
                </div>
              </div>
            )}

            {/* Context */}
            <div className="grid grid-cols-2 gap-x-4">
              <KeyValue label="Regime" value={regimeLabel(signal.regime)} />
              <KeyValue label="Session" value={sessionLabel(signal.session)} />
              {signal.factorDiagnostics?.adxValue != null && (
                <KeyValue label="ADX" value={fmtNum(signal.factorDiagnostics.adxValue, 1)} />
              )}
              {signal.atr != null && (
                <KeyValue label="ATR" value={fmtPrice(signal.atr, pair, type)} />
              )}
              {signal.signalClass && (
                <KeyValue label="Class" value={String(signal.signalClass)} />
              )}
            </div>

            {/* Intermarket */}
            {intermarketEntries.length > 0 && (
              <div>
                <p className="label mb-1">Intermarket confirmation</p>
                <div className="grid grid-cols-2 gap-x-4">
                  {intermarketEntries.map(([label, value]) => (
                    <KeyValue key={label} label={label} value={value} />
                  ))}
                </div>
              </div>
            )}

            {hasWarnings && (
              <div>
                <p className="label mb-1">Warnings</p>
                <div className="flex flex-wrap gap-1.5">
                  {signal.warnings!.slice(0, 6).map(w => (
                    <Chip key={w} tone="warning">{w}</Chip>
                  ))}
                </div>
              </div>
            )}

            {!isEngineBOnly && signal.engine_b != null && (
              <p className="note">Engine B attached — see the Engine B tab.</p>
            )}

            <p className="note">
              {isEngineBOnly
                ? 'Engine B structure scan — confidence from Naked Engine gates (structure, location, trigger, RR).'
                : 'Quality score (0–3.0): weighted multi-timeframe trend and momentum, entry location and volatility regime, plus intermarket, carry, COT, microstructure and volume. The meter tick marks this group’s TRADE threshold.'}
            </p>
          </div>
        </Details>

        {onExecute && (
          <Button
            size="sm"
            className="w-full gap-2"
            onClick={(e) => {
              e.stopPropagation();
              onExecute(signal);
            }}
            disabled={executeDisabled}
          >
            <Play className="h-3.5 w-3.5" />
            {executeLabel || 'Execute'}
          </Button>
        )}
      </div>
    </CardShell>
  );
}

function EngineAV3SignalCard({
  signal,
  onExecute,
  onSelect,
  selected,
  executeDisabled,
  executeLabel,
}: Props) {
  const pair = signal.display || signal.pair || signal.symbol || '-';
  const type = signal.type;
  const isLong = signal.direction === 'LONG';
  const isShort = signal.direction === 'SHORT';
  const decision = String(signal.decision || 'NO_SIGNAL');
  const entryZone = Array.isArray(signal.entryZone) ? signal.entryZone : [];
  const passedPredicates = (signal.predicates || []).filter((predicate) => predicate.passed);
  const decisionTone = decision === 'TRADE' ? 'long' : decision === 'WATCH' ? 'warning' : 'default';
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
    <CardShell
      isLong={isLong}
      isShort={isShort}
      selected={selected}
      onClick={onSelect ? () => onSelect(signal) : undefined}
    >
      {/* ── Identity ── */}
      <div className="flex items-center justify-between gap-2 border-b border-border/70 px-3.5 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="font-display truncate text-[15px] font-semibold tracking-tight">{pair}</span>
          <DirectionChip direction={signal.direction} />
          <Chip tone={decisionTone as 'long' | 'warning' | 'default'}>{decision}</Chip>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {signal.horizon && <Chip>{signal.horizon}</Chip>}
          <Chip tone={convT.tier === 'HIGH' ? 'accent' : 'default'} title="Conviction tier">
            {convT.tier}
          </Chip>
        </div>
      </div>

      <div className="space-y-3 p-3">
        {hasQuality && (
          <ScoreLine
            caption="Decision score"
            fraction={`${fmtNum(score, 2)}/${fmtNum(max, 2)}`}
            pct={conf}
            thresholdPct={thresholdPct}
            passed={passed}
            thresholdTitle={
              threshold != null ? `Trade threshold ${fmtNum(threshold, 2)} of ${fmtNum(max, 2)}` : undefined
            }
          />
        )}

        <div className="grid grid-cols-4 gap-3">
          <Metric label="Entry" value={fmtPrice(signal.price, pair, type)} />
          <Metric label="Invalidation" value={fmtPrice(signal.invalidation ?? signal.sl, pair, type)} tone="short" />
          <Metric label="TP1" value={fmtPrice(signal.tp1, pair, type)} tone="long" />
          <Metric label="TP2" value={fmtPrice(signal.tp2, pair, type)} tone="long" />
        </div>

        {/* State that changes the decision stays uncollapsed */}
        {showAdjustmentMismatch && (
          <p className="note text-warning">
            WATCH: adjusted score clears threshold, but the decision used the pre-blend score.
          </p>
        )}
        {showDecisionPassButWatch && (
          <p className="note text-warning">
            WATCH: score clears threshold — awaiting setup confirmation or execution eligibility.
          </p>
        )}
        {(signal.rejectionReasons || []).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {signal.rejectionReasons?.map((reason) => (
              <Chip key={reason} tone="warning">{reason}</Chip>
            ))}
          </div>
        )}

        <Details summary="Details" className="border-t border-border pt-0.5">
          <div onClick={stopSelect} className="space-y-3">
            <TimeframePolicyBlock signal={signal} />

            <div className="space-y-0.5">
              <KeyValue label="Setup" value={signal.setupId || 'No qualified setup'} />
              <KeyValue label="Family / subclass" value={`${signal.family || '-'} / ${signal.subclass || '-'}`} />
              <KeyValue label="Validation" value={signal.validationStatus || 'UNAVAILABLE'} />
              <KeyValue label="Exit policy" value={signal.exitPolicy || 'SINGLE_TP1'} />
              {entryZone.length >= 2 && (
                <KeyValue
                  label="Trigger zone"
                  value={`${fmtPrice(entryZone[0], pair, type)} – ${fmtPrice(entryZone[1], pair, type)}`}
                />
              )}
              {scoreBreakdown?.hasAdjustments && (
                <EngineAScoreAdjustmentRows breakdown={scoreBreakdown} />
              )}
            </div>

            {Object.keys(components).length > 0 && (
              <div>
                <p className="label mb-1.5">Component scores</p>
                <div className="space-y-0.5">
                  {Object.entries(components).map(([name, component]) => (
                    <KeyValue
                      key={name}
                      label={name}
                      value={component.available === false ? 'N/A' : fmtNum(component.contribution, 3)}
                      meta={`s ${fmtNum(component.signal, 2)} · q ${fmtNum(component.quality, 2)} · w ${fmtNum(component.weight, 2)}`}
                    />
                  ))}
                </div>
              </div>
            )}

            {(signal.predicates || []).length > 0 && (
              <div>
                <p className="label mb-1.5">
                  Predicates — {passedPredicates.length}/{signal.predicates?.length || 0} aligned
                </p>
                <div className="space-y-0.5">
                  {(signal.predicates || []).slice(0, 8).map((predicate) => (
                    <KeyValue
                      key={predicate.name}
                      label={predicate.name}
                      value={predicate.passed ? 'PASS' : 'FAIL'}
                      tone={predicate.passed ? 'long' : 'warning'}
                      meta={predicate.expected}
                    />
                  ))}
                </div>
              </div>
            )}

            {signal.validationArtifact && (
              <div className="space-y-0.5">
                <KeyValue
                  label="Artifact"
                  value={signal.validationArtifact.artifactId}
                  meta={signal.validationArtifact.status}
                />
                {signal.scoringProfile?.profileSha256 && (
                  <KeyValue
                    label="Profile"
                    value={signal.scoringProfile.profileId}
                    meta={signal.scoringProfile.profileSha256.slice(0, 12)}
                  />
                )}
              </div>
            )}

            <p className="note">
              The meter uses the decision-time score (before intermarket/news blends). The tier
              follows the V3 {decision} badge, not the adjusted display score.
            </p>
          </div>
        </Details>

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
            <Play className="h-3.5 w-3.5" />
            {executeLabel || 'Execute demo'}
          </Button>
        )}
      </div>
    </CardShell>
  );
}

/** Timeframe ladder + policy provenance. Always inside a disclosure. */
function TimeframePolicyBlock({ signal }: { signal: EngineASignal }) {
  if (!signal.timeframePolicyVersion) return null;
  const roles = [
    signal.regimeTf ? `${signal.regimeTf} regime` : null,
    signal.biasTf ? `${signal.biasTf} bias` : null,
    signal.structureTf ? `${signal.structureTf} structure` : null,
    signal.setupTf ? `${signal.setupTf} setup` : null,
    signal.triggerTf ? `${signal.triggerTf} trigger` : null,
    signal.atrTimeframe ? `${signal.atrTimeframe} ATR` : null,
    signal.executionTf ? `${signal.executionTf} execution` : null,
  ].filter(Boolean) as string[];
  if (roles.length === 0) return null;
  const speed = signal.liveSpeedClass || signal.baselineSpeedClass || 'UNKNOWN';
  const profile = signal.timeframeProfile || 'SAFE_FALLBACK';
  return (
    <div>
      <p className="label mb-1">Timeframes</p>
      <div className="flex flex-wrap gap-1.5">
        {roles.map(r => <Chip key={r}>{r}</Chip>)}
      </div>
      <div className="mt-1.5 space-y-0.5">
        <KeyValue label="Speed / baseline" value={`${speed} · ${profile}`} />
        {signal.entryReadiness && (
          <KeyValue
            label="Entry readiness"
            value={signal.entryReadiness}
            tone={signal.entryReadiness === 'READY' ? 'long' : 'warning'}
          />
        )}
        {signal.policyScore != null && (
          <KeyValue
            label="Policy / legacy score"
            value={`${fmtNum(signal.policyScore, 2)} · ${fmtNum(signal.legacyScore, 2)}`}
            meta={`authoritative: ${signal.authoritativeScoreSource || 'LEGACY'}`}
          />
        )}
      </div>
    </div>
  );
}

function EngineAScoreAdjustmentRows({
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
    <KeyValue
      label="Adjusted display"
      value={fmtNum(breakdown.displayScore, 2)}
      meta={parts.length > 0 ? parts.join(', ') : undefined}
    />
  );
}

/**
 * One factor contribution. Signed factors (the ortho block) colour by sign
 * because the sign is the information; unsigned magnitudes stay neutral ink.
 */
function FactorCell({
  label,
  value,
  signed,
}: {
  label: string;
  value: number | undefined;
  signed?: boolean;
}) {
  const n = toNum(value, NaN);
  const tone = signed && Number.isFinite(n)
    ? (n >= 0 ? 'long' : 'short')
    : 'default';
  return <Metric label={label} value={fmtNum(value, 2)} tone={tone} />;
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
