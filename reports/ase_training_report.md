# ASE Training Report

- artifact version: `v1`
- trained family-horizons: 10
- failed family-horizons: 0

## Trained

| family | horizon | eval trades | expectancy R | win rate | Brier | DSR | Bootstrap LB | threshold | fallback | enriched |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| forex | intraday | 41 | 0.1196 | 0.5610 | 0.2469 | 3.1990 | -0.0647 | 0.2736 | no | yes |
| forex | swing | 46 | 0.1907 | 0.6957 | 0.2547 | 5.1455 | 0.0009 | 0.2385 | no | yes |
| crypto | intraday | 47 | 0.0393 | 0.5106 | 0.2472 | 0.6503 | -0.1601 | 0.2428 | no | yes |
| crypto | swing | 314 | -0.1203 | 0.4363 | 0.2598 | -35.6144 | -0.2596 | -0.3868 | no | yes |
| commodity | intraday | 49 | 0.2134 | 0.6735 | 0.2509 | 6.1440 | 0.0445 | 0.2585 | no | yes |
| commodity | swing | 85 | -0.0459 | 0.4824 | 0.2937 | -6.1433 | -0.3701 | 0.3763 | no | yes |
| equity | intraday | 62 | -0.0051 | 0.5161 | 0.2490 | -1.9510 | -0.1655 | 0.0387 | no | family_has_no_verified_enriched_route |
| equity | swing | 192 | 0.0203 | 0.5312 | 0.2546 | 3.0765 | -0.1150 | -0.1685 | no | family_has_no_verified_enriched_route |
| index_etf | intraday | 107 | 0.0131 | 0.5421 | 0.2483 | 0.1904 | -0.1674 | 0.0793 | no | yes |
| index_etf | swing | 73 | 0.0227 | 0.5753 | 0.2526 | 0.3210 | -0.1786 | 0.1678 | no | yes |

## Failed / FLAT-only

None.

Negative evaluation expectancy is reported without alteration. Only family-horizons that failed training are listed as FLAT-only.
