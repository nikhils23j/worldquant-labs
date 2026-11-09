# RiskLab: interview notes

## 1. What it is

A Monte Carlo option-pricing lab in Python/numpy that measures how much each variance-reduction trick actually buys:
antithetics, control variates, and scrambled Sobol quasi-Monte Carlo with a Brownian bridge. It also compares three
ways to compute Greeks, prices barrier options with a Brownian-bridge correction, and runs a Heston model. Everything is
checked against closed forms or independent QuantLib engines.

## 2. How it works: key decisions and why

- **Exact GBM in log space, streamed in batches.** No Euler error for GBM, prices stay positive, and memory stays flat
  (~51 MiB for a million 252-date paths) because only running sums are kept, never the path matrix.
- **Welford moment aggregation** avoids catastrophic cancellation in E[X²] − E[X]² over millions of samples.
- **Control variate:** the geometric-Asian payoff, whose price has a closed form. The coefficient β is fitted on an
  *independent* pilot so the estimator stays unbiased, and the pilot counts toward the path budget. Antithetic pairs
  (not individual paths) are the independent units in the standard error.
- **Brownian bridge for QMC.** Sobol's first coordinates are its best-distributed ones. The bridge spends coordinate 0 on
  W(T), coordinate 1 on W(T/2), and so on, so the good coordinates control the path's large-scale shape. Same points,
  very different results.
- **Randomised QMC for error bars.** One Sobol run has no meaningful sample variance, so I use 20–32 independent
  scramblings and measure RMSE across them. Estimates at each N are prefixes of one stream (Sobol prefixes of length 2^k are nets).
- **Greeks.** Pathwise differentiates the payoff along the path, which is low variance but needs a continuous payoff.
  Likelihood ratio differentiates the density: it handles digitals, but its vega weight sums over every time step.
  Bump-and-revalue uses common random numbers so the noise cancels in the difference.
- **Barrier correction.** Given two grid values on the safe side, a Brownian bridge crosses the barrier with probability
  exp(−2·a·b/(σ²Δt)). I multiply the payoff by the survival probability instead of sampling the crossing, which is exact for GBM.
- **Heston: full-truncation Euler.** It uses max(v, 0) in drift and diffusion. It's simple and known to be the
  least-biased of the simple fixes, and I measure the bias against QuantLib's semi-analytic price.
- **Independent checks.** QuantLib is only a reference: AnalyticEuropean, AnalyticDiscreteGeometricAsian,
  MCDiscreteArithmeticAP, AnalyticBarrier and AnalyticHeston. The QuantLib date grid is rescaled so its fixings match my model exactly.

## 3. Key results (measured, see RESULTS.md)

- Original validation reproduces: 40 analytic cases vs QuantLib (max difference 1.1e-13), and 4 arithmetic-Asian cases
  within 0.82 combined SE. Over nine million-path runs, max 95% half-width was **0.00118** and peak RSS **51.4 MiB**
  (previously 0.00118 at 54.9 MiB).
- Equal 100k-path budget, 20 seeds: antithetic 2.8x, control **737x**, antithetic+control 824x variance reduction.
- QMC, arithmetic Asian, 252 dates, 20 replications at 65,536 paths: Sobol without bridge 29x, Sobol + bridge 2,529x,
  Sobol + bridge + control **17,216x** variance reduction vs plain MC (8,702x after accounting for CPU time).
  RMSE 4.27e-2 → 3.25e-4.
- Greeks (200k paths): all three estimators agree with QuantLib's European delta/vega within their CIs. LR vega was
  ~24x noisier than pathwise for the 252-date Asian. For a digital, pathwise gives 0 (wrong) and LR beats CRN bumping by 6x in CI width.
- Barrier (B=90 down-and-out): naive 52-step MC overprices by 1.12 (24 SE); the bridge-corrected estimate is within 0.5 SE.
  Every corrected cell over 3 cases × 4 step counts is within 1.7 SE of QuantLib.
- Heston with the Feller condition violated: 12-step Euler is off by +0.79 (37 SE), with 45% of steps hitting v < 0;
  at 252 steps the error is 1.8 SE. With Feller satisfied, every cell is within 1.2 SE.

**What these do NOT show:** anything about pricing real products. The models aren't calibrated, and the intervals cover
Monte Carlo error only, not model error. The QMC gains are for one smooth ATM Asian and would shrink for kinked or
high-dimensional payoffs. Each RMSE comes from 20 replications, so it is itself uncertain by roughly ±15%. Heston uses
plain Euler, not a production scheme like Andersen QE.

## 4. STAR answer (~2 minutes)

**Situation.** I'd built a basic Monte Carlo pricer for Asian options, with AI pair-programming help, and the first
version already used antithetics and a control variate. But I couldn't say *how much* each technique mattered or where
they break, and in quant interviews that's the whole point.

**Task.** I set out to turn it into a lab: measure every variance-reduction method at an equal budget, add quasi-Monte
Carlo properly, compare Greek estimators, and handle cases where naive simulation is biased (barriers, Heston), with
every result checked against an independent library.

**Action.** I implemented scrambled Sobol with a Brownian-bridge path construction and ran an RMSE-vs-paths study with
20 independent scramblings per method. I added pathwise and likelihood-ratio Greeks next to the existing CRN bumps, a
Brownian-bridge barrier correction, and a full-truncation Heston scheme. I validated each one against QuantLib's
analytic engines and wrote tests for the statistical properties, such as the bridge's covariance equalling min(s, t).
The AI helped with a lot of the boilerplate. I made the design decisions and dug into anything that looked off.

**Result.** At 65,536 paths, Sobol plus bridge plus control variate cut the variance by about 17,000x versus plain MC.
That's the "10x smaller error bar means 100x fewer paths" idea in action. The surprises taught me the most. Sobol
without the bridge gave only 29x, so the construction matters more than the sequence. A naive barrier simulation was
still 11 standard errors off with daily monitoring, while the corrected one was right with just 4 steps.

## 5. Likely follow-ups

- **Why does the bridge help so much?** It concentrates the variance into the first few coordinates (low effective
  dimension), which are exactly the ones Sobol fills most evenly.
- **Why fit β on an independent pilot?** Fitting β on the same sample makes the estimator slightly biased, O(1/N).
  A separate pilot keeps it unbiased, at the cost of a small budget I count.
- **How do you get error bars with QMC?** Randomised QMC. Independent scramblings each give an unbiased estimate, and
  their spread is the standard error.
- **When is pathwise wrong?** When the payoff is discontinuous (digitals, barriers): its derivative is zero almost
  everywhere, so pathwise misses the jump. LR or smoothing is needed there.
- **Why does LR vega get worse with more dates?** Its weight sums Σ[(Z²−1)/σ − Z√Δt] over all steps, so its variance grows roughly linearly with the step count.
- **Isn't the naive barrier just pricing a different contract?** Yes. It correctly prices *discrete* monitoring. The
  bias I report is relative to the continuously monitored contract in QuantLib.
- **What's the Feller condition?** 2κθ > ξ². When it holds, the CIR variance can't hit zero. When it fails, Euler steps
  go negative often, and truncation adds bias that shrinks only as Δt → 0.
- **What would you do next?** Andersen QE for Heston, multilevel Monte Carlo, Greeks under QMC, and a GPU/numba kernel.

## 6. Explain-cold checklist

- [ ] Why MC error ∝ 1/√N, and what "variance reduction 100x" means in paths
- [ ] Antithetic: why pairs are the independent unit; when it helps (monotone payoffs)
- [ ] Control variate: optimal β = Cov/Var, the independent pilot, why the geometric Asian works
- [ ] Sobol vs PRNG, scrambling, power-of-2 sample sizes, RQMC error bars
- [ ] Brownian-bridge construction (conditional mean/variance of the midpoint)
- [ ] Pathwise vs LR vs CRN bump: assumptions and variance behaviour
- [ ] Barrier crossing probability exp(−2ab/(σ²Δt)), and why multiplying by survival is unbiased
- [ ] Heston SDEs, Feller condition, full truncation
- [ ] Streaming vs materialising paths: memory numbers
- [ ] How each result is validated (closed form / QuantLib), and the claim limits
