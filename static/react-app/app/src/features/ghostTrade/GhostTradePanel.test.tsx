import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import { GhostTradeView } from '@/components/panels/GhostTradePanel';
import { bannerForHealth, canShowExecute, groupSignals, sortSignals } from './display';
import type { GhostGroup, GhostHealth, GhostPerformance, GhostPosition, GhostSignal } from './types';


const health: GhostHealth = {
  enabled: true,
  mode: 'SHADOW',
  executionStatus: 'SHADOW ONLY',
  liveTradingAllowed: false,
  demoAutoEnabled: false,
  scanRunning: false,
};

const signal: GhostSignal = {
  signalId: 'signal-1',
  signalVersion: 'ghost-v1',
  scanId: 'scan-1',
  instrument: {
    source: 'MT5', brokerSymbol: 'EURUSD.a', canonicalSymbol: 'EUR/USD',
    assetGroup: 'forex', assetSubgroup: 'forex_major', baseAsset: 'EUR',
    quoteAsset: 'USD', metadata: {}, tradeEnabled: true,
    eligibleForScoring: true, skipReasons: [],
  },
  style: 'intraday', direction: 'LONG', decisionTime: '2026-07-12T12:00:00Z',
  confirmedScore: 0.64, liveAdjustment: -0.04, displayScore: 0.60,
  directionConfidence: 0.61, entryQuality: 0.73, entry: 1.1, stop: 1.09,
  target: 1.12, structuralRR: 2, volatilityRegime: 'NORMAL', canExecute: false,
  status: 'ELIGIBLE', reasons: ['shadow_mode'], dataFreshness: 'FRESH', spread: 0.0001,
  components: {
    D1Structure: { direction: 1, strength: 0.72, reason: 'HH/HL sequence confirmed' },
    H4Momentum: { direction: 0.44, normalized_roc: 0.5, volume_quality: 0.5 },
    H4Volatility: { regime: 'NORMAL', atr_percentile: 62, bb_width_percentile: 55 },
    H1EntryQuality: { score: 0.73, rr_score: 0.5, room_score: 1 },
  },
  confirmedTimes: { D1: 'd1', H4: 'h4', H1: 'h1' },
  groupRank: 1, groupCount: 1, globalRank: 1, globalCount: 1,
};

const groups: GhostGroup[] = [{
  assetGroup: 'forex', instrumentsDiscovered: 1, instrumentsScored: 1,
  signals: 1, bullish: 1, bearish: 0, neutral: 0, executableDemo: 0,
  averageScore: 0.64, highestScore: 0.64,
}];

const performance: GhostPerformance = {
  totalClosedTrades: 0, meanR: null, medianR: null, winRate: null,
  profitFactor: null, totalR: 0,
};


describe('Ghost Trade display contracts', () => {
  it('renders the complete shadow panel and hides demo execution', () => {
    const markup = renderToStaticMarkup(
      <GhostTradeView
        health={health}
        groups={groups}
        signals={[signal]}
        positions={[] as GhostPosition[]}
        performance={performance}
        currentScan={{
          scanId: 'scan-1', status: 'COMPLETED',
          startedAt: '2026-07-12T12:00:00Z', completedAt: '2026-07-12T12:00:42Z',
          discoveredCount: 1883, scoredCount: 1, skippedCount: 1767,
          durationMs: 42200, errors: [],
        }}
        loading={false}
        error={null}
        selectedSignal={signal}
        onSelectSignal={vi.fn()}
        onScan={vi.fn()}
        onExecute={vi.fn()}
        onDismiss={vi.fn()}
        onClosePosition={vi.fn()}
        onToggleAuto={vi.fn()}
      />,
    );

    expect(markup).toContain('Ghost Trade');
    expect(markup).toContain('SHADOW MODE — NO ORDERS CAN BE SENT');
    expect(markup).toContain('Forex');
    expect(markup).toContain('EUR/USD');
    expect(markup).toContain('D1 Structure');
    expect(markup).toContain('Confirmed 64');
    expect(markup).toContain('Live -4');
    expect(markup).toContain('Scan 42.2s');
    expect(markup).not.toContain('Discovered 1883');
    expect(markup).not.toContain('Execute Demo');
  });

  it('shows execution only for verified executable demo signals', () => {
    const verified = { ...health, mode: 'DEMO_MANUAL' as const, executionStatus: 'DEMO VERIFIED' as const };
    expect(canShowExecute(verified, { ...signal, canExecute: true })).toBe(true);
    expect(canShowExecute(health, { ...signal, canExecute: true })).toBe(false);
    expect(canShowExecute(verified, signal)).toBe(false);
  });

  it('groups and sorts signals without mixing asset classes', () => {
    const crypto = {
      ...signal,
      signalId: 'crypto',
      confirmedScore: 0.9,
      instrument: { ...signal.instrument, assetGroup: 'crypto' as const, canonicalSymbol: 'BTC/USDT' },
    };
    expect(groupSignals([crypto, signal]).forex).toEqual([signal]);
    expect(groupSignals([crypto, signal]).crypto).toEqual([crypto]);
    expect(sortSignals([signal, crypto], 'score')[0].signalId).toBe('crypto');
  });

  it('maps every safety state to unambiguous banner copy', () => {
    expect(bannerForHealth(health)).toBe('SHADOW MODE — NO ORDERS CAN BE SENT');
    expect(bannerForHealth({ ...health, mode: 'DEMO_MANUAL', executionStatus: 'DEMO VERIFIED' }))
      .toBe('DEMO VERIFIED');
    expect(bannerForHealth({ ...health, mode: 'DEMO_MANUAL', executionStatus: 'DEMO NOT VERIFIED' }))
      .toBe('DEMO EXECUTION DISABLED — ACCOUNT COULD NOT BE VERIFIED');
  });
});
