"""OX Book — evidence-first daily trend book (standalone, paper/research only).

Built from the surviving-strategy evidence base:
  - docs/research/what_surviving_strategies_do_differently.md
  - athena_research/tsmom_engine/RESULTS.md (25-yr deep validation)

Design invariants (each traces to measured evidence, not opinion):
  1. D1-only signal horizon        — slow edges persist; fast edges decayed post-1990
                                     (Lempérière et al. 2014).
  2. Long-only default             — short side showed no persistent edge across all
                                     groups in the repo's 25-yr validation.
  3. Fixed canonical config        — one plateau-centred parameter set for every
                                     market; per-market tuning is forbidden by design
                                     (tuning is how backtests die: Bailey/López de Prado).
  4. Curated breadth               — only markets with standalone gold-class edge AND
                                     pairwise correlation below cap join the book;
                                     pseudo-replication fakes N and dilutes quality.
  5. Multiple-testing discipline   — every evaluation is recorded in an append-only
                                     trial registry; promotion claims require a t-stat
                                     above the Harvey-Liu-Zhu hurdle (default 3.0).
  6. Cost stress + decay haircut   — edges must survive stressed costs; sizing expects
                                     only (1 - DECAY_HAIRCUT) of backtest expectancy
                                     (McLean-Pontiff 58% post-publication decline).

This package is advisory output ONLY. It has no execution path and never touches
risk, kill-switch, or broker code paths of Engines A/B/C/D or ASE.
"""
