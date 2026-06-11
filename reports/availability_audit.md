# ASE Phase 0 — PTIS availability audit

Generated: 2026-06-11 18:41 UTC

Each series uses a frozen `available_time` rule. Series with default (unverified)
FRED lag rules are flagged `UNVERIFIED_LAG` and excluded from enriched models.

| series_id | source | rule_id | verified | flag | row_count |
|-----------|--------|---------|----------|------|-----------|
| CFTC:COT:AUD:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:BTC:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:CAD:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:CATTLE:noncomm_net | CFTC | cftc_cot | yes |  | 1 |
| CFTC:COT:CHF:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:COCOA:noncomm_net | CFTC | cftc_cot | yes |  | 1 |
| CFTC:COT:COFFEE:noncomm_net | CFTC | cftc_cot | yes |  | 1 |
| CFTC:COT:CORN:noncomm_net | CFTC | cftc_cot | yes |  | 1 |
| CFTC:COT:ETH:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:EUR:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:GBP:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:HG:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:JPY:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:MXN:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:NG:noncomm_net | CFTC | cftc_cot | yes |  | 9 |
| CFTC:COT:NQ100:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:NZD:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:OIL:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:PL:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:SOYBEANS:noncomm_net | CFTC | cftc_cot | yes |  | 1 |
| CFTC:COT:SP500:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:SUGAR:noncomm_net | CFTC | cftc_cot | yes |  | 1 |
| CFTC:COT:WHEAT:noncomm_net | CFTC | cftc_cot | yes |  | 1 |
| CFTC:COT:XAG:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| CFTC:COT:XAU:noncomm_net | CFTC | cftc_cot | yes |  | 127 |
| EODHD:AAPL:D1:close | EODHD | eodhd_bar | yes |  | 100 |
| EODHD:AAPL:D1:high | EODHD | eodhd_bar | yes |  | 100 |
| EODHD:AAPL:D1:low | EODHD | eodhd_bar | yes |  | 100 |
| EODHD:AAPL:D1:open | EODHD | eodhd_bar | yes |  | 100 |
| EODHD:AAPL:D1:volume | EODHD | eodhd_bar | yes |  | 100 |
| EODHD:AAPL:H1:close | EODHD | eodhd_bar | yes |  | 100 |
| EODHD:AAPL:H1:high | EODHD | eodhd_bar | yes |  | 100 |
| EODHD:AAPL:H1:low | EODHD | eodhd_bar | yes |  | 100 |
| EODHD:AAPL:H1:open | EODHD | eodhd_bar | yes |  | 100 |
| EODHD:AAPL:H1:volume | EODHD | eodhd_bar | yes |  | 100 |
| EODHD:AAPL:H4:close | EODHD | eodhd_bar | yes |  | 100 |
| EODHD:AAPL:H4:high | EODHD | eodhd_bar | yes |  | 100 |
| EODHD:AAPL:H4:low | EODHD | eodhd_bar | yes |  | 100 |
| EODHD:AAPL:H4:open | EODHD | eodhd_bar | yes |  | 100 |
| EODHD:AAPL:H4:volume | EODHD | eodhd_bar | yes |  | 100 |
| FRED:BOERUKM:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 3867 |
| FRED:DFF:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 26265 |
| FRED:DFII10:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 5856 |
| FRED:DGS10:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 16086 |
| FRED:ECBDFR:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 10015 |
| FRED:FEDFUNDS:rate | FRED_POLICY | fred_policy | no | UNVERIFIED_LAG | 860 |
| FRED:IRLTLT01AUM156N:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 682 |
| FRED:IRLTLT01DEM156N:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 840 |
| FRED:IRLTLT01EZM156N:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 673 |
| FRED:IRLTLT01GBM156N:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 796 |
| FRED:IRLTLT01JPM156N:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 448 |
| FRED:IRSTCI01AUM156N:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 429 |
| FRED:IRSTCI01CHM156N:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 627 |
| FRED:IRSTCI01GBM156N:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 580 |
| FRED:IRSTCI01JPM156N:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 490 |
| FRED:IRSTCI01ZAM156N:rate | FRED | fred_daily | no | UNVERIFIED_LAG | 832 |

## UNVERIFIED_LAG series

These may not be used in enriched ASE models until vintage/lag is verified:

- `FRED:BOERUKM:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
- `FRED:DFF:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
- `FRED:DFII10:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
- `FRED:DGS10:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
- `FRED:ECBDFR:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
- `FRED:FEDFUNDS:rate` — FRED policy rates: release calendar where known, else obs + 1 BD
- `FRED:IRLTLT01AUM156N:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
- `FRED:IRLTLT01DEM156N:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
- `FRED:IRLTLT01EZM156N:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
- `FRED:IRLTLT01GBM156N:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
- `FRED:IRLTLT01JPM156N:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
- `FRED:IRSTCI01AUM156N:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
- `FRED:IRSTCI01CHM156N:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
- `FRED:IRSTCI01GBM156N:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
- `FRED:IRSTCI01JPM156N:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
- `FRED:IRSTCI01ZAM156N:rate` — FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)
