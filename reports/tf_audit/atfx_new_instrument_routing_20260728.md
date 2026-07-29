# ATFX new-instrument routing — resolved policy (2026-07-28)

Generated read-only from `timeframe_policy.describe_symbol_policy`. `auto` is the style a live scan selects.


## Forex (24)

| symbol | class | score_group | region | profile | source | R/B/S/Se/Tr | m5 | speed | disabled |
|---|---|---|---|---|---|---|---|---|---|
| AUD/CAD | forex | forex_crosses | - | FOREX_CROSSES_BROAD | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | NORMAL | M15,M5,M1 |
| CAD/CHF | forex | forex_crosses | - | FOREX_CROSSES_BROAD | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | NORMAL | M15,M5,M1 |
| EUR/CAD | forex | forex_crosses | - | FOREX_CROSSES_BROAD | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | NORMAL | M15,M5,M1 |
| EUR/NZD | forex | forex_crosses | - | FOREX_CROSSES_BROAD | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | NORMAL | M15,M5,M1 |
| GBP/CAD | forex | forex_crosses | - | FOREX_CROSSES_BROAD | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | NORMAL | M15,M5,M1 |
| GBP/CHF | forex | forex_crosses | - | FOREX_CROSSES_BROAD | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | NORMAL | M15,M5,M1 |
| GBP/NZD | forex | forex_crosses | - | FOREX_CROSSES_BROAD | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | NORMAL | M15,M5,M1 |
| NZD/CAD | forex | forex_crosses | - | FOREX_CROSSES_BROAD | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | NORMAL | M15,M5,M1 |
| NZD/CHF | forex | forex_crosses | - | FOREX_CROSSES_BROAD | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | NORMAL | M15,M5,M1 |
| CAD/JPY | forex | forex_crosses | - | FOREX_CROSSES_LIQUID | SYMBOL_OVERRIDE | D1/H4/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| CHF/JPY | forex | forex_crosses | - | FOREX_CROSSES_LIQUID | SYMBOL_OVERRIDE | D1/H4/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| NZD/JPY | forex | forex_crosses | - | FOREX_CROSSES_LIQUID | SYMBOL_OVERRIDE | D1/H4/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| USD/NOK | forex | forex_crosses | - | FOREX_SCANDI_USD | SYMBOL_OVERRIDE | D1/H4/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| USD/SEK | forex | forex_crosses | - | FOREX_SCANDI_USD | SYMBOL_OVERRIDE | D1/H4/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| USD/DKK | forex | forex_crosses | - | FOREX_CROSSES_BROAD | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | NORMAL | M15,M5,M1 |
| USD/CNH | forex | forex_crosses | - | FOREX_MANAGED_ASIA | SYMBOL_OVERRIDE | D1/H4/H1/M30/M15 | disabled | SLOW | M5,M1 |
| EUR/HUF | forex | forex_exotics | - | FOREX_EXOTICS_LIQUID | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | SLOW | M15,M5,M1 |
| EUR/PLN | forex | forex_exotics | - | FOREX_EXOTICS_LIQUID | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | SLOW | M15,M5,M1 |
| USD/CZK | forex | forex_exotics | - | FOREX_EXOTICS_LIQUID | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | SLOW | M15,M5,M1 |
| USD/HUF | forex | forex_exotics | - | FOREX_EXOTICS_LIQUID | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | SLOW | M15,M5,M1 |
| USD/PLN | forex | forex_exotics | - | FOREX_EXOTICS_LIQUID | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | SLOW | M15,M5,M1 |
| EUR/ZAR | forex | forex_exotics | - | ENGINE_A_SWING_FOREX_EXOTICS_RESTRICTED | SYMBOL_OVERRIDE | D1/D1/H4/H1/H1 | disabled | SLOW | M30,M15,M5,M1 |
| GBP/ZAR | forex | forex_exotics | - | ENGINE_A_SWING_FOREX_EXOTICS_RESTRICTED | SYMBOL_OVERRIDE | D1/D1/H4/H1/H1 | disabled | SLOW | M30,M15,M5,M1 |
| USD/HKD | forex | forex_exotics | - | ENGINE_A_SWING_FOREX_MANAGED_PEGGED_RESTRICTED | SYMBOL_OVERRIDE | D1/D1/H4/H1/H1 | disabled | SLOW | M30,M15,M5,M1 |

## Metals (1)

| symbol | class | score_group | region | profile | source | R/B/S/Se/Tr | m5 | speed | disabled |
|---|---|---|---|---|---|---|---|---|---|
| XAU/ZAR | commodity | commodity_other | - | THIN_METALS_BASE_SOFTS | SCORE_GROUP_OVERRIDE | D1/H4/H4/H1/M30 | disabled | NORMAL | M15,M5,M1 |

## Indices (4)

| symbol | class | score_group | region | profile | source | R/B/S/Se/Tr | m5 | speed | disabled |
|---|---|---|---|---|---|---|---|---|---|
| China A50 | index | asian_indices | - | EQUITY_INDEX_BROAD | SYMBOL_OVERRIDE | D1/H4/H4/H1/M30 | disabled | SLOW | M15,M5,M1 |
| Spain 35 | index | eu_indices | - | EQUITY_INDEX_STANDARD | SCORE_GROUP_OVERRIDE | D1/H4/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| France 40 | index | eu_indices | - | EQUITY_INDEX_STANDARD | SCORE_GROUP_OVERRIDE | D1/H4/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| Italy 40 | index | eu_indices | - | EQUITY_INDEX_STANDARD | SCORE_GROUP_OVERRIDE | D1/H4/H1/M30/M15 | disabled | NORMAL | M5,M1 |

## Shares — first 12 of 220

| symbol | class | score_group | region | profile | source | R/B/S/Se/Tr | m5 | speed | disabled |
|---|---|---|---|---|---|---|---|---|---|
| AA | stock | stock_other | CASH_EQUITY_US | CASH_EQUITY_STANDARD_DYNAMIC | SCORE_GROUP_OVERRIDE | D1/D1/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| AAL | stock | stock_other | CASH_EQUITY_US | CASH_EQUITY_STANDARD_DYNAMIC | SCORE_GROUP_OVERRIDE | D1/D1/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| ABNB | stock | stock_other | CASH_EQUITY_US | CASH_EQUITY_STANDARD_DYNAMIC | SCORE_GROUP_OVERRIDE | D1/D1/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| ABT | stock | stock_other | CASH_EQUITY_US | CASH_EQUITY_STANDARD_DYNAMIC | SCORE_GROUP_OVERRIDE | D1/D1/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| ADBE | stock | stock_other | CASH_EQUITY_US | CASH_EQUITY_STANDARD_DYNAMIC | SCORE_GROUP_OVERRIDE | D1/D1/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| AI | stock | stock_other | CASH_EQUITY_US | CASH_EQUITY_STANDARD_DYNAMIC | SCORE_GROUP_OVERRIDE | D1/D1/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| AIG | stock | stock_other | CASH_EQUITY_US | CASH_EQUITY_STANDARD_DYNAMIC | SCORE_GROUP_OVERRIDE | D1/D1/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| AKAM | stock | stock_other | CASH_EQUITY_US | CASH_EQUITY_STANDARD_DYNAMIC | SCORE_GROUP_OVERRIDE | D1/D1/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| ALL | stock | stock_other | CASH_EQUITY_US | CASH_EQUITY_STANDARD_DYNAMIC | SCORE_GROUP_OVERRIDE | D1/D1/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| AMC | stock | stock_other | CASH_EQUITY_US | CASH_EQUITY_STANDARD_DYNAMIC | SCORE_GROUP_OVERRIDE | D1/D1/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| ANF | stock | stock_other | CASH_EQUITY_US | CASH_EQUITY_STANDARD_DYNAMIC | SCORE_GROUP_OVERRIDE | D1/D1/H1/M30/M15 | disabled | NORMAL | M5,M1 |
| ATHM | stock | stock_other | CASH_EQUITY_US | CASH_EQUITY_STANDARD_DYNAMIC | SCORE_GROUP_OVERRIDE | D1/D1/H1/M30/M15 | disabled | NORMAL | M5,M1 |

### Share region totals

- CASH_EQUITY_EUROPE: 32
- CASH_EQUITY_HONG_KONG: 46
- CASH_EQUITY_US: 142
- **total: 220**

### Distinct share resolutions

- `stock_other` / `CASH_EQUITY_STANDARD_DYNAMIC` / D1/D1/H1/M30/M15 / m5=disabled — **220 symbols**
