import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

const storeFixture = vi.hoisted(() => {
  const grokSnapshot = {
    engine: 'grok' as const,
    scannedAt: '2026-08-27T12:11:00Z',
    rows: [{ pair: 'XAU/USD', direction: 'LONG', decision: 'READY', score: 71, maxScore: 100 }],
  };
  return {
    scanCacheA: [
      { display: 'XAU/USD', symbol: 'GC=F', direction: 'LONG', decision: 'TRADE', score: 2.4, maxScore: 3 },
    ] as unknown[] | null,
    scanCacheAMeta: { scannedAt: '2026-08-27T12:01:00Z', count: 1 },
    scanCacheB: null as unknown[] | null,
    scanCacheBMeta: null,
    engineScanSnapshots: { grok: grokSnapshot } as Record<string, typeof grokSnapshot>,
    engineCompilePending: {},
    setEngineScanSnapshot: vi.fn(),
    clearEngineScanSnapshots: vi.fn(),
    setActivePanel: vi.fn(),
    grokSnapshot,
  };
});

vi.mock('@/hooks/useStore', () => ({
  useStore: () => storeFixture,
}));

vi.mock('@/lib/apiClient', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

import EngineCompilePanel from './EngineCompilePanel';

describe('EngineCompilePanel', () => {
  it('renders a compiled pair when Engine A and GROK both passed', () => {
    const markup = renderToStaticMarkup(<EngineCompilePanel />);

    expect(markup).toContain('Scan Board');
    expect(markup).toContain('does not scan');
    expect(markup).toContain('XAU/USD');
    expect(markup).toContain('TRADE');
    expect(markup).toContain('READY');
    expect(markup).toContain('2 pass');
  });

  it('shows the empty board when no engine has been scanned', () => {
    const previousA = storeFixture.scanCacheA;
    const previousExtra = storeFixture.engineScanSnapshots;
    storeFixture.scanCacheA = null;
    storeFixture.engineScanSnapshots = {};
    try {
      const markup = renderToStaticMarkup(<EngineCompilePanel />);
      expect(markup).toContain('then come back');
      expect(markup).not.toContain('XAU/USD');
    } finally {
      storeFixture.scanCacheA = previousA;
      storeFixture.engineScanSnapshots = previousExtra;
    }
  });
});
