# ASE Training Report

- artifact version: `v2-integrity`
- trained family-horizons: 8
- failed family-horizons: 2

## Trained

| family | horizon | eval trades | expectancy R | win rate | Brier | DSR | Bootstrap LB | threshold | fallback | enriched |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| forex | intraday | 100 | 0.1980 | 0.6700 | 0.2459 | 10.1326 | 0.0851 | 0.1852 | no | yes |
| forex | swing | 55 | -0.3118 | 0.3636 | 0.2583 | -13.8105 | -0.5457 | 0.1365 | no | yes |
| crypto | intraday | 22 | -0.5333 | 0.2273 | 0.2675 | -7.3642 | -0.6837 | 0.4120 | no | yes |
| crypto | swing | 42 | -0.0459 | 0.4048 | 0.2583 | -4.0883 | -0.1947 | 0.2952 | no | yes |
| commodity | intraday | 23 | -0.1663 | 0.4783 | 0.2499 | -5.4967 | -0.4732 | 0.1643 | no | yes |
| commodity | swing | 33 | -0.3137 | 0.3636 | 0.3159 | -9.0092 | -0.5020 | 0.3579 | no | yes |
| equity | swing | 12 | 0.2278 | 0.5833 | 0.2501 | 0.8205 | 0.2278 | 0.2896 | no | family_has_no_verified_enriched_route |
| index_etf | swing | 29 | 0.0542 | 0.5517 | 0.2535 | 0.2540 | -0.0962 | 0.2231 | no | yes |

## Failed / FLAT-only

- `equity/intraday`: no events for equity/intraday
- `index_etf/intraday`: no events for index_etf/intraday

Negative evaluation expectancy is reported without alteration. Only family-horizons that failed training are listed as FLAT-only.
