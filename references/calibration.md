# Calibration

Every formula here works by hand. `scripts/reckon.py` only automates it.

## Contents

- Definitions
- Quick mode: rescaling the gut guess
- Choosing the method (full mode)
- Formulas
- Confidence
- History schema
- Recording actuals by hand
- Checking the method on your own history (backtest)

## Definitions

| Term | Meaning |
|---|---|
| `N` | the agent's gut guess of its own working minutes (`estimate.guess_min`) |
| `g` | `actual.agent_min / estimate.guess_min` for one finished item |
| `R` | estimated rounds for the whole task (modules after risk, plus integration) |
| `mpr` | minutes per round, agent time only |
| `ratio` | `actual.agent_min / (estimate.rounds x 0.2)` for one finished history item: actual time over the cold-start baseline. The fixed baseline keeps ratios independent of earlier calibration. |
| `agent_min` | time the agent spent thinking and running tools, excluding long waits and idle gaps |
| `wait_min` | time inside long-running shell commands (builds, CI watches, deploys) |
| `human_turns` | times a person had to write to the agent after the first request |

## Quick mode: rescaling the gut guess

Take the 40 most recent finished items that have a `guess_min`; once 8 of
them were done by the same model as the one that will do this task, use
only those. With at least 8 items:

```
A_p50 = N x g50        g50 = median of g
A_p90 = N x g90        g90 = 90th percentile of g
```

With fewer, use the cold values `g50 = 0.12` and `g90 = 0.6`, confidence
LOW. They come from the benchmark history only: with this skill loaded,
agents guessed about 8x their measured minutes, and Claude Sonnet and
Claude Haiku guessed alike. On the later test set they gave 63% median
error and covered 98% of actuals at P90. They are one team's numbers:
your own history replaces them after 8 tasks.

The method only works if the guess is made the same way every time: the
agent's plain answer for its own minutes, without reading code and without
correcting for known bias.

When an item has both a calibrated round estimate and a calibrated guess
(full mode), report the geometric mean of the two: `sqrt(a x b)` for P50
and for P90.

## Choosing the method (full mode)

Look only at the 40 most recent finished items (history is in time
order). When at least 8 finished items were done by the same model as the
one that will do this task (`actual.model`), use only those. Agent speed
drifts as models and harnesses change: in the benchmark the ratio fell from
1.17 to 0.22 within three weeks, and one model ran 5x slower per round than
another, so old or mixed history misleads. Then use the first that applies:

1. **Calibrated**: at least 5 finished items of the same type that have
   both an estimate and an actual. If the type has fewer, use all finished
   items with estimates when there are at least 8.
2. **Measured speed**: at least 10 measured items (with or without
   estimates) to compute `mpr`.
3. **Cold start**: no usable history.

## Formulas

Cold start:

```
A_p50 = R x 0.2
A_p90 = A_p50 x 3
```

Measured speed (`mpr50`, `mpr90` are the median and 90th percentile of
`agent_min / rounds` over measured items):

```
A_p50 = R x mpr50
A_p90 = R x mpr90 x 1.5      (the 1.5 covers round-count error)
```

Calibrated (`r50`, `r90` from the ratios of matching items; skip items
whose actual is 0):

```
A_p50 = R x 0.2 x r50
A_p90 = R x 0.2 x r90
```

Percentiles by hand: sort the values; the median is the middle one (mean
of the two middle ones for an even count); for the 90th percentile take the
value at position `ceil(0.9 x n)` counting from 1. With fewer than 10
values use the largest value as `r90`.

B and C:

```
B = sum of touch minutes (anchors.md), or human_turns_p50 x 3 min when
    history has measured human turns for this type
C = sum of known waits (wait-times table); when history has wait_min for
    this type, use its median as a floor. Once at least 5 finished items
    have an estimated C above 0, multiply C by
    c_ratio = sum(actual wait_min) / sum(estimated c_min) over the window.
    Use the ratio of sums, not a median of ratios: most estimated waits
    never happen, so per-item ratios are mostly 0. Agents overestimate
    waits (3.6x to 7.7x in the runs in evidence.md and benchmark.md).
```

Report `A+C` as the agent-side elapsed time and `B` as the person's
hands-on time. Never add idle time or another person's queue time into A,
B or C; mention it as a calendar note.

## Confidence

| Level | When |
|---|---|
| HIGH | calibrated on at least 10 items of the same type and `r90 / r50 <= 2` |
| MEDIUM | calibrated on fewer same-type items or on all types, or measured speed |
| LOW | cold start, or the task is vague (risk R6) |

## History schema

`history.jsonl` holds one JSON object per line. An estimate is appended
when it is made; its `actual` is filled in later.

```json
{"id": "2026-03-02-retry-email", "date": "2026-03-02", "title": "Stop duplicate email on retry",
 "type": "bugfix", "repo": "shop-api", "agent": "claude-code", "level": "quick",
 "estimate": {"rounds": 46, "guess_min": 60, "a_p50": 12, "a_p90": 29, "b_min": 20, "c_min": 8,
              "method": "rounds cold", "confidence": "LOW",
              "modules": [{"name": "idempotency key", "type": "bugfix", "size": "S", "rounds": 25, "risk": "R2"}]},
 "actual": {"agent_min": 5.5, "rounds": 44, "wait_min": 0, "idle_min": 0, "human_turns": 0,
            "wall_min": 5.5, "model": "claude-sonnet-5-5", "source": "claude-code", "sessions": ["3f2a9c1e"]}}
```

A quick-mode item has `"level": "quick"`, a `guess_min` and no `rounds` or
`modules`. Measured-only items (from `backfill`) have `"kind": "measured"` and no
`estimate`. Titles are short and free of secrets; set
`RECKON_HASH_TITLES=1` to store a hash instead of the title.

## Recording actuals by hand

Without the Claude Code adapter, or without Python:

1. Note the time when the agent starts and when it hands back each time.
2. `agent_min` = sum of the agent's working stretches, minus any single
   command that ran longer than 2 minutes (count that in `wait_min`) and
   any silent gap longer than 10 minutes (count that in `idle_min`).
3. `human_turns` = messages the person had to send after the first.
   Add `model` with the id of the model that did the work.
4. Edit the item's line in `history.jsonl` and add the `actual` object with
   `"source": "manual"`. Rounds are optional for manual actuals.

## Checking the method on your own history (backtest)

Use this before trusting calibration on a new team or model:

1. Pick about 20 finished tasks with measured actuals and a clear first
   request.
2. Estimate each one from its request alone, without looking at the
   actual (an agent that has not seen the actuals should do this).
3. For each task, calibrate using only the other tasks (leave-one-out) and
   compute its P50 and P90.
4. Pass when at least 80% of actuals are at or below P90, the median of
   `|actual - P50| / actual` is under 50%, and the result clearly beats a
   naive "how long will this take?" answer on the same tasks.

`python3 scripts/reckon.py backtest <file.jsonl>` runs steps 3 and 4 and
reports quick mode (`guess_min`) and full mode (`rounds`) side by side.
Each line needs `estimate.guess_min` or `estimate.rounds`, and
`actual.agent_min`; add
`"naive": {"minutes": N}` with the plain answer to compare against it.
It uses the same thresholds as live use (8 items for the guess; 5 of a type
or 8 overall for rounds), so with few items it reports cold-start numbers.
