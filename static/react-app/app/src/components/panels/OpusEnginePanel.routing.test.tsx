import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

const refresh = vi.hoisted(() => vi.fn(async () => undefined));
const accountFixture = vi.hoisted(() => ({
  accounts: [{ broker: 'mt5', live: true, available: true, accountKind: 'demo' }],
}));

vi.mock('@/hooks/useApiData', () => ({
  useApiPoll: (url: string) => {
    if (url === '/api/opus/status') {
      return {
        data: {
          ok: true,
          enabled: true,
          mode: 'live',
          liveArmed: true,
          liveReason: 'live armed',
        },
        loading: false,
        error: null,
        refresh,
      };
    }
    if (url === '/api/opus/account') {
      return {
        data: {
          ok: true,
          liveArmed: true,
          liveReason: 'live armed',
          requireDemoAccount: true,
          accounts: accountFixture.accounts,
        },
        loading: false,
        error: null,
        refresh,
      };
    }
    return {
      data: { ok: true, orders: [], count: 0 },
      loading: false,
      error: null,
      refresh,
    };
  },
  useApiPost: () => ({ post: vi.fn(), loading: false, error: null }),
}));

import OpusEnginePanel from './OpusEnginePanel';

describe('OpusEnginePanel execution routing', () => {
  it('defaults to the confirmed live demo broker when OPUS is armed', () => {
    const markup = renderToStaticMarkup(<OpusEnginePanel />);

    expect(markup).toContain('Broker — DEMO');
    expect(markup).not.toContain('Paper simulator');
  });

  it('fails closed to paper when no live demo broker is available', () => {
    const accounts = accountFixture.accounts;
    accountFixture.accounts = [];
    try {
      const markup = renderToStaticMarkup(<OpusEnginePanel />);

      expect(markup).toContain('Paper simulator');
      expect(markup).not.toContain('Broker — DEMO');
    } finally {
      accountFixture.accounts = accounts;
    }
  });
});
