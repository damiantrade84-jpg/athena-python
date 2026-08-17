import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import ASESupportIndicator from './ASESupportIndicator';

describe('ASESupportIndicator', () => {
  it('renders the positive shadow state with explicit advisory scope', () => {
    const markup = renderToStaticMarkup(
      <ASESupportIndicator
        shadow={{
          schemaVersion: 'ase_engine_b_shadow.v2',
          direction: 'LONG',
          decisionStatus: 'TRADE',
          probabilityPositive: 0.61,
          expectedNetR: 0.14,
          alignment: 'ALIGNED',
          supportState: 'SUPPORTED',
          supportEligible: true,
          reason: 'ase_trade_aligned_positive',
          horizon: 'intraday',
          dataQualityOk: true,
          modelHealthOk: true,
          advisoryOnly: true,
          affectsEngineBScore: false,
        }}
      />,
    );

    expect(markup).toContain('data-ase-support="supported"');
    expect(markup).toContain('ASE SUPPORTS');
    expect(markup).toContain('E[net R] +0.140');
    expect(markup).toContain('Advisory only — no Engine B score or gate change');
  });

  it('renders stale context as unavailable, never supported', () => {
    const markup = renderToStaticMarkup(
      <ASESupportIndicator
        shadow={{
          supportState: 'UNAVAILABLE',
          alignment: 'ASE_FLAT',
          reason: 'ase_signal_stale',
        }}
      />,
    );

    expect(markup).toContain('data-ase-support="advisory"');
    expect(markup).toContain('ASE UNAVAILABLE');
    expect(markup).not.toContain('ASE SUPPORTS');
  });
});
