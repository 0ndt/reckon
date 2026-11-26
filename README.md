# reckon

**Realistic Estimates, Calibrated on Known Outcomes, Not man-days.**

[![tests](https://github.com/0ndt/reckon/actions/workflows/tests.yml/badge.svg)](https://github.com/0ndt/reckon/actions/workflows/tests.yml)

An [Agent Skill](https://agentskills.io) that sizes software work done by
AI coding agents the way that work actually behaves: in agent minutes,
split into agent time, human touch time and wait time, reported as P50/P90
ranges and corrected by what past tasks really took.

## Try it on work you have already done

Before installing anything, see what reckon would have said about your own
recent requests. From inside a repository you have worked on with Claude
Code:

```
python3 <skill-dir>/scripts/replay.py            # 12 recent requests, one model call
python3 <skill-dir>/scripts/replay.py --list     # only show which requests it would use
python3 <skill-dir>/scripts/replay.py --agent opencode   # or --agent codex: replay those sessions
```

It picks your 12 most recent finished requests from the local transcripts,
asks the agent for its gut guess on all of them in one batched call, and
puts side by side, for each request:

- how long it really took (agent minutes, measured from the transcript),
- the agent's raw answer to "how long will this take you?",
- reckon at cold start (no history of yours yet),
- reckon once calibrated on your history (leave-one-out over the 12),

with the median error, how often the actual landed at or below P50 and
P90, and what the estimating cost: seconds, tokens and dollars, in total,
per request, and as a share of the work it sized. A run takes seconds and
costs a few cents. It writes a report to `.reckon/replay/` and nothing to
the history. Example from one real run (request text removed):

| | Median error | Actual / P50 | At or below P50 | At or below P90 |
|---|---|---|---|---|
| Agent's raw answer | 341% | 0.19 | 92% | n/a |
| reckon, cold start | 75% | 2.11 | 25% | 83% |
| reckon, calibrated | 51% | 0.82 | 50% | 83% |

| Cost of estimating 12 requests | Total | Per request | Share of the work |
|---|---|---|---|
| Time | 6 s | 0.5 s | 0.1% of 106 agent minutes |
| Tokens | 8,182 | 681 | 0.01% |
| Dollars (API prices) | 0.023 | 0.002 | |

It is a quick look, not the benchmark: the estimator sees only the request
text (not the repository), 12 requests move a lot from one sample to the
next, and the request text is sent to the model through your own `claude`
CLI. Options: `-n 6` (3-30 requests), `--model haiku`, `--any-project`,
`--hide-text`. With another agent, `--prompt-only` prints the prompt and
`--guesses file.jsonl` scores the answers you got back.

## Why the name

*To reckon* means to estimate or work something out ("I reckon it'll take
an hour"). Sailors used **dead reckoning** to find a ship's position from
a known speed and the time travelled, with no landmark in sight, and
corrected the course at every fix. This skill does the same for agent
work: it takes the agent's own first reckoning (its gut guess), corrects
it by how far off past guesses turned out to be, and corrects again every
time a real outcome comes in. Since 0.3.0 that rescaled guess is the
default; counting rounds and converting them with measured speed is kept
for large work that needs a breakdown.

The letters spell out the method:

| Letter | Stands for | What the skill does |
|---|---|---|
| **R** | Realistic | estimates in agent units, not human timelines |
| **E** | Estimates | agent time is always a P50/P90 range with a confidence level |
| **C** | Calibrated | scales by the ratio of actual to estimated time on past tasks |
| **K** | Known | uses measured history once there is some; before 8 finished tasks it has no known outcomes of yours and falls back to a provisional prior from one team's benchmark, marked LOW |
| **O** | Outcomes | records what each task really took, automatically when it can |
| **N** | Not man-days | never answers in man-days, man-hours or story points; work that people will do is out of scope |

## The problem

Ask an agent how long a task will take and it answers like a human
developer would: "2-3 hours", "1 day". Agents learned those numbers from
human-written text, and they overestimate agent-built work several times
over. In the benchmark below, a plain "how long will this take?" answer
was **about 7x too high** at the median. The opposite mistake also happens: the
agent's own minutes are tiny, but a person still has to review, answer and
test, and builds, CI and deploys still take their time.

## What you get

Quick mode, the default for a single request:

```
About 4-8 minutes of agent time (P50-P90, calibrated on 12 past tasks).
Needs your review of the diff, about 5 minutes.
```

Full mode, for a multi-part plan or anything whose quick P90 is over 2
hours. It adds a breakdown someone can check and a proposed split; it
does not make the number more accurate. This is the script's real output
on an empty history (`--rounds 81 --guess-min 240`), where the rescaled
guess gives the number and the rounds are a cross-check:

```
| # | Module                        | Type        | Size | Rounds | Risk note |
|---|-------------------------------|-------------|------|--------|-----------|
| 1 | Preferences table and API     | migration   | M    | 26     | R4        |
| 2 | Daily digest job              | integration | M    | 30     | R2        |
| 3 | Settings screen               | ui          | S    | 14     | -         |
|   | Integration (+15%)            |             |      | 11     |           |

| Bucket            | P50    | P90     |
|-------------------|--------|---------|
| A. Agent time     | 29 min | 2h 24m  |
| B. Human touch    | 50 min |         |
| C. Wait           | 30 min |         |
| A+C elapsed       | 59 min | 2h 54m  |
Method: guess cold · Confidence: LOW · Split proposed: 3 pieces · Top risk: provider batching limits
```

## How it works

1. **Quick mode by default.** The agent gives its plain gut answer: "how
   many minutes of my own time will this take?" It does not read code and
   does not correct the number itself. The script multiplies it by the
   median and 90th percentile of actual/guess over the 40 most recent
   finished tasks that have a guess, done by the same model once 8 of
   those are. Agent guesses are consistently several times too high, so a
   measured ratio fixes most of the error. Cold start uses 0.09 and 0.45:
   a provisional prior from one team and two Claude models that can be
   off by up to 1.7x, replaced by your own history after 8 tasks. A
   backlog is the same thing N times: one guess per item, recorded in one
   `record --batch` call.
2. **Full mode when structure matters:** a quick P90 over 2 hours; a
   multi-part plan or spec that needs a breakdown or a split; or a request
   for a breakdown.
   - **Profile once, reuse.** A 1-2k token repo profile is cached in
     `.reckon/profile.md`, stamped with the commit, and refreshed only
     after 30 changed files or a manifest or CI change. It is built from
     what already exists: AGENTS.md,
     CLAUDE.md, README, docs and ADRs, then code indexes such as
     [codegraph](https://github.com/colbymchenry/codegraph) and
     [graphify](https://github.com/safishamsi/graphify), then `rg`,
     `ctags`, `ast-grep` and `git log`. It never installs tools or builds
     indexes.
   - **Count rounds.** Modules get a type and a size from fixed anchors,
     plus 15% for integration. Risks are notes for the report; they do not
     change the number. The total is sanity-checked against measured
     reference classes, and anything over 2 hours at P90 gets a proposed
     split.
   - **Which number is reported:**

     | Rounds | Guess | Reported agent time |
     |---|---|---|
     | cold | cold or calibrated | the rescaled guess |
     | calibrated (history ratio or measured speed) | cold | rounds |
     | calibrated | calibrated | geometric mean of the two |

     The full table lives in
     [references/calibration.md](references/calibration.md), and the
     script follows it.
3. **Add people and waiting.** Human touch points and known waits are
   reported separately; calendar time is driven by them, not by the agent.
4. **Record outcomes.** At the end of the turn that did the work, the
   actual is measured from the agent's session log (Claude Code, OpenCode
   or Codex CLI) or
   entered by hand. It measures the one request the estimate was for, from
   the time the estimate was recorded to the next human prompt, not the
   whole session.

## Results

Two checks, both with estimates made blind (the estimating agent never saw
the outcome) and scored against measured agent time (bucket A).

**Benchmark** ([references/benchmark.md](references/benchmark.md)):

- **Data:** 200 test requests and 160 calibration requests, split by time,
  from 568 Claude Code sessions.
- **Comparisons:** naive guesses, developer hours, the eta skill, a planning
  skill and the history median.
- **Each request:** calibrated only on the requests finished before it.

| Method | Median error | Actual / P50 | At or below P50 | At or below P90 |
|---|---|---|---|---|
| Plain "how long will this take?" | 669% | 0.14 | 97% | n/a |
| Developer hours | 5782% | 0.02 | 100% | n/a |
| Always the recent median | 69% | 1.11 | 51% | 90% |
| Naive guess rescaled on recent history | 53% | 1.18 | 45% | 88% |
| reckon 0.2.0 (rounds), cold start | 178% | 0.37 | 87% | 98% |
| reckon 0.2.0 (rounds), calibrated | 51% | 1.06 | 48% | 87% |
| reckon 0.3.0 (quick), cold start (prior 0.12) | 67% | 0.78 | 64% | 97% |
| reckon 0.3.0 (quick), calibrated | 56% | 1.04 | 50% | 88% |
| **reckon 0.3.2 (quick), cold start, Sonnet** | **66%** | **0.83** | **65%** | **97%** |
| **reckon 0.3.2 (quick), cold start, Haiku** | **86%** | **0.67** | **69%** | **96%** |
| **reckon 0.3.2 (quick), calibrated** | **51%** | **1.11** | **48%** | **88%** |

The 0.3.2 rows use guesses made with the 0.3.1/0.3.2 skill text (the
text is the same; 0.3.2 only changes the cold prior). Calibrated results
across repeated guess runs ranged from 51% to 55%, so 0.3.2 is not
better than 0.3.0 once calibrated (p = 0.53); it is the same.

Cost of one estimate:

| | Batched, per task | One task on its own |
|---|---|---|
| 0.2.0 | 2,900 tokens | 27 s, 200k tokens |
| 0.3.0 | 2,000 tokens (Haiku: 2,100 tokens at 1/15 the price) | 13 s, 115k tokens |
| 0.3.2 | 1,800 tokens (Haiku: 2,000 tokens) | not re-measured |

What this means:

- **The accuracy criterion set in advance was missed.** The target was a
  median error under 50%. 0.2.0 scored 51%, 0.3.0 56% (95% interval
  46-64%) and 0.3.2 51-55% across guess runs. The coverage criteria
  were met.
- **After 8 finished tasks:** calibrated estimates are almost unbiased, and
  P50/P90 mean what they say.
- **Single requests:** expect an error of about half the actual, either
  way. Speed per round varies that much on its own. Always answering "the
  recent median" scores 69%, so the method buys about 13 points.
- **0.3.0 vs 0.2.0:** not distinguishable once calibrated (p = 0.86),
  though the point estimate is 5 points worse. The cold start is far
  better (66-86% vs 178%), and an estimate costs about half.
- **Cold start is fragile.** The same 0.3.0 text guessed 19% higher in
  the afternoon than in the morning, and the 0.3.1 text made Haiku guess
  1.6x higher. Treat a cold estimate as a rough range until 8 tasks are
  recorded.
- **Cost against the 5% target:** batched, an estimate is under 0.5% of
  the work's tokens. One quick estimate run on its own in a fresh session
  cost a median 13% of the work's time and 15% of its tokens, because the
  median request is only 7 rounds and 1.6 minutes.
- **Where the accuracy comes from:** calibrating on measured history, not
  from counting rounds. A naive minutes guess rescaled the same way did as
  well, which is why 0.3.0 makes the rescaled guess the default.
- **Waits (C):** with the ratio-of-sums correction, 152 minutes were
  estimated against 129 actual, down from 7.7x too high.
- **Human touches (B):** only a proxy could be checked, and it was weak.

**First validation, 0.1.0:** 20 whole delegated tasks with the anchors
frozen. Cold start had a median error of 31%. This did **not** hold on the
larger set: the same version's cold-start error on the 200 benchmark
requests was 296%.

**Limits.** All of this was measured on one team's Claude Code history
over three weeks, with one harness. Most requests were small (median 1.6
agent minutes, none over 60), so full mode, the 2-hour split rule and
multi-hour work are untested. The design was iterated on the same test
set, so the numbers are optimistic to an unknown degree; the 0.3.2 cold
prior was also chosen after seeing test results. See "Limits of this
benchmark" in
[references/benchmark.md](references/benchmark.md).

Your models, tools and repos differ. Record actuals for a few tasks and run
`backtest` on your own history; it compares quick and full mode.

## Install

Copy the `reckon` folder into your agent's skills folder, for example:

| Agent | Folder |
|---|---|
| Claude Code (personal) | `~/.claude/skills/reckon/` |
| Claude Code (one project) | `.claude/skills/reckon/` |
| Other Agent Skills clients | their skills folder |

Then ask "how long will this take you?" or "estimate this backlog for
the agent". It is not meant for work that people will do; asked about
that, the skill says so instead of estimating.

## Requirements

- Any agent that can read and write files.
- Python 3.9+ is **optional** (standard library only). Without it, every
  formula is simple enough to apply by hand; only automatic transcript
  measurement is lost.
- Automatic measurement reads Claude Code transcripts, the OpenCode 2.x
  session database and Codex CLI rollout files (tested with OpenCode 2.2.5
  and Codex CLI 0.172.0). Other agents record actuals by hand.
- Git is optional; outside a repo the store is `~/.reckon/`.

## The `.reckon/` folder

```
.reckon/
  .gitignore     contains "*", so the folder ignores itself
  profile.md     cached repo profile, stamped with its commit
  sources.json   detected docs, indexes and tools
  history.jsonl  estimates and their actuals
  estimates/     one Markdown file per estimate
```

- It is created at the root of the main repository, so every git worktree
  shares one history.
- It ignores itself; your `.gitignore` is never edited. To share history
  with your team through git, add `!.gitignore` and `!history.jsonl` to
  `.reckon/.gitignore`.
- Outside git it is `~/.reckon/`. `RECKON_HOME=/some/path` moves it.
- The first time it is created, the script says so and the agent tells
  you where it is.
- What is stored, all of it local; nothing is sent anywhere:
  - `history.jsonl`: per estimate, an id built from the date and the
    title, the short title, task type, the repo folder name, agent name,
    model id, the time of the estimate, the numbers, and module names in
    full mode; per actual, minutes, round counts, the start time, the
    model id and the first 8 characters of the session id.
  - `profile.md` and `sources.json`: file counts, folder names, file
    types, build and CI file names, script commands, the most-changed file
    paths, the branch name and commit hash, and the agent's notes.
  - `estimates/*.md`: the full-mode reports the agent writes.
- `RECKON_HASH_TITLES=1` stores a hash instead of the title and builds
  the id from that hash. It does not hide the repo folder name, module
  names or anything in `profile.md` and `estimates/`.
- `history.jsonl` can be edited by hand. A line the script cannot parse is
  reported with its line number, skipped for calibration, and kept as it
  is. Writers take a lock file, so parallel agents do not lose each
  other's lines.

## Scripts

Run them from inside the repository you are estimating. `<skill-dir>` is
the folder this README is in:

```
python3 <skill-dir>/scripts/reckon.py where                 # create/print the store
python3 <skill-dir>/scripts/reckon.py record --title "Fix login loop" --type bugfix --guess-min 20 --model <id>
        # quick; on an empty history prints a_p50 1.8, a_p90 9.0, method "guess cold"
python3 <skill-dir>/scripts/reckon.py record --batch backlog.jsonl --model <id>   # one estimate per line
python3 <skill-dir>/scripts/reckon.py actual <id>                         # measure the request that followed the estimate
python3 <skill-dir>/scripts/reckon.py actual <id> --turns 2               # ... plus two follow-up turns
python3 <skill-dir>/scripts/reckon.py sessions                            # list this repo's recent sessions
python3 <skill-dir>/scripts/reckon.py actual <id> --session <id-prefix>   # pick the session
python3 <skill-dir>/scripts/reckon.py actual <id> --session <id-prefix> --whole-session   # the whole session did this task
python3 <skill-dir>/scripts/reckon.py actual <id> --agent-min 9 --wait-min 6   # or by hand
python3 <skill-dir>/scripts/reckon.py profile               # full mode: build or refresh the profile
python3 <skill-dir>/scripts/reckon.py calibrate --type feature --rounds 81 --guess 240
        # on an empty history prints a_p50 21.6, a_p90 108.0, used "guess cold"
python3 <skill-dir>/scripts/reckon.py record --title "Notification preferences" --type feature --rounds 81 --guess-min 240 --b-min 50 --c-min 30
python3 <skill-dir>/scripts/reckon.py backfill --limit 200   # seed measured speed from past sessions (5+ rounds each)
python3 <skill-dir>/scripts/reckon.py backtest file.jsonl    # leave-one-out check
python3 <skill-dir>/scripts/replay.py                       # try reckon on your own past requests (see the top)
```

`replay.py` is a demonstration, not part of the skill: SKILL.md never
loads it and agents do not need it.

Tests: `python3 -m unittest discover -s tests` from the skill folder
(synthetic data only).

Session logs: `--agent claude-code` (default), `--agent opencode` or
`--agent codex` on `actual`, `sessions`, `backfill` and `replay.py`.
Adding another agent: write a module in `scripts/adapters/` that turns a
session into a `common.Trace` (see `adapters/__init__.py`), register it,
and add a scrubbed real session to `tests/fixtures/adapters/` so the
contract tests in `tests/test_adapters.py` cover it.

## Evidence

See [references/benchmark.md](references/benchmark.md) for the full
benchmark, the skill's own measurement. Background reading is listed in
[references/evidence.md](references/evidence.md): METR time horizons,
agentpace, DORA 2025 and others. Their figures are quoted there as each
source reports them and were not re-checked for this release; one of
them, AgentTime, is secondhand with no primary source linked.

## License

MIT. Version 0.4.1.

## Changelog

**Unreleased**

- Measured actuals for OpenCode (`--agent opencode`) and Codex CLI
  (`--agent codex`), on `actual`, `sessions`, `backfill` and `replay.py`.
- All adapters share one measurement core (`scripts/adapters/common.py`);
  the Claude Code numbers are unchanged (checked on 60 real sessions).
- Contract tests on scrubbed real sessions (timings kept, text removed).
- Sessions are matched by each agent's own id (`--session <prefix>`);
  OpenCode and Codex ids keep more characters in history so they stay unique.

**0.4.1**

- Each estimate stores `skill_version` (the SKILL.md it was made with), so
  a later release can tell which ratios were measured with an older text.

**0.4.0**

- `scripts/replay.py`: try reckon on your own recent requests. One batched
  model call; shows actual minutes, the raw guess, reckon cold and
  calibrated, and what the estimating cost. Not loaded by the skill.

**0.3.2** (benchmark re-run on the 0.3.1 text)

- Cold prior 0.09 / 0.45 instead of 0.12 / 0.6. 0.12 was already about
  25% high for 0.3.0, and the 0.3.1 text made guesses higher still. At
  0.09 / 0.45 every guess run kept 91-98% of actuals at or below P90.
- Calibrated accuracy is unchanged (51-55% across guess runs).
- Upgrading: a change to the skill text can move the guess level (Haiku
  moved 1.6x between 0.3.0 and 0.3.1). Ratios in an existing history were
  measured with the older text, so expect estimates to be off by up to
  that factor until the 40-item window has turned over.

**0.3.1** (fixes from two reviews)

- `actual` measures the one request an estimate was for (from the time
  `record` stored to the next human prompt; `--turns N` for follow-ups),
  not the whole session. `--whole-session` is the explicit option.
  `record` is therefore the last tool call of a turn that only
  estimates; full mode runs `calibrate`, writes its report, then records.
- One rule for rounds against guess, in calibration.md, and the script,
  the examples and this README follow it. Measured speed now counts as
  calibrated rounds.
- The 40-item window is taken per method and the same-model rule inside
  it; `backfill` keeps history in start-time order (run it once to
  re-sort a 0.3.0 store).
- History is safer: unparsed lines are kept and reported, writers take a
  lock, input is validated, `--a-p50` entries are stored as `manual`,
  hashed titles no longer leak through the id.
- A P90 not above its P50 falls back to the cold spread with LOW
  confidence; `record` prints the corrected wait; `record --batch`.
- Full mode moved to `references/full-mode.md` and is presented as
  structure, not accuracy; risk multipliers became notes.
- Out of scope stated: work that people will do.
- Outside git the store is `~/.reckon/` instead of the current folder.
- The README states the missed accuracy criterion and the benchmark's
  limits. New evals with fixtures, and a test suite.

**0.3.0** Quick mode (the rescaled gut guess) became the default.

**0.2.0** 40-item window, same-model rule, single-request reference class.
