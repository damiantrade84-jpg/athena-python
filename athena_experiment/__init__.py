"""Tuning Lab: Engine A/B experimentation tool.

Separate from ``athena_backtest`` (imports it, does not modify its behavior).
Lets an operator tune timeframe roles, per-group weights/thresholds, and a
curated set of new indicators for one Engine A or Engine B pair, run a
frozen-candle backtest of the change against the current default, and — only
on explicit confirmation — push an improvement into ``config.local.yaml`` as
the new default (effective on the next Athena restart).

Every new knob this package can set defaults to a value that reproduces
today's exact live scoring; see ``athena_experiment.knobs`` for the full
catalog and the corresponding scoring-core hook points.
"""
