# FRED availability-lag audit

Generated: 2026-07-10T11:40:17.247309+00:00

Spot-check the implied lags against the actual FRED release dates: https://fred.stlouisfed.org/series/<SERIES_ID> (Release table). Rules stay `verified=False` until confirmed.

| series | source | rule | verified | value_time | available_time | lag (days) |
|---|---|---|---|---|---|---|
| FRED:BOERUKM:rate | FRED | fred_daily | False | 2016-11-01 00:00 | 2016-11-02 16:00 | 1.7 |
| FRED:BOERUKM:rate | FRED | fred_daily | False | 2016-12-01 00:00 | 2016-12-02 17:00 | 1.7 |
| FRED:BOERUKM:rate | FRED | fred_daily | False | 2017-01-01 00:00 | 2017-01-02 17:00 | 1.7 |
| FRED:DFEDTARL:rate | FRED | fred_daily | False | 2026-07-07 00:00 | 2026-07-08 16:00 | 1.7 |
| FRED:DFEDTARL:rate | FRED | fred_daily | False | 2026-07-08 00:00 | 2026-07-09 16:00 | 1.7 |
| FRED:DFEDTARL:rate | FRED | fred_daily | False | 2026-07-09 00:00 | 2026-07-10 16:00 | 1.7 |
| FRED:DFEDTARU:rate | FRED | fred_daily | False | 2026-07-07 00:00 | 2026-07-08 16:00 | 1.7 |
| FRED:DFEDTARU:rate | FRED | fred_daily | False | 2026-07-08 00:00 | 2026-07-09 16:00 | 1.7 |
| FRED:DFEDTARU:rate | FRED | fred_daily | False | 2026-07-09 00:00 | 2026-07-10 16:00 | 1.7 |
| FRED:DFF:rate | FRED_POLICY | fred_policy | False | 2026-07-06 00:00 | 2026-07-07 16:00 | 1.7 |
| FRED:DFF:rate | FRED_POLICY | fred_policy | False | 2026-07-07 00:00 | 2026-07-08 16:00 | 1.7 |
| FRED:DFF:rate | FRED_POLICY | fred_policy | False | 2026-07-08 00:00 | 2026-07-09 16:00 | 1.7 |
| FRED:DFII10:rate | FRED | fred_daily | False | 2026-07-02 00:00 | 2026-07-03 16:00 | 1.7 |
| FRED:DFII10:rate | FRED | fred_daily | False | 2026-07-06 00:00 | 2026-07-07 16:00 | 1.7 |
| FRED:DFII10:rate | FRED | fred_daily | False | 2026-07-07 00:00 | 2026-07-08 16:00 | 1.7 |
| FRED:DGS10:rate | FRED | fred_daily | False | 2026-07-06 00:00 | 2026-07-07 16:00 | 1.7 |
| FRED:DGS10:rate | FRED | fred_daily | False | 2026-07-07 00:00 | 2026-07-08 16:00 | 1.7 |
| FRED:DGS10:rate | FRED | fred_daily | False | 2026-07-08 00:00 | 2026-07-09 16:00 | 1.7 |
| FRED:ECBDFR:rate | FRED | fred_daily | False | 2026-07-07 00:00 | 2026-07-08 16:00 | 1.7 |
| FRED:ECBDFR:rate | FRED | fred_daily | False | 2026-07-08 00:00 | 2026-07-09 16:00 | 1.7 |
| FRED:ECBDFR:rate | FRED | fred_daily | False | 2026-07-09 00:00 | 2026-07-10 16:00 | 1.7 |
| FRED:FEDFUNDS:rate | FRED_POLICY | fred_policy | False | 2025-12-01 00:00 | 2025-12-02 17:00 | 1.7 |
| FRED:FEDFUNDS:rate | FRED_POLICY | fred_policy | False | 2026-01-01 00:00 | 2026-01-02 17:00 | 1.7 |
| FRED:FEDFUNDS:rate | FRED_POLICY | fred_policy | False | 2026-02-01 00:00 | 2026-02-02 17:00 | 1.7 |
| FRED:IRLTLT01AUM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-03-01 00:00 | 2026-05-13 16:00 | 73.7 |
| FRED:IRLTLT01AUM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-04-01 00:00 | 2026-06-12 16:00 | 72.7 |
| FRED:IRLTLT01AUM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-05-01 00:00 | 2026-07-13 16:00 | 73.7 |
| FRED:IRLTLT01DEM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-03-01 00:00 | 2026-05-13 16:00 | 73.7 |
| FRED:IRLTLT01DEM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-04-01 00:00 | 2026-06-12 16:00 | 72.7 |
| FRED:IRLTLT01DEM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-05-01 00:00 | 2026-07-13 16:00 | 73.7 |
| FRED:IRLTLT01EZM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2025-11-01 00:00 | 2026-01-12 17:00 | 72.7 |
| FRED:IRLTLT01EZM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2025-12-01 00:00 | 2026-02-12 17:00 | 73.7 |
| FRED:IRLTLT01EZM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-01-01 00:00 | 2026-03-15 16:00 | 73.7 |
| FRED:IRLTLT01GBM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-03-01 00:00 | 2026-05-13 16:00 | 73.7 |
| FRED:IRLTLT01GBM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-04-01 00:00 | 2026-06-12 16:00 | 72.7 |
| FRED:IRLTLT01GBM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-05-01 00:00 | 2026-07-13 16:00 | 73.7 |
| FRED:IRLTLT01JPM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-03-01 00:00 | 2026-05-13 16:00 | 73.7 |
| FRED:IRLTLT01JPM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-04-01 00:00 | 2026-06-12 16:00 | 72.7 |
| FRED:IRLTLT01JPM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-05-01 00:00 | 2026-07-13 16:00 | 73.7 |
| FRED:IRSTCI01AUM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-03-01 00:00 | 2026-05-13 16:00 | 73.7 |
| FRED:IRSTCI01AUM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-04-01 00:00 | 2026-06-12 16:00 | 72.7 |
| FRED:IRSTCI01AUM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-05-01 00:00 | 2026-07-13 16:00 | 73.7 |
| FRED:IRSTCI01CHM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2024-01-01 00:00 | 2024-03-14 16:00 | 73.7 |
| FRED:IRSTCI01CHM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2024-02-01 00:00 | 2024-04-12 16:00 | 71.7 |
| FRED:IRSTCI01CHM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2024-03-01 00:00 | 2024-05-13 16:00 | 73.7 |
| FRED:IRSTCI01GBM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-03-01 00:00 | 2026-05-13 16:00 | 73.7 |
| FRED:IRSTCI01GBM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-04-01 00:00 | 2026-06-12 16:00 | 72.7 |
| FRED:IRSTCI01GBM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-05-01 00:00 | 2026-07-13 16:00 | 73.7 |
| FRED:IRSTCI01JPM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-03-01 00:00 | 2026-05-13 16:00 | 73.7 |
| FRED:IRSTCI01JPM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-04-01 00:00 | 2026-06-12 16:00 | 72.7 |
| FRED:IRSTCI01JPM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-05-01 00:00 | 2026-07-13 16:00 | 73.7 |
| FRED:IRSTCI01ZAM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-03-01 00:00 | 2026-05-13 16:00 | 73.7 |
| FRED:IRSTCI01ZAM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-04-01 00:00 | 2026-06-12 16:00 | 72.7 |
| FRED:IRSTCI01ZAM156N:rate | FRED_MONTHLY_OECD | fred_monthly_oecd | False | 2026-05-01 00:00 | 2026-07-13 16:00 | 73.7 |
