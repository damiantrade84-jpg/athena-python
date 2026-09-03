import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/hooks/useStore', () => ({
  useStore: () => ({
    showToast: vi.fn(),
    setEngineScanSnapshot: vi.fn(),
    markEngineCompilePending: vi.fn(),
  }),
}));

vi.mock('@/lib/apiClient', () => ({
  default: {
    get: vi.fn(async () => ({})),
    post: vi.fn(async () => ({})),
    getJson: vi.fn(async () => ({})),
    postJson: vi.fn(async () => ({})),
  },
}));

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: {},
  LineSeries: {},
  LineStyle: { Solid: 0, Dotted: 1, Dashed: 2, LargeDashed: 3 },
  createChart: vi.fn(),
  createSeriesMarkers: vi.fn(),
}));

vi.mock('@/styles/fable.css', () => ({}));

import FableEnginePanel from './FableEnginePanel';
import {
  fableCanSeal,
  fableDecisionClass,
  fablePreferredMode,
  fableTierClass,
  type FableCapabilities,
} from '@/lib/fableEngine';
import { snapshotFromFableSignals } from '@/lib/engineScanCompile';

describe('FableEnginePanel', () => {
  it('renders the codex shell with its own scoped identity before any data arrives', () => {
    const markup = renderToStaticMarkup(<FableEnginePanel />);
    expect(markup).toContain('fbl-root');
    expect(markup).toContain('data-panel="fable-engine"');
    expect(markup).toContain('Narrative Liquidity Engine');
    expect(markup).toContain('Read the market');
    expect(markup).toContain('No stories yet');
    // Seal controls only exist once a story is selected; nothing is executable on an empty codex.
    expect(markup).not.toContain('data-testid="fable-seal-button"');
  });
});

describe('fableEngine helpers', () => {
  it('only EXECUTE stories can be sealed', () => {
    expect(fableCanSeal({ decision: 'EXECUTE' })).toBe(true);
    expect(fableCanSeal({ decision: 'STAGE' })).toBe(false);
    expect(fableCanSeal(null)).toBe(false);
  });

  it('prefers the configured default mode only when it is enabled', () => {
    const capabilities: FableCapabilities = {
      defaultMode: 'demo',
      globalExecutorMode: 'demo',
      researchStatus: 'UNVALIDATED',
      modes: {
        paper: { enabled: true, brokerOrder: false },
        demo: { enabled: true, brokerOrder: true },
        live: { enabled: false, brokerOrder: true },
      },
    };
    expect(fablePreferredMode(capabilities)).toBe('demo');
    expect(fablePreferredMode({ ...capabilities, modes: { ...capabilities.modes, demo: { enabled: false, brokerOrder: true } } })).toBe('paper');
    expect(fablePreferredMode(null)).toBe('paper');
  });

  it('maps decisions and tiers to scoped classes', () => {
    expect(fableDecisionClass('EXECUTE')).toBe('fbl-decision--execute');
    expect(fableDecisionClass('VOID')).toBe('fbl-decision--void');
    expect(fableTierClass('LEGEND')).toBe('fbl-tier--legend');
    expect(fableTierClass(undefined)).toBe('fbl-tier--sketch');
  });

  it('compiles FABLE decisions into explicit scan-board stances', () => {
    const snapshot = snapshotFromFableSignals([
      { pair: 'EUR/USD', direction: 'LONG', decision: 'EXECUTE', coherence: 81.2, maxCoherence: 100, decisionReason: 'NARRATIVE_COHERENT' },
      { pair: 'GBP/USD', direction: 'SHORT', decision: 'STAGE', coherence: 70, decisionReason: 'AWAITING_RETURN' },
      { pair: 'XAU/USD', direction: 'NONE', decision: 'VOID', coherence: 0, voidReasons: ['DATA_STALE:M15'] },
    ], '2026-09-02T10:00:00Z');
    expect(snapshot.engine).toBe('fable');
    expect(snapshot.rows.map((row) => row.stance)).toEqual(['pass', 'watch', 'fail']);
    expect(snapshot.rows[0].score).toBe(81.2);
    expect(snapshot.rows[2].reason).toBe('DATA_STALE:M15');
  });
});
