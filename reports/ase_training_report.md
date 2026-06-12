# ASE Training Report

- artifact version: `v1`
- trained family-horizons: 8
- failed family-horizons: 2

## Trained

| family | horizon | eval trades | expectancy R | win rate | Brier | threshold | fallback | enriched |
|---|---|---:|---:|---:|---:|---:|---|---|
| forex | intraday | 40 | 0.1102 | 0.5500 | 0.2466 | 0.1573 | no | insufficient_verified_enriched_rows |
| forex | swing | 40 | 0.0867 | 0.6000 | 0.2530 | 0.1800 | no | insufficient_verified_enriched_rows |
| commodity | intraday | 73 | 0.0724 | 0.5342 | 0.2520 | 0.2339 | no | insufficient_verified_enriched_rows |
| commodity | swing | 82 | 0.2212 | 0.6829 | 0.2450 | 0.2403 | no | insufficient_verified_enriched_rows |
| equity | intraday | 134 | 0.0627 | 0.5597 | 0.2560 | 0.0755 | no | family_has_no_verified_enriched_route |
| equity | swing | 108 | 0.0412 | 0.5000 | 0.2566 | -0.2155 | no | family_has_no_verified_enriched_route |
| index_etf | intraday | 40 | 0.0872 | 0.5750 | 0.2459 | 0.1579 | no | family_has_no_verified_enriched_route |
| index_etf | swing | 247 | 0.0252 | 0.5587 | 0.2722 | -0.5863 | no | family_has_no_verified_enriched_route |

## Failed / FLAT-only

- `crypto/intraday`: no events for crypto/intraday
- `crypto/swing`: no events for crypto/swing

Negative evaluation expectancy is reported without alteration. Only family-horizons that failed training are listed as FLAT-only.
