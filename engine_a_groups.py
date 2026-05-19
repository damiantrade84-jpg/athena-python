"""Engine A score-group constants.

Kept config-independent so boot-time config validation can enforce threshold
coverage without importing scoring.py while config.py is still loading.
"""

ENGINE_A_KNOWN_SCORE_GROUPS = frozenset({
    "forex_majors",
    "forex_crosses",
    "forex_exotics",
    "forex_other",
    "crypto_btc",
    "crypto_eth",
    "crypto_doge",
    "crypto_alt_majors",
    "crypto_other",
    "precious_trackers",
    "energy_oil",
    "nat_gas",
    "copper",
    "pgm_metals",
    "base_metals",
    "softs",
    "commodity_other",
    "us_indices_trackers",
    "eu_indices",
    "asian_indices",
    "index_other",
    "us_stock_single",
    "bond_tlt",
    "smallcap_em_etf",
    "stock_other",
    "unknown",
})
