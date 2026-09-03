import { describe, expect, it } from 'vitest';

import {
  buildCompileSnapshots,
  compileEngineScanBoard,
  formatCompileScore,
  snapshotFromKimiState,
  snapshotFromOxEnvelope,
  snapshotFromSolLikeSignals,
  type EngineScanSnapshot,
} from '../engineScanCompile';

function snap(
  engine: EngineScanSnapshot['engine'],
  rows: EngineScanSnapshot['rows'],
  scannedAt = '2026-08-27T12:00:00Z',
): EngineScanSnapshot {
  return { engine, scannedAt, rows };
}

describe('compileEngineScanBoard', () => {
  it('joins Engine A TRADE and GROK READY on the same pair as agreement', () => {
    const board = compileEngineScanBoard([
      snap('engineA', [
        { display: 'XAU/USD', symbol: 'GC=F', direction: 'LONG', decision: 'TRADE', score: 2.4, maxScore: 3 },
      ]),
      snap('grok', [
        { pair: 'XAU/USD', symbol: 'XAUUSD', direction: 'LONG', decision: 'READY', score: 71, maxScore: 100 },
      ]),
    ]);

    expect(board.rows).toHaveLength(1);
    const row = board.rows[0];
    expect(row.display).toBe('XAU/USD');
    expect(row.agreement).toBe('agree');
    expect(row.agreedDirection).toBe('LONG');
    expect(row.passedEngines).toEqual(['engineA', 'grok']);
    expect(row.failedEngines).toEqual([]);
    expect(row.hits.engineA?.stance).toBe('pass');
    expect(row.hits.engineA?.score).toBe(2.4);
    expect(row.hits.grok?.stance).toBe('pass');
    expect(row.hits.sol).toBeNull();
  });

  it('keeps a failing engine visible with its decision and reason', () => {
    const board = compileEngineScanBoard([
      snap('engineA', [
        { display: 'XAU/USD', direction: 'LONG', decision: 'TRADE', score: 2.1 },
      ]),
      snap('grok', [
        {
          pair: 'XAU/USD',
          direction: 'NONE',
          decision: 'BLOCKED',
          score: 18,
          reason: 'session_closed',
        },
      ]),
    ]);

    const row = board.rows[0];
    expect(row.agreement).toBe('mixed');
    expect(row.passedEngines).toEqual(['engineA']);
    expect(row.failedEngines).toEqual(['grok']);
    expect(row.hits.grok).toMatchObject({
      stance: 'fail',
      decision: 'BLOCKED',
      reason: 'session_closed',
    });
  });

  it('does not treat an unscanned engine as a fail', () => {
    const board = compileEngineScanBoard([
      snap('engineA', [
        { display: 'EUR/USD', direction: 'LONG', decision: 'TRADE', score: 2.0 },
      ]),
    ]);

    const row = board.rows[0];
    expect(row.agreement).toBe('solo');
    expect(row.hits.grok).toBeNull();
    expect(row.hits.engineB).toBeNull();
    expect(row.failedEngines).toEqual([]);
    expect(board.scannedEngines).toEqual(['engineA']);
  });

  it('joins Yahoo, slash, and compact identities as one instrument', () => {
    const board = compileEngineScanBoard([
      snap('engineA', [
        { display: 'XAU/USD', symbol: 'GC=F', direction: 'LONG', decision: 'TRADE', score: 2.5 },
      ]),
      snap('kimi', [
        { symbol: 'XAUUSD', direction: 'LONG', decision: 'READY', score: 81 },
      ]),
    ]);

    expect(board.rows).toHaveLength(1);
    expect(board.rows[0].display).toBe('XAU/USD');
    expect(board.rows[0].passedEngines).toEqual(['engineA', 'kimi']);
  });

  it('marks opposite passing directions as a conflict', () => {
    const board = compileEngineScanBoard([
      snap('engineA', [
        { display: 'EUR/USD', direction: 'LONG', decision: 'TRADE', score: 2.2 },
      ]),
      snap('opus', [
        { display: 'EUR/USD', direction: 'SHORT', decision: 'TRADE', score: 0.8 },
      ]),
    ]);

    expect(board.rows[0].agreement).toBe('conflict');
    expect(board.rows[0].agreedDirection).toBeNull();
    expect(board.rows[0].passedEngines).toEqual(['engineA', 'opus']);
  });

  it('does not count WATCH as a pass', () => {
    const board = compileEngineScanBoard([
      snap('engineA', [
        { display: 'GBP/USD', direction: 'LONG', decision: 'WATCH', score: 1.4 },
      ]),
      snap('sol', [
        { pair: 'GBP/USD', direction: 'LONG', decision: 'WATCH', score: 40 },
      ]),
    ]);

    const row = board.rows[0];
    expect(row.hits.engineA?.stance).toBe('watch');
    expect(row.hits.sol?.stance).toBe('watch');
    expect(row.passedEngines).toEqual([]);
    expect(row.agreement).toBe('none');
  });

  it('treats a scanned engine with no row for the pair as absent, not unscanned', () => {
    const board = compileEngineScanBoard([
      snap('engineA', [
        { display: 'XAU/USD', direction: 'LONG', decision: 'TRADE', score: 2.3 },
      ]),
      snap('grok', [
        { pair: 'EUR/USD', direction: 'LONG', decision: 'READY', score: 80 },
      ]),
    ]);

    const gold = board.rows.find((row) => row.display === 'XAU/USD');
    expect(gold?.hits.grok?.stance).toBe('absent');
    expect(gold?.failedEngines).toEqual(['grok']);
    expect(gold?.agreement).toBe('mixed');
  });

  it('returns an empty board when nothing has been scanned', () => {
    const board = compileEngineScanBoard([]);
    expect(board.rows).toEqual([]);
    expect(board.scannedEngines).toEqual([]);
  });
});

describe('snapshot adapters', () => {
  it('maps Engine A/B caches plus extra engine snapshots without starting a scan', () => {
    const snapshots = buildCompileSnapshots({
      scanCacheA: [
        { display: 'XAU/USD', direction: 'LONG', decision: 'TRADE', score: 2.4, maxScore: 3 },
      ],
      scanCacheAMeta: { scannedAt: '2026-08-27T12:01:00Z' },
      scanCacheB: [
        {
          display: 'XAU/USD',
          direction: 'LONG',
          canonical_trade_ok: false,
          engine_b_canonical_actionable: false,
          canonical_primary_reject_reason: 'no_trigger',
          score: 1.1,
        },
      ],
      scanCacheBMeta: {
        scannedAt: '2026-08-27T12:02:00Z',
        rejectedDiagnostics: [
          { display: 'USD/JPY', direction: 'SHORT', no_trigger_classification: 'no_clear_structure' },
        ],
      },
      extra: {
        grok: snapshotFromSolLikeSignals('grok', [
          { pair: 'XAU/USD', direction: 'LONG', decision: 'READY', score: 71, decisionReason: 'ok' },
        ], '2026-08-27T12:03:00Z'),
      },
    });

    const board = compileEngineScanBoard(snapshots);
    const gold = board.rows.find((row) => row.display === 'XAU/USD');
    expect(gold?.hits.engineA?.stance).toBe('pass');
    expect(gold?.hits.engineB?.stance).toBe('fail');
    expect(gold?.hits.engineB?.reason).toBe('no_trigger');
    expect(gold?.hits.grok?.stance).toBe('pass');
    const yen = board.rows.find((row) => row.display === 'USD/JPY');
    expect(yen?.hits.engineB?.stance).toBe('fail');
    expect(yen?.hits.engineB?.reason).toBe('no_clear_structure');
  });

  it('maps KIMI active signals as pass and scored-but-unsigned symbols as fail', () => {
    const snapshot = snapshotFromKimiState({
      lastScan: 1_777_000_000,
      scanCount: 4,
      minScore: 75,
      signals: [{ symbol: 'XAUUSD', direction: 'LONG', score: 81, grade: 'A' }],
      symbols: {
        XAUUSD: { cards: { long: { total: 81, grade: 'A' } } },
        EURUSD: {
          cards: {
            long: { total: 40, grade: '—' },
            short: { total: 55, grade: 'B', reasons: ['no sweep'] },
          },
        },
      },
    });

    expect(snapshot).not.toBeNull();
    const board = compileEngineScanBoard([snapshot!]);
    const gold = board.rows.find((row) => row.key === 'XAUUSD');
    const euro = board.rows.find((row) => row.key === 'EURUSD');
    expect(gold?.hits.kimi).toMatchObject({ stance: 'pass', direction: 'LONG', score: 81 });
    expect(euro?.hits.kimi).toMatchObject({
      stance: 'fail',
      direction: 'SHORT',
      score: 55,
      reason: 'no sweep',
    });
  });

  it('maps OX TRADE / WATCH / NO_SIGNAL without rewriting scores', () => {
    const snapshot = snapshotFromOxEnvelope({
      scannedAt: 1_777_000_000,
      signals: [
        { display: 'BTC/USDT', symbol: 'BTCUSDT', decision: 'TRADE', direction: 'LONG', score: 0.62 },
        { display: 'ETH/USDT', decision: 'WATCH', direction: 'SHORT', score: 0.41 },
        { display: 'SOL/USDT', decision: 'NO_SIGNAL', direction: null, score: 0.12 },
      ],
    });
    const board = compileEngineScanBoard([snapshot]);
    expect(board.rows.find((row) => row.display === 'BTC/USDT')?.hits.oxAlpha?.stance).toBe('pass');
    expect(board.rows.find((row) => row.display === 'ETH/USDT')?.hits.oxAlpha?.stance).toBe('watch');
    expect(board.rows.find((row) => row.display === 'SOL/USDT')?.hits.oxAlpha?.stance).toBe('fail');
  });
});

describe('formatCompileScore', () => {
  it('shows native scores and leaves missing values blank', () => {
    expect(formatCompileScore(2.4, 3)).toBe('2.40/3');
    expect(formatCompileScore(71, 100)).toBe('71/100');
    expect(formatCompileScore(null, 100)).toBe('—');
  });
});
