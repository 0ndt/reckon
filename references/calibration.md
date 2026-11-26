# Calibration

Every formula here works by hand. `<skill-dir>/scripts/reckon.py` only
automates it, and its tests check that it follows this file.

## Contents

- Definitions
- The window
- Quick mode: rescaling the gut guess
- Full mode: rounds to minutes
- Rounds or guess: which number is reported
- Percentiles by hand, and ranges that are too narrow
- B and C
- Confidence
- History schema
- How actuals are measured
- Recording actuals by hand
- Checking the method on your own history (backtest)

## Definitions

| Term | Meaning |
|---|---|
| `N` | the agent's gut guess of its own working minutes (`estimate.guess_min`) |
| `g` | `actual.agent_min / estimate.guess_min` for one finished item |
| `R` | estimated rounds for the whole task (modules plus integration) |
| `mpr` | minutes per round, agent time only |
| `ratio` | `actual.agent_min / (estimate.rounds x 0.2)` for one finished item: actual time over the cold-start baseline. The fixed baseline keeps ratios independent of earlier calibration. |
| finished item | a history line whose `actual.agent_min` is above 0 |
| `agent_min` | time the agent spent thinking and running tools, excluding long waits and idle gaps |
| `wait_min` | time inside long-running shell commands (builds, CI watches, deploys) |
| `human_turns` | times a person had to write to the agent after the first request |

## The window

History is kept in time order, oldest line first. Each method looks only
at the items it can use:

| Method | Items it can use |
|---|---|
| guess (`g50`, `g90`) | finished items that have `estimate.guess_min` |
| rounds ratio (`r50`, `r90`) | finished items that have `estimate.rounds` |
| measured speed (`mpr50`, `mpr90`) | finished items with at least 5 measured `actual.rounds` |

For each method separately:

1. Take the **40 most recent** items that method can use.
2. If at least **8 of those 40** were done by the same model as the one
   that will do this task (`actual.model`), keep only those. Otherwise,
   or when the model is unknown, keep all 40.

Agent speed drifts as models and harnesses change: in the benchmark the
rounds ratio fell from 1.17 to 0.22 within three weeks, and one model ran
5x slower per round than another, so old or mixed history misleads.
Measured-only items (from `backfill`) never push estimates out of the
guess window, because they are not in it.

## Quick mode: rescaling the gut guess

With at least 8 items in the guess window:

```
A_p50 = N x g50        g50 = median of g
A_p90 = N x g90        g90 = 90th percentile of g
```

With fewer, use the cold values `g50 = 0.09` and `g90 = 0.45`, confidence
LOW. With this skill loaded, agents guessed about 11x their measured
minutes. The values are provisional, from one team's history on one
harness:
- History-only fits ranged from 0.07 to 0.13 across two models, two skill
  texts and two sessions, and the later test set wanted about 25% less
  than the history fit every time; 0.09 is a rounded value across those
  runs, chosen after seeing test results (benchmark.md, "0.3.1 re-run").
- With the 0.3.1 text, Claude Sonnet gave a 66% median error and Claude
  Haiku 86% (estimates still about 1.5x too high), with 96-97% of actuals
  at or below P90.
- Expect a cold P50 to be off by up to 1.7x. Your own history replaces
  these values after 8 finished items.

The method only works if the guess is made the same way every time: the
agent's plain answer for its own minutes, without reading code and without
correcting for known bias. A guess made for a different agent is about the
wrong speed: record it with confidence LOW.

## Full mode: rounds to minutes

Use the first state that applies to the rounds windows:

1. **Ratio** (the script calls it `calibrated`): the rounds-ratio window
   holds at least 5 items of the same task type; if not, at least 8 items
   of any type.

   ```
   A_p50 = R x 0.2 x r50
   A_p90 = R x 0.2 x r90
   ```

2. **Speed**: the measured-speed window holds at least 10 items.

   ```
   A_p50 = R x mpr50
   A_p90 = R x mpr90 x 1.5      (the 1.5 covers round-count error)
   ```

3. **Cold**: neither.

   ```
   A_p50 = R x 0.2
   A_p90 = A_p50 x 3
   ```

## Rounds or guess: which number is reported

Full mode records both the rounds and the gut guess. "Calibrated" for
rounds means the ratio or the speed state above; for the guess it means 8
items in the guess window. The benchmark's cold rescaled guess beat cold
rounds by a wide margin (66-86% against 178% median error), so at cold start
the guess is the estimate and the rounds are shown as a cross-check only.

| Rounds         | Guess      | Reported A                |
|----------------|------------|---------------------------|
| none           | any        | guess                     |
| any            | none       | rounds                    |
| cold           | cold       | guess                     |
| cold           | calibrated | guess                     |
| speed or ratio | cold       | rounds                    |
| speed or ratio | calibrated | geometric mean of the two |

The geometric mean is `sqrt(a x b)`, for P50 and for P90. The script
prints which row applied as `method` (`record`) or `used` (`calibrate`):
`guess cold`, `guess calibrated`, `rounds cold`, `rounds speed`,
`rounds calibrated` or `rounds+guess`. Report what it prints; do not
replace it with a hand calculation from another row.

## Percentiles by hand, and ranges that are too narrow

Sort the values; the median is the middle one (mean of the two middle
ones for an even count); for the 90th percentile take the value at
position `ceil(0.9 x n)` counting from 1. With fewer than 10 values use
the largest value.

If the 90th percentile is not above the median (few items, or all with
the same ratio), there is no usable range. Keep the P50, set the P90 to
the P50 times the cold-start spread (x5 for the guess, which is 0.45 /
0.09; x3 for rounds), and report confidence LOW, saying the history is
too uniform to give a range. The script does this and prints a `note`.

## B and C

```
B = sum of the per-touch minutes in anchors.md for each point where the
    person must act.
    human_turns_p50 (printed by `calibrate --type T` once history has 3
    items of that type) is a cross-check on the count only: if it is
    higher than the number of touches you listed, add 3 minutes for each
    missing turn.
C = sum of known waits (wait-times table); when history has wait_min for
    this type, use its median (`wait_floor_min`) as a floor.
    c_ratio = sum(actual wait_min) / sum(estimated c_min) over the 40 most
    recent finished items that have an estimated C, quick or full mode,
    once at least 5 of them estimated a C above 0.
    Report C x c_ratio. `record --c-min` prints it as `c_corrected`; the
    stored `c_min` stays as estimated.
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
| HIGH | rounds ratio from at least 10 items of the same type with `r90 / r50 <= 2` (reported from rounds, or rounds combined with a calibrated guess) |
| MEDIUM | quick mode with a calibrated guess (8 or more items); rounds ratio from fewer same-type items or from all types; measured speed |
| LOW | cold start; a guess made for a different agent; a range that was too narrow (see above); numbers entered by hand without a stated confidence; or the task is vague (risk note R6) |

Quick mode never reports HIGH: the guess window is not split by task
type.

## History schema

`history.jsonl` holds one JSON object per line, oldest first. An estimate
is appended when it is made; its `actual` is filled in later. These two
lines were written by the script (`record` then `actual`), a quick-mode
item at cold start and a full-mode item not yet finished:

```json
{"id": "2026-11-25-stop-duplicate-email-on-retry", "date": "2026-11-25", "ts": "2026-11-25T08:06:19Z", "title": "Stop duplicate email on retry", "type": "bugfix", "repo": "shop-api", "agent": "example-agent", "level": "quick", "estimate": {"rounds": null, "guess_min": 20, "a_p50": 1.8, "a_p90": 9.0, "b_min": 0, "c_min": 0, "method": "guess cold", "confidence": "LOW", "modules": [], "model": "example-model-1"}, "actual": {"agent_min": 3.5, "rounds": 14, "wait_min": 0, "idle_min": 0, "human_turns": 0, "wall_min": null, "source": "manual", "model": "example-model-1"}}
{"id": "2026-11-25-notification-preferences-and-email-diges", "date": "2026-11-25", "ts": "2026-11-25T08:06:19Z", "title": "Notification preferences and email digest", "type": "feature", "repo": "shop-api", "agent": "example-agent", "level": "full", "estimate": {"rounds": 81, "guess_min": 240, "a_p50": 21.6, "a_p90": 108.0, "b_min": 50, "c_min": 30, "method": "guess cold", "confidence": "LOW", "modules": [{"name": "preferences table and API", "type": "migration", "size": "M", "rounds": 26, "risk": "R4"}, {"name": "daily digest job", "type": "integration", "size": "M", "rounds": 30, "risk": "R2"}, {"name": "settings screen", "type": "ui", "size": "S", "rounds": 14}], "model": "example-model-1"}}
```

Check: 20 x 0.09 = 1.8 and 20 x 0.45 = 9; 240 x 0.09 = 21.6 and
240 x 0.45 = 108 (the guess row of the table above; 81 rounds cold would be 16.2 and
48.6).

An actual measured from a transcript looks like this:

```json
"actual": {"source": "claude-code", "scope": "request", "start": "2026-03-10T09:35", "model": "example-model-1", "rounds": 3, "agent_min": 1.0, "wait_min": 0.0, "idle_min": 0.0, "human_turns": 0, "human_gap_min": 0.0, "wall_min": 1.0, "sessions": ["f1x7ure0"]}
```

- `ts` is the UTC time the estimate was made. `actual` uses it to find
  the request to measure, so include it in lines written by hand.
- `method: "manual"` marks a range entered with `--a-p50` instead of
  computed.
- `skill_version` is the `metadata.version` of the SKILL.md the estimate
  was made with (since 0.4.1). The guess level can move when the skill
  text changes, so this tells which ratios came from which text. By hand,
  copy it from SKILL.md; lines without it are older.
- `scope` is `request` or `session` (see the next section); `start` is in
  local time.
- Measured-only items (from `backfill`) have `"kind": "measured"` and no
  `estimate`; they feed measured speed only.
- Only `id`, `estimate.guess_min` (or `estimate.rounds`) and
  `actual.agent_min` are needed for calibration; a minimal by-hand line is
  in SKILL.md.
- With `RECKON_HASH_TITLES=1` the title is stored as a hash and the id is
  built from that hash, not from the title.
- A line that is not valid JSON is left out of calibration, reported with
  its line number, and never deleted or rewritten by the script.

## How actuals are measured

`actual <id>` measures **one request**, not the session. It reads the
session logs of Claude Code (default), OpenCode (`--agent opencode`, its
local SQLite database) or Codex CLI (`--agent codex`, its rollout files).
All three are turned into the same event list and measured by the same
code (`scripts/adapters/common.py`), so a round, a turn and `agent_min`
mean the same for each; per-agent notes are in each adapter's docstring.
For one request:

- It starts at the estimate's `ts` and stops at the next human prompt.
- If the agent called no tool between `ts` and the end of that turn (it
  reported the estimate and stopped), the request is the **next** turn:
  the person's go-ahead and the work that follows it.
- So `record` must be the last tool call of a turn that only estimates.
  A tool call after it (writing a report file, reading more code) makes
  that tail the measured request and the work turn is missed. Full mode
  therefore runs `calibrate`, writes its report, and calls `record` last.
- `--turns N` also counts the N turns after that, for follow-up prompts
  that belong to the same request (answers, corrections). Run `actual`
  again to replace an earlier measurement.
- `--session <id-prefix>` picks the session; the default is the newest
  session of this project with agent work after `ts`. `sessions` lists
  them with their number of human prompts.
- `--whole-session` measures every turn of the session. Use it only when
  the whole session was this one task. An estimate without `ts` (made by
  0.3.0 or written by hand without it) can only be measured this way; the
  script then warns when the session holds more than one human prompt.

Inside the measured part:

- a shell or monitor call that runs longer than 2 minutes is `wait_min`;
- a silent gap longer than 10 minutes that no such call explains is
  `idle_min` and is dropped;
- everything else is `agent_min`.

Two things these rules count imperfectly:

- **Subagents.** A subagent's own rounds are not counted. While the main
  agent waits for one, that time counts as `agent_min` up to 10 minutes
  per silent gap and as `idle_min` above it, so an orchestrating session
  has a cliff at 10 minutes. For work done mostly by subagents, record the
  actual by hand.
- **Background shell commands** return at once, so they are never a wait.
  Time the agent spends polling counts as `agent_min`.

`backfill` adds whole past sessions with at least 5 rounds as
measured-only items (shorter sessions are skipped) and keeps the file in
start-time order.

## Recording actuals by hand

For an agent without an adapter, or without Python:

1. Note the time when the agent starts on the request and when it hands
   back each time.
2. `agent_min` = sum of the agent's working stretches for this request
   only, minus any single command that ran longer than 2 minutes (count
   that in `wait_min`) and any silent gap longer than 10 minutes (count
   that in `idle_min`).
3. `human_turns` = messages the person had to send after the first.
   Add `model` with the id of the model that did the work.
4. Either run `actual <id> --agent-min <minutes> [--wait-min W]
   [--human-turns H] [--model M]`, or edit the item's line in
   `history.jsonl` and add the `actual` object with `"source": "manual"`.
   Rounds are optional for manual actuals. Keep each item on one line.

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
   naive "how long will this take?" answer on the same tasks. (reckon's
   own benchmark missed the 50% mark: see benchmark.md.)

`python3 <skill-dir>/scripts/reckon.py backtest <file.jsonl>` runs steps 3
and 4 and reports quick mode (`guess_min`) and full mode (`rounds`) side
by side. Each line needs `estimate.guess_min` or `estimate.rounds`, and
`actual.agent_min`; add `"naive": {"minutes": N}` with the plain answer to
compare against it. It uses the same windows and thresholds as live use,
so with few items it reports cold-start numbers.
