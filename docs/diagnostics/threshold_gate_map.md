# Threshold And Gate Map

This map is based on code inspection in `scanner.py`, `scoring.py`, `factor_scoring.py`, `market_structure.py`, `engine_c.py`, `risk_engine.py`, and `config.yaml`.

| gate name | file/function | current value | config key | applies to | effect |
|---|---|---:|---|---|---|
| Engine A live score threshold | `scoring.py:get_score_threshold` | crypto 2.4, forex 2.1, commodity 1.8, stock 1.8, index 1.5 | `MIN_CONFLUENCE_CLASS` | all assets | blocks trade tier |
| Engine A max score | `athena.py:analyze_pair`, `factor_scoring.py:compute_factor_scores` | 3.0 | none | all assets | normalizes only |
| Engine A normalized C participation floor | `engine_c.py:normalise_engine_a` | `scoreNorm > 0.30` | none | all assets | blocks Engine C A participation |
| Engine A ADX hard fail | `factor_scoring.py:_adx_gate` | 15.0 | `FACTOR_ADX_HARD_FAIL` | all assets | blocks Engine A score |
| Engine A ADX soft multiplier | `factor_scoring.py:_adx_gate` | 0.65 | `FACTOR_ADX_SOFT_MULT` | all assets | penalizes score |
| Engine A ADX trend minimum | `factor_scoring.py:_adx_gate` | crypto 15, forex 20, commodity/stock/index 25 | `ADX_TREND_MIN_CLASS` | all assets | selects soft vs full multiplier |
| Engine A D1/H4/H1 trend alignment | `factor_scoring.py:_coherent_trend_score` | D1 0.50, H4 0.30, H1 0.20 weighting | code constants | all assets | scores/penalizes direction |
| Engine A session multiplier | `factor_scoring.py:_session_multiplier` | core 1.0, shoulder 0.90, off 0.75 | `FOREX_ENGINE.session_*` | forex | penalizes score |
| Engine A momentum conviction | `factor_scoring.py:_momentum_quality` | RSI/MACD quality 0-1 | `INDICATOR_WEIGHTS.momentum` | all assets | penalizes/boosts score |
| Engine A addon conviction | `factor_scoring.py:_asset_addon` | forex carry, crypto funding/OI, commodity COT | factor config/data | forex/crypto/commodity | penalizes/boosts score |
| Engine A conviction floor | `factor_scoring.py:compute_factor_scores` | 0.60 | `FACTOR_CONVICTION_FLOOR` | all assets | preserves floor under weak momentum/addon |
| Scan quantile gate | `scanner.py:compute_scan_quantile_floors` | disabled | `SCAN_QUANTILE_*` | all assets except configured excludes | blocks trade tier if enabled |
| Blocked trend states | `scoring.py:_classify_signal` | `DEAD RANGING`, `DEVELOPING` | `ENGINE_A_BLOCKED_TREND_STATES` | all assets | watchlist/blocks trade tier |
| Engine B structural verdict | `scanner.py:_analyse`, `engine_c.py:normalise_engine_b` | must be `CLEAR` | none | all assets | blocks Engine B signal |
| Engine B score threshold | `market_structure.py:engine_b_confidence_passes` | style profile min_score, regime scaled | `NAKED_ENGINE.style_profiles.*.min_score`, `ENGINE_B_REGIME_MULTIPLIERS` | all assets | blocks Engine B confidence pass |
| Engine B confidence passed | `market_structure.py:calculate_confidence` | must be true | checklist profile | all assets | blocks Engine B signal |
| Engine B structure gate | `market_structure.py:calculate_confidence` | not hard counter and aligned/BOS/sweep | code | all assets | checklist block |
| Engine B location gate | `market_structure.py:calculate_confidence` | zone/OB/breakout entry | `NAKED_ENGINE.zone_multipliers` | all assets | checklist block |
| Engine B entry trigger gate | `market_structure.py:calculate_confidence` | trigger or BOS+volume or sweep/CHoCH at zone | code | all assets | checklist block |
| Engine B room gate | `market_structure.py:calculate_confidence` | profile `min_room_atr`, default 0.35 | `NAKED_ENGINE.score_group_overrides.*.min_room_atr` | all assets | checklist point/fail reason |
| Engine B RR gate | `market_structure.py:calculate_confidence` | scalp/intraday 1.5, swing 2.0 plus overrides | `NAKED_ENGINE.style_profiles.*.min_rr` | all assets | checklist block |
| Engine B D1 conflict penalty | `market_structure.py:calculate_confidence` | 0.25 score deduction | `NAKED_ENGINE.d1_pd_array_penalty` | all assets | penalizes score |
| Engine B forex ADX gate | `market_structure.py:calculate_confidence` | 12 | `ENGINE_B_FOREX_ADX_MIN` | forex | blocks structure_ok |
| Engine B OB/FVG/sweep/BOS requirements | `market_structure.py:analyze_structure`, `calculate_confidence` | evidence-dependent | `NAKED_ENGINE.ob_min_strength` and code | all assets | scores/checklist |
| Engine B market profile gate | `market_structure.py:calculate_confidence` | profile scoring enabled | `ENGINE_B_PROFILE_SCORING_ENABLED` | all assets | scores only |
| Engine C A-only executable gate | `engine_c.py:compute_consensus` | reliability >=0.60 and conviction >=0.65; reduced risk >=0.50; watchlist >=0.40 | code | all assets | blocks/watchlists |
| Engine C B-only executable gate | `engine_c.py:compute_consensus` | `B_norm * 0.65`, same reliability/conviction gates | `ENGINE_C_B_ONLY_MULT` | all assets | blocks/watchlists |
| Engine C aligned executable gate | `engine_c.py:compute_consensus` | execute >=0.65, reduced risk >=0.50, watchlist >=0.50 | code | all assets | blocks/watchlists |
| Engine C conflict override | `engine_c.py:compute_consensus` | B >=0.70, A <=0.45, B penalty 0.85 | `ENGINE_C_B_CONFLICT_*` | all assets | blocks or B override |
| Engine C reliability gate | `engine_c.py:compute_consensus` | execute >=0.60 reliability, reduced risk >=0.45 | code/meta policy | all assets | blocks/watchlists |
| Engine C B checklist requirement | `engine_c.py:normalise_engine_b`, `risk_engine.py:risk_check` | `confidence.passed is True` | none | all assets | blocks Engine C/risk |
| Execution data freshness | `risk_engine.py:risk_check` | policy from freshness service | `PAPER_SOAK.REQUIRED_FRESHNESS_GATE`, freshness config | all assets | blocks execution |
| Signal freshness | `risk_engine.py:risk_check` | 300 seconds | `SIGNAL_MAX_AGE_SEC` | all assets | blocks execution |
| Kill switch | `scanner.py:run_full_scan`, `risk_engine.py:risk_check` | runtime state | runtime | all assets | blocks scan/execution |
| Duplicate position | `risk_engine.py:risk_check` | one same-pair position | code | all assets | blocks execution |
| Max open positions | `risk_engine.py:risk_check` | 5 default | `MAX_OPEN_POSITIONS` | all assets | blocks execution |
| Correlation cap | `risk_engine.py:risk_check` | 2 default | `MAX_CORRELATED_POSITIONS` | all assets | blocks execution |
| Max SL percent | `risk_engine.py:risk_check` | per asset map | `MAX_SL_PCT` | all assets | blocks execution |
| Account/risk sizing | `risk_engine.py:risk_check` | account data, Kelly/base risk, heat | `RISK_PCT`, `MAX_RISK_PER_TRADE`, `MAX_PORTFOLIO_HEAT`, `KELLY_MAX_RISK` | all assets | blocks or sizes |
| Drawdown/daily loss | `risk_engine.py:risk_check` | reduce 10%, stop 15%, daily 5% default | `DRAWDOWN_*`, `DAILY_LOSS_LIMIT` | all assets | blocks/reduces |
