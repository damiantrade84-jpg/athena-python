# What surviving strategies do differently — evidence review

Date: 2026-08-25
Scope: What distinguishes the small minority of trading strategies/traders that stay
profitable out-of-sample from the majority that fail? Every claim below was checked
against the cited primary source on this date. Anything not directly confirmed from a
primary source is labeled `not verified`.

---

## 1. The base rate of failure

**Brazil, futures day traders (Chague, De-Losso, Giovannetti 2020).**
Full population data from the CVM regulator: of 19,646 individuals who began day
trading mini-Ibovespa futures in 2013–2015, only 1,551 persisted past 300 trading days,
and **97% of those persistent traders lost money net of fees**. Only 17 (1.1%) earned
more than the Brazilian minimum wage; 8 (0.5%) earned more than a bank teller's starting
salary. The single best earner averaged US$310/day with a daily standard deviation of
US$2,560. No evidence of learning: win rates fell with persistence (30% profit after 1
day → 14% after 2–50 days → 8% after 101–200 days → 3% after 300+ days).
Source: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101

**Taiwan, equity day traders (Barber, Lee, Liu, Odean 2014).**
~450,000 day traders per year, 1992–2006. About 20% earn net profits in a given year,
but only **less than 1% (roughly 4,000 of 450,000) are predictably and reliably
profitable net of fees year after year** — and their skill persists (top-ranked traders
earn +37.9 bps/day net in the following year vs −28.9 bps/day for bottom-ranked), which
rules out pure luck for that tiny group.
Source: https://faculty.haas.berkeley.edu/odean/papers/day%20traders/The%20Cross-Section%20of%20Speculator%20Skill.pdf

**EU retail CFD accounts (ESMA 2018).**
National regulators' analyses across EU jurisdictions found **74–89% of retail CFD
accounts lose money**, with average losses of €1,600–€29,000 per investor. The UK FCA
independently estimated ~80% of active retail CFD accounts loss-making (£1.07bn/year
projected).
Sources: https://www.esma.europa.eu/sites/default/files/library/esma35-43-1000_additional_information_on_the_agreed_product_intervention_measures_relating_to_contracts_for_differences_and_binary_options.pdf
https://www.fca.org.uk/publication/consultation/cp18-38.pdf

## 2. Why most backtests die out-of-sample

This is measured, not folklore:

**The factor zoo / multiple testing problem (Harvey, Liu, Zhu 2016, Review of
Financial Studies).** They catalogue 316 published factors from 313 papers testing
cross-sectional return patterns. With that many trials, the usual t-statistic cutoff of
2.0 is meaningless; a newly proposed factor needs **t > 3.0**. Their conclusion:
"most claimed research findings in financial economics are likely false."
Source: https://people.duke.edu/~charvey/Research/Published_Papers/P118_and_the_cross.PDF

**Backtest overfitting math (Bailey, Borwein, López de Prado, Zhu 2014,
"Pseudo-Mathematics and Financial Charlatanism").** Trying just N=10 independent
strategy configurations on 5 years of data yields an expected maximum in-sample Sharpe
of ~1.57 even when every configuration has zero true edge. With 5 years of data, ~45
trials already guarantee an expected in-sample Sharpe ≈ 1 from noise alone. The
minimum backtest length required grows with the number of trials tried. A hold-out
split does NOT fix this because hold-out ignores how many configurations were tried.
Their follow-up (Deflated Sharpe Ratio, JPM 2014) provides the correction; their core
claim: *a backtest that does not report the number of trials attempted is worthless*.
Sources: https://www.davidhbailey.com/dhbpapers/backtest-pseudo.pdf
https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf

**Published edges decay by roughly half (McLean & Pontiff 2016, Journal of Finance).**
Tracking 97 academically documented predictors: portfolio returns are **26% lower
out-of-sample** than in-sample, and **58% lower post-publication**. Importantly,
predictability does not vanish entirely — statistically significant decay, not death.
Source: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2156623

## 3. What the survivors do differently

### 3.1 They exploit a structural counterparty or behavioral bias
Time-series momentum profits come from somewhere: Moskowitz, Ooi, Pedersen (2012) show
with CFTC positioning data that **speculators systematically profit from time-series
momentum at the expense of hedgers** — i.e., it is compensation for providing liquidity
against hedging demand. Edges tied to a durable economic mechanism (risk transfer,
behavioral under-reaction) persist; edges tied only to a curve-fit pattern don't.
Source: https://www.sciencedirect.com/science/article/pii/S0304405X11002613

### 3.2 Slow signals survive; fast signals get arbitraged away
Lempérière, Deremble, Seager, Potters, Bouchaud (CFM, 2014) test trend following over
**two centuries of data (back to 1800)** across four asset classes: overall t-stat ≈ 5
since 1960 and ≈ 10 since 1800, positive in every decade, 10-year rolling performance
never negative in 200 years. But their key nuance: **trends over months show no
degradation, while short trends (~3 days) have significantly withered since ~1990.**
Speed of edge predicts survival: slow edges persist, fast edges are competed away.
Source: https://arxiv.org/abs/1404.3274

### 3.3 Breadth, not depth
TSMOM (2012) is positive in **all 58 individual liquid futures instruments** across
equities, currencies, commodities, bonds — the diversification across dozens of markets
is what turns a modest per-instrument effect into a substantial diversified Sharpe.
Survivors trade many independent bets; losers bet repeatedly on one chart.

### 3.4 Robustness across parameters, not one magic setting
TSMOM results are "robust across a number of sub-samples, look-back periods and holding
periods" (MOP 2012); trend-following t-stats are "only weakly dependent" on lookback n
(Lempérière et al., Table 2). A real edge sits on a flat performance plateau. A sharp
optimum at one parameter value is a fingerprint of overfitting.

### 3.5 Costs decide everything at retail frequency
In the Brazil study, gross-to-net is exactly where traders die (the paper computes both;
persistence past 300 days nets 97% losers). In Taiwan, heavy day traders earn positive
gross abnormal returns that flip to net losses after commissions + the 30 bps sales tax
— only the extreme top percentile clears costs. The surviving <1% are those whose edge
exceeds costs with room to spare.

### 3.6 They count their trials
Harvey–Liu–Zhu (t > 3.0), Bailey–López de Prado (Deflated Sharpe Ratio, PBO/CSCV):
surviving research programs explicitly adjust for the number of configurations tried
and demand higher significance than naive single-test thresholds.

### 3.7 They expect decay and plan for less than half the backtest
McLean–Pontiff's 58% post-publication decline is the realistic haircut for any edge
that becomes widely known. Survivors size positions so the strategy still works after
returns halve; they don't leverage up to the backtest Sharpe.

## 4. Known survivor anomalies (measured longevity)

| Anomaly | Evidence | Longevity |
|---|---|---|
| Time-series momentum / trend | MOP 2012: 58/58 instruments positive | Robust since 1960+; centuries per CFM |
| Trend following (multi-month) | Lempérière 2014: t≈10 since 1800 | No degradation found |
| Short-term trends (~days) | Lempérière 2014 Fig. 8 | Significantly decayed since 1990 |
| Academic cross-sectional factors | McLean–Pontiff 2016 | −58% post-publication but nonzero |

Note the asymmetry: what survives long-term is slow, broad, cost-light, and mechanically
explicable. What dies is fast, narrow, cost-heavy, and curve-fit. This matches the
failure data: retail day trading (fastest possible turnover, one market, full costs) has
the worst survival rate measured anywhere (<1% Taiwan, 3% Brazil).

## 5. Practical checklist for testing a strategy honestly

1. Log every trial (every config you backtest). If N is unknown, the result is uninterpretable (Bailey et al.).
2. Demand t > 3.0 (or deflated Sharpe > 0.95) rather than t > 2.0 (Harvey–Liu–Zhu).
3. Include realistic full costs: spread, commission, slippage, financing. Re-test at 1.5x assumed costs.
4. Check the parameter neighborhood: performance should degrade smoothly, not cliff-edge, around your settings.
5. Ask who loses money to you and why they keep doing it (hedger? behavioral under-reaction?). No answer = suspicion.
6. Walk-forward / combinatorial cross-validation (CSCV/PBO if available), never a single lucky hold-out split.
7. Haircut expected returns ~50–60% for decay/crowding before sizing (McLean–Pontiff).
8. Prefer breadth: many instruments/independent bets beat deeper optimization on one series.
9. Prefer slower signal horizons unless you have an explicit speed advantage (you don't, at retail).
10. Size for the halved-return world; a strategy that only works at full backtest Sharpe is sized to blow up.

## 6. Sources

- Chague, De-Losso, Giovannetti (2020), "Day Trading for a Living?" — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101
- Barber, Lee, Liu, Odean (2014), "The Cross-Section of Speculator Skill" — https://faculty.haas.berkeley.edu/odean/papers/day%20traders/The%20Cross-Section%20of%20Speculator%20Skill.pdf
- ESMA (2018), product intervention analysis on CFDs — https://www.esma.europa.eu/sites/default/files/library/esma35-43-1000_additional_information_on_the_agreed_product_intervention_measures_relating_to_contracts_for_differences_and_binary_options.pdf
- FCA CP18/38 — https://www.fca.org.uk/publication/consultation/cp18-38.pdf
- Harvey, Liu, Zhu (2016), "...and the Cross-Section of Expected Returns", RFS 29(1) — https://people.duke.edu/~charvey/Research/Published_Papers/P118_and_the_cross.PDF
- Bailey, Borwein, López de Prado, Zhu (2014), "Pseudo-Mathematics and Financial Charlatanism" — https://www.davidhbailey.com/dhbpapers/backtest-pseudo.pdf
- Bailey, Borwein, López de Prado, Zhu (2017), "The Probability of Backtest Overfitting" — https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
- Bailey, López de Prado (2014), "The Deflated Sharpe Ratio", JPM 40(5) — https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
- McLean, Pontiff (2016), "Does Academic Research Destroy Stock Return Predictability?", JF — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2156623
- Moskowitz, Ooi, Pedersen (2012), "Time Series Momentum", JFE 104(2) — https://www.sciencedirect.com/science/article/pii/S0304405X11002613
- Lempérière, Deremble, Seager, Potters, Bouchaud (2014), "Two Centuries of Trend Following" — https://arxiv.org/abs/1404.3274
