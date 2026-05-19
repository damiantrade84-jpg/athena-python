# Indicator Calibration References

This file is an auditable companion to
`docs/research/indicator_calibration_references.yaml`.

It records verified references that may be cited in research and audit notes
about Athena indicator calibration. It does not change trading behavior,
thresholds, indicators, scoring, SL/TP, or execution logic.

## Policy

- Every registry entry must include `citation` and `used_for`.
- Verified references may be cited only for the listed `used_for` purpose.
- Unverified references must not be marked `production_supporting: true`.
- A verified reference is not a production approval. It cannot justify a code or
  config change unless a future task explicitly reviews and approves that use.
- Entries marked `production_supporting: false` are research context only.

## Sections

### ATR

Current verified reference:

- J. Welles Wilder Jr., *New Concepts in Technical Trading Systems* (1978),
  ISBN `0-89459-027-8`, Open Library `OL4745184M`.

Used for ATR origin and terminology only. It does not validate Athena-specific
ATR periods, volatility bands, or stop/target multipliers.

### ADX / DI

Current verified reference:

- J. Welles Wilder Jr., *New Concepts in Technical Trading Systems* (1978),
  ISBN `0-89459-027-8`, Open Library `OL4745184M`.

Used for ADX/DI origin and terminology only. It does not validate Athena ADX
floors, DI alignment multipliers, or hard-fail values.

### RSI

Current verified reference:

- J. Welles Wilder Jr., *New Concepts in Technical Trading Systems* (1978),
  ISBN `0-89459-027-8`, Open Library `OL4745184M`.

Used for RSI origin and terminology only. It does not validate Athena RSI
thresholds, z-score transforms, or scoring weights.

### Trend / Momentum

Current verified reference:

- Tobias J. Moskowitz, Yao Hua Ooi, and Lasse Heje Pedersen, "Time Series
  Momentum," *Journal of Financial Economics* 104(2), 228-250 (2012),
  DOI `10.1016/j.jfineco.2011.11.003`.

Used for research context that trend/momentum effects have been studied across
asset classes. It does not validate Athena's EMA stack, MACD/RSI mix, intraday
timeframes, thresholds, or execution rules.

### Volatility Targeting

Current verified reference:

- Alan Moreira and Tyler Muir, "Volatility-Managed Portfolios,"
  *Journal of Finance* 72(4), 1611-1644 (2017),
  DOI `10.1111/jofi.12513`.

Used for research context around volatility-managed exposure and inverse-vol
position overlays. It does not authorize live sizing changes.

### Data-Snooping / Multiple Testing

Current verified references:

- Campbell R. Harvey, Yan Liu, and Caroline Zhu, "... and the Cross-Section of
  Expected Returns," *Review of Financial Studies* 29(1), 5-68 (2016),
  DOI `10.1093/rfs/hhv059`.
- Halbert White, "A Reality Check for Data Snooping," *Econometrica* 68(5),
  1097-1126 (2000), DOI `10.1111/1468-0262.00152`.
- Peter Reinhard Hansen, "A Test for Superior Predictive Ability,"
  *Journal of Business & Economic Statistics* 23(4), 365-380 (2005),
  DOI `10.1198/073500105000000063`.

Used for anti-overfit governance and data-snooping controls. These references do
not prove any Athena factor, threshold, or parameter variant is valid.

### ATR SL/TP Practitioner Calibration

Current verified reference:

- J. Welles Wilder Jr., *New Concepts in Technical Trading Systems* (1978),
  ISBN `0-89459-027-8`, Open Library `OL4745184M`.

Used only as the ATR definition when discussing ATR-based stops and targets.
No verified reference in this registry currently validates Athena's ATR SL/TP
multipliers.

### Engine B Structure/RR Validation

Current verified references:

- Halbert White, "A Reality Check for Data Snooping," *Econometrica* 68(5),
  1097-1126 (2000), DOI `10.1111/1468-0262.00152`.
- Peter Reinhard Hansen, "A Test for Superior Predictive Ability,"
  *Journal of Business & Economic Statistics* 23(4), 365-380 (2005),
  DOI `10.1198/073500105000000063`.

Used for validation governance when comparing Engine B RR, fallback target, and
structure-gate variants. They do not validate BOS/CHoCH/OB/FVG definitions, RR
thresholds, fallback TP, or level-selection logic.

## Verification Status

No unverified references are included in the machine-readable registry.
