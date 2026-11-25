# Benchmark

A broad check of reckon against measured agent work and against other ways
of estimating. It is the reason for the changes in 0.2.0 and 0.3.0. Only
aggregate numbers are published; the task text is private.

Read the limits before quoting anything: one team's history on one
harness, mostly small requests, a design iterated on the same test set,
and a pre-registered accuracy criterion (median error under 50%) that
neither 0.2.0 (51%) nor 0.3.0 (56%) met.

## Contents

- Setup
- Headline results
- 0.3.0: the rescaled gut guess as the default
- 0.3.1: changes made after the benchmark
- Where the error comes from
- What helped and what did not
- Waits, human touches, splitting
- Cost of estimating
- Limits of this benchmark
- How to run the same check on your history

## Setup

- **Data:** 568 Claude Code sessions from one team over three weeks, on
  three product repos and an assistant workspace.
  - The unit is one request: one message to the agent, plus all the agent's
    work until the next message.
  - Session copies made by resuming or forking were removed.
  - Also removed:
    - requests under 3 rounds;
    - turns that only relay a teammate's result (218);
    - requests the agent handed to another agent (128).
  - That left about 570 requests. (Earlier versions of this file gave 567
    here and 570 elsewhere, and the same count, 1,133, for both the
    duplicates and the under-3-round requests. These counts come from the
    data preparation, not from the scoring output, and could not be
    re-checked, so the exact figures are left out.)
- **Split by time:**
  - History: 160 requests sampled from 4-18 November, used for
    calibration.
  - Test: 200 requests from 19-25 November, stratified by who asked and
    which repo.
  - Requests in the test set: a median of 7 rounds and 1.6 minutes of
    agent time; P90 29 rounds.
  - The 45 requests used to build 0.1.0 were excluded.
- **Blind estimates:**
  - Every estimator saw only:
    - the request;
    - the agent's previous message;
    - for reckon, the repo profile rebuilt at the commit the request
      started from.
  - Estimators had no tools.
  - Estimates ran in batches of 8 with Claude Sonnet 5.5.
  - A script scored them against the measured agent minutes.
- **Statistics:**
  - Bootstrap 95% intervals use 2,000 resamples.
  - Paired comparisons use the Wilcoxon signed-rank test on |log error|.
- **Criteria fixed before running:**
  - 80-95% of actuals at or below P90;
  - 40-65% at or below P50;
  - median absolute percentage error (MdAPE) under 50%;
  - significantly better than naive guesses and than always answering the
    history median;
  - Spearman rank correlation of at least 0.4;
  - estimating cost of at most 5% of the work.

## Headline results

Test set, 200 requests. "Rolling" means each request is calibrated on every
request finished before it, which is how the skill is used.

| Method | MdAPE [95% CI] | actual / P50 | <= P50 | <= P90 [95% CI] | Spearman |
|---|---|---|---|---|---|
| Naive "how long will it take you?" | 669% | 0.14 | 97% | - | 0.60 |
| Developer hours, the backlog habit | 5782% | 0.02 | 100% | - | 0.59 |
| eta skill script (elliotjlt) | 2420% | 0.04 | 100% | 100% | 0.27 |
| Planning skill, then a total | 1024% | 0.09 | 99% | - | 0.58 |
| Always the median of the last 40 | 69% [62-76] | 1.11 | 51% | 90% | 0.15 |
| Naive guess rescaled on the last 40 | 53% [46-59] | 1.18 | 45% | 88% | 0.60 |
| reckon 0.1.0, cold start | 296% [237-330] | 0.26 | 93% | 99% | 0.60 |
| reckon 0.1.0, calibrated, rolling | 62% [53-73] | 0.85 | 58% | 96% | 0.57 |
| **reckon 0.2.0, cold start** | 178% [157-221] | 0.37 | 87% | 98% | 0.61 |
| **reckon 0.2.0, calibrated, rolling** | **51% [44-60]** | **1.06** | **48%** | **87% [82-91]** | **0.58** |

An actual/P50 ratio below 1 means the estimate was too high.

Against the fixed criteria, reckon 0.2.0 with calibration:

| Criterion | Result | Pass |
|---|---|---|
| <= P90 in 80-95% | 86.5% | yes |
| <= P50 in 40-65% | 48% | yes |
| MdAPE under 50% (CI upper bound under 60%) | 51% (CI upper bound 60%) | no |
| Better than naive, developer hours, and the history median | p < 0.002 for all three | yes |
| Spearman of at least 0.4 | 0.58 | yes |
| Cost of at most 5% of the work | 4% batched; about 25% for one small request run on its own | only for larger requests (see below) |

**Verdict:** calibrated estimates are honest. They are almost unbiased, and
the P50 and P90 mean what they say. But single-request accuracy sits at
about +/-50%. It does not get tighter, because the speed per round alone
varies that much (see below).

**Honest comparison:** a naive minutes guess, rescaled on the same recent
history, did as well as reckon on minutes. There was no significant
difference (p = 0.64), and the two methods won about half the time each.
The gain comes from calibrating on measured history, not from counting
rounds. Rounds still earn their place:
- they give a decomposition someone can check;
- cold-start ranking is as good as naive;
- they separate counting error from speed error, so you can see which one
  to fix.

A geometric mean of the two calibrated methods scored 50.5% MdAPE with
Spearman 0.63. That difference is not significant (p = 0.16).

## 0.3.0: the rescaled gut guess as the default

The headline result showed that a naive guess, rescaled on recent history,
was as accurate as counting rounds. 0.3.0 makes that the default (quick
mode) and keeps rounds for plans, backlogs and splits (full mode). It was
run on the same 360 requests:
- Quick-mode guesses were made from the 0.3.0 skill text, with no repo
  profile, as in live use.
- For the 12.5% of test requests where the estimator chose full mode, the
  rounds came from the 0.2.0 run, which uses the same counting text.
- The cold-start ratios (0.12 and 0.6) were fitted on the history set
  only.

| Method (test set, 200 requests) | MdAPE [95% CI] | actual / P50 | <= P50 | <= P90 | Spearman |
|---|---|---|---|---|---|
| 0.2.0, calibrated, rolling | 51% [44-60] | 1.06 | 48% | 87% | 0.58 |
| Naive guess rescaled, rolling | 53% [46-59] | 1.18 | 45% | 88% | 0.60 |
| **0.3.0, calibrated, rolling** | **56% [46-64]** | **1.04** | **50%** | **88%** | **0.61** |
| 0.3.0, quick for every request | 56% | 1.04 | 51% | 88% | 0.61 |
| 0.3.0, rounds + guess for every request | 56% | 1.07 | 49% | 89% | 0.61 |
| 0.3.0, Claude Haiku 5.5 guesses | 59% | 1.17 | 48% | 87% | 0.55 |
| 0.2.0, cold start | 178% | 0.37 | 87% | 98% | 0.61 |
| **0.3.0, cold start** | **67% [54-74]** | **0.78** | **64%** | **97%** | 0.61 |

- **The MdAPE criterion is missed again.** 0.3.0 calibrated scored 56%
  [46-64] against the pre-registered "under 50%". Coverage (88% at or
  below P90, 50% at or below P50) and rank correlation (0.61) are inside
  their criteria. "Always the median of the last 40" scores 69%, so the
  method buys about 13 points of median error over a constant.
- **Calibrated accuracy is not distinguishable from 0.2.0.** 0.3.0
  against 0.2.0 gave p = 0.86, with each winning about half the requests.
  Against the naive rescaled guess, p = 0.67. The point estimate is 5
  points worse than 0.2.0 (56% against 51%).
- **Combining rounds with the guess added nothing here.** The full-mode
  requests were small: median 6 minutes and at most 22.
- **Cold start is much better.** The error was 67% instead of 178%, with
  97% of actuals at or below P90 (96.5% before rounding).
  - With the skill loaded, agents guessed about 8x their measured time.
    Sonnet and Haiku guessed alike: the history median ratio was 0.12 for
    both.
  - This prior is still one team's number.
- **Learning curve:**

  | Finished items | MdAPE | <= P90 |
  |---|---|---|
  | 0 | 67% | 97% |
  | 8 | 56% | 89% |
  | 40 | 54% | 89% |

- **Repeatability:** across three Sonnet runs on 40 requests, the guess
  varied by 13% (median coefficient of variation). Rounds varied by
  15-18%.
- **By group (0.3.0 / 0.2.0 / naive rescaled):**
  - Repo work: 50 / 45 / 46%.
  - Outside a repo: 57 / 60 / 55%.
  - Requests of 4 minutes or more: 41 / 47 / 51%.
  - Requests under 1 minute: 61 / 67 / 50%.
- **Cost:** see below. A single estimate is about half the time and
  tokens of 0.2.0.

## 0.3.1: changes made after the benchmark

0.3.1 fixes the script and the instructions; the benchmark was **not**
re-run, and every number in this file is from 0.3.0 or earlier. Changes
that could move the numbers if it were re-run:

- **Window per method.** The 40-item window is now taken per method
  (items with a guess; items with estimated rounds), and the same-model
  rule is applied inside that window. 0.3.0 filtered by model first, over
  all finished items, then took 40. On a history where every item has a
  guess and one model dominates, as here, the two give nearly the same
  items, but not always exactly the same.
- **Measured speed counts as calibrated rounds** when choosing between
  rounds and guess. Whether that state occurred in the benchmark runs
  cannot be told from the scoring output.
- **Risk multipliers are now notes.** The round counts scored here
  included multipliers on 32% of modules (0.2.0 text). New full-mode round
  counts will be lower; the history ratio absorbs a consistent shift once
  calibrated, and at cold start the reported number comes from the guess.
- **A P90 that is not above the P50** now falls back to the cold spread.
  This needs identical ratios and should not occur on real history.
- **Per-request actuals.** The benchmark already scored single requests;
  the script's `actual` now measures the same unit.

## Where the error comes from

| Measure | 0.1.0 | 0.2.0 |
|---|---|---|
| Estimated rounds, median (actual: 7) | 31 | 21 |
| Actual / estimated rounds, geometric mean | 0.30 | 0.42 |
| Spearman, estimated vs actual rounds | 0.64 | 0.66 |
| Modules given a risk multiplier | 56% | 32% |

- **Actual speed:** 0.16 minutes per round at the median, 0.34 at P90. The
  0.2 cold value is close.
- **Speed alone:** even with the exact round count, a fixed 0.2 min/round
  gives 46% MdAPE. Speed per round varies that much from request to
  request, so about 45-50% is the floor for single requests with this
  approach.
- **Counting error:** agents overcount rounds for single requests. In
  0.1.0 the main cause was anchoring on a reference class of whole
  multi-request sessions (median 40). Most requests are follow-ups that
  take under 10 rounds. Calibration absorbs the remaining overcount after
  8 finished items.
- **Speed drift:**
  - The calibration ratio (actual minutes over rounds x 0.2) was 1.17 for
    the first 40 history items and 0.22 for the last 40.
  - By model, the median ratio was:
    - claude-sonnet-5-5: 0.24
    - claude-opus-5-5: 0.42
    - claude-sonnet-5: 0.54
    - deepseek-v4.1-flash: 1.21
  - Calibrating on all history is therefore biased. 0.2.0 uses the 40 most
    recent items, and same-model items once 8 exist.
- **Public tasks:** 160 SWE-bench tasks solved by a shell-only agent
  (GPT-5.2 / GPT-5-mini, one command per round; JetBrains Research
  trajectories).
  - 0.1.0 estimated a median of 16 rounds against an actual 11.
  - 76% of estimates were within 2x.
  - Ranking was weak (Spearman 0.30), because those tasks vary little in
    size.

## What helped and what did not

Ablations of 0.1.0 on 60 test requests (cold start, same estimator):

| Variant | Median rounds | MdAPE | Spearman |
|---|---|---|---|
| full 0.1.0 | 46.5 | 293% | 0.75 |
| without the whole-task reference class | 39 | 235% | 0.78 |
| without the risk list | 36 | 235% | 0.74 |
| without the repo profile | 41 | 310% | 0.78 |

The repo profile's effect (293% against 310% on 60 requests) is within
noise. The whole-task reference class and liberal risk multipliers
inflated counts. 0.2.0 added a single-request reference class and required
evidence before applying a risk; 0.3.1 turns the multipliers into notes.

**Learning curve (0.2.0, most recent k items):**

| k | MdAPE | <= P50 | <= P90 |
|---|---|---|---|
| 0 | 178% | 87% | 98% |
| 8 | 56% | 51% | 88% |
| 20 | 52% | 46% | 88% |
| 40 | 51% | 48% | 86% |

Calibration pays off after 8 finished tasks.

**Same check on history only (no test data, items 11-160, rolling):**
- 0.1.0: MdAPE 73%, actual/P50 0.57.
- 0.2.0: MdAPE 60%, actual/P50 0.94.

**Repeatability:** each of 40 requests was estimated 3 times by each model.
- The round count varied by 15-18% (median coefficient of variation).
- Calibrated MdAPE per run:
  - Claude Haiku 5.5: 54-58%
  - Claude Sonnet 5.5: 53-64%
  - Claude Opus 5.5: 46-58%
- The model used to estimate matters little, so the cheapest one is fine.

**By group (0.2.0, rolling):**
- Work in a git repo: 45% MdAPE. Assistant operations outside a repo: 60%.
- Delegated by an agent: 46%. Typed by a person: 60%.
- By estimated size: L 41%, XS 54%.

## Waits, human touches, splitting

- **Waits (C):**
  - Estimators listed a wait on 74 requests. On 66% of those, no wait
    happened.
  - Total estimated C was 992 minutes against 129 actual, 7.7x too high.
  - With the 0.2.0 ratio-of-sums correction it was 152 minutes against
    129.
  - The 0.1.0 median-of-ratios correction was 0, because most ratios were
    0.
- **Human touches (B):** only a proxy could be checked. The proxy asks
  whether the agent ended a person's request by asking a question or for
  approval. Predicting a touch had 49% precision and 41% recall. B remains
  low confidence; record actual human turns to calibrate it.
- **Split rule:** no request in the data took more than 60 minutes, so the
  2-hour split rule could not be tested here.

## Cost of estimating

- **The work itself:** a test request processed a median of 740,000 tokens
  (P25 490,000, P75 1.65 million), 96% of them cache reads.
- **Batched, no tools:**
  - 0.2.0 full mode: about 2,900 tokens per request, 0.011 USD at API
    prices.
  - 0.3.0 quick mode: about 2,000 tokens, 0.006 USD. With Haiku, 0.0004
    USD.
  - Either way it is under 0.5% of the work's tokens.
- **Real single estimate, 0.3.0 quick mode:** the same 8 requests, in a
  fresh session.
  - 13 seconds each (12-15), 4 agent turns.
  - 90,000-125,000 tokens (median 115,000), about 0.12 USD at API prices.
  - That is 4-23% of the work's tokens (median 15%) and 3-34% of its time
    (median 13%).
  - Most of those tokens are the fresh session's own system prompt. Inside
    an ongoing conversation the added cost is the skill text and one
    script call.
- **Real single estimate, 0.1.0 full workflow:** the skill loaded, with
  file and shell tools, on 8 requests.
  - Median 27 seconds and 6 agent turns. The longest took 85 seconds.
  - Tokens processed were 90,000-285,000 (median about 200,000), mostly
    cached system prompt. That is 7-51% of the tokens of the work being
    estimated, with a median of about 21%.
  - Time was 7-170% of the work's time, with a median of about 25%.
- 0.3.0 therefore uses quick mode for single requests and keeps the full
  workflow for plans, backlogs and possible splits.

## Limits of this benchmark

- **One team, one harness:**
  - The minute numbers come from one team on Claude Code.
  - Other teams and agents should calibrate on their own history; the
    learning curve says 8 tasks is enough to start.
  - The cold-start ratios (0.12 and 0.6) are that team's, with two Claude
    models. They are provisional for everyone else.
- **Small requests:**
  - Most requests are small, with a median of 1.6 agent minutes.
  - No request took more than 60 minutes, and the requests that got full
    mode took 22 minutes at most. Full mode, the 2-hour split rule and
    anything multi-hour are untested.
- **The design was iterated on the same test set:**
  - The window and model rules of 0.2.0 were chosen after seeing the
    drift in it, and 0.3.0's quick-mode default after seeing that the
    rescaled naive guess tied 0.2.0 on it. Only the cold-start ratios were
    fitted on the history set alone.
  - The history-only check above points the same way, but the results
    here are optimistic to an unknown degree.
  - A prospective check on requests made after a freeze has not been run.
- **0.1.0's first validation did not replicate:** its 31% cold-start error
  on 20 whole tasks (evidence.md) became 296% on these 200 requests.
- **Baselines:** the planning and naive baselines were run as prompts with
  the same batch format. The eta baseline used its own script at the
  matching commit.

## How to run the same check on your history

1. Record estimates and actuals with the skill for a few weeks. Quick mode
   records `guess_min`; full mode records both rounds and the guess.
2. Run `python3 <skill-dir>/scripts/reckon.py backtest .reckon/history.jsonl`.
   - The check is leave-one-out.
   - It uses the same window and model rules as live use (0.3.1 rules;
     see calibration.md).
   - It reports quick mode (guess) and full mode (rounds) side by side, so
     you can see which works better on your history.
3. Pass when:
   - at least 80% of actuals are at or below P90;
   - MdAPE is under about 50%;
   - the result beats a naive guess you record next to each estimate.
