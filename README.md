# reckon

**Realistic Estimates, Calibrated on Known Outcomes, Not man-days.**

An [Agent Skill](https://agentskills.io) that sizes software work done by
AI coding agents the way that work actually behaves: in agent minutes,
split into agent time, human touch time and wait time, reported as P50/P90
ranges and corrected by what past tasks really took.

## Why the name

*To reckon* means to estimate or work something out ("I reckon it'll take
an hour"). Sailors used **dead reckoning** to find a ship's position from
its known speed and the time travelled, with no landmark in sight. This
skill does the same for agent work: it takes measured speed (minutes per
round) and the work ahead (rounds) and works out where you will be, then
corrects its course every time a real outcome comes in.

The letters spell out the method:

| Letter | Stands for | What the skill does |
|---|---|---|
| **R** | Realistic | estimates in agent units, not human timelines |
| **E** | Estimates | always a P50/P90 range with a confidence level |
| **C** | Calibrated | scales by the ratio of actual to estimated time on past tasks |
| **K** | Known | uses only measured history and the repo's own docs and indexes |
| **O** | Outcomes | records what each task really took, automatically when it can |
| **N** | Not man-days | never answers in man-days, man-hours or story points |

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
About 2-6 minutes of agent time (P50-P90, calibrated on 40 past tasks).
Needs your review of the diff, about 5 minutes.
```

Full mode, for plans, backlogs and work that may need splitting:

```
| # | Module                        | Type    | Size | Rounds | Risk | After risk |
|---|-------------------------------|---------|------|--------|------|------------|
| 1 | CSV endpoint with filters     | feature | M    | 30     | -    | 30         |
| 2 | Export button and download    | ui      | S    | 12     | -    | 12         |
|   | Integration (+15%)            |         |      |        |      | 6          |

| Bucket            | P50    | P90    |
|-------------------|--------|--------|
| A. Agent time     | 10 min | 29 min |
| B. Human touch    | 25 min |        |
| C. Wait           | 8 min  |        |
| A+C elapsed       | 18 min | 37 min |
Confidence: LOW (cold start) · Top risk: large exports time out
```

## How it works

1. **Quick mode by default.** The agent gives its plain gut answer: "how
   many minutes of my own time will this take?" It does not read code and
   does not correct the number itself. The script multiplies it by the
   median and 90th percentile of actual/guess over the 40 most recent
   finished tasks, done by the same model once there are 8. Agent guesses
   are consistently several times too high, so a measured ratio fixes most
   of the error. Cold start uses 0.12 and 0.6, fitted on the benchmark
   history.
2. **Full mode when structure matters:** a plan, spec, sprint or backlog
   with several parts; a quick P90 over 2 hours; or a request for a
   breakdown.
   - **Profile once, reuse.** A 1-2k token repo profile is cached in
     `.reckon/profile.md`, stamped with the commit, and refreshed only from
     `git diff`. It is built from what already exists: AGENTS.md,
     CLAUDE.md, README, docs and ADRs, then code indexes such as
     [codegraph](https://github.com/colbymchenry/codegraph) and
     [graphify](https://github.com/safishamsi/graphify), then `rg`,
     `ctags`, `ast-grep` and `git log`. It never installs tools or builds
     indexes.
   - **Count rounds.** Modules get a type and a size from fixed anchors,
     at most one risk factor each and only with evidence, plus 15% for
     integration. The total is sanity-checked against measured reference
     classes, and anything over 2 hours at P90 gets a proposed split.
   - **Combine.** Rounds are converted with measured speed. When both the
     rounds and the gut guess are calibrated, the estimate is their
     geometric mean.
3. **Add people and waiting.** Human touch points and known waits are
   reported separately; calendar time is driven by them, not by the agent.
4. **Record outcomes.** After the task, the actual is measured from the
   agent's transcript (Claude Code today) or entered by hand, and the next
   estimate is calibrated with it.

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
| **reckon 0.3.0 (quick), cold start** | **67%** | **0.78** | **64%** | **97%** |
| **reckon 0.3.0 (quick), calibrated** | **56%** | **1.04** | **50%** | **88%** |

Cost of one estimate:

| | Batched, per task | One task on its own |
|---|---|---|
| 0.2.0 | 2,900 tokens | 27 s, 200k tokens |
| 0.3.0 | 2,000 tokens (Haiku: 2,100 tokens at 1/15 the price) | 13 s, 115k tokens |

What this means:

- **After 8 finished tasks:** calibrated estimates are almost unbiased, and
  P50/P90 mean what they say.
- **Single requests:** expect about +/-50%. Speed per round varies that
  much on its own.
- **0.3.0 vs 0.2.0:** same accuracy once calibrated (no significant
  difference, p = 0.86). The cold start is far better (67% vs 178%), and
  an estimate costs about half.
- **Where the accuracy comes from:** calibrating on measured history, not
  from counting rounds. A naive minutes guess rescaled the same way did as
  well, which is why 0.3.0 makes the rescaled guess the default.
- **Waits (C):** with the ratio-of-sums correction, 152 minutes were
  estimated against 129 actual, down from 7.7x too high.
- **Human touches (B):** only a proxy could be checked, and it was weak.

**First validation, 0.1.0:** 20 whole delegated tasks with the anchors
frozen. Cold start had a median error of 31%. Calibrated, the error was
30%, with 85% of actuals at or below P90.

Your models, tools and repos differ. Record actuals for a few tasks and run
`reckon.py backtest` on your own history; it compares quick and full mode.

## Install

Copy the `reckon` folder into your agent's skills folder, for example:

| Agent | Folder |
|---|---|
| Claude Code (personal) | `~/.claude/skills/reckon/` |
| Claude Code (one project) | `.claude/skills/reckon/` |
| Other Agent Skills clients | their skills folder |

Then ask "how long will this take?" or "estimate this backlog".

## Requirements

- Any agent that can read and write files.
- Python 3 is **optional** (standard library only). Without it, every
  formula is simple enough to apply by hand; only automatic transcript
  measurement is lost.
- Git is optional; outside a repo the store lives in the current folder.

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
- `RECKON_HOME=/some/path` moves it.
- Only numbers, short titles, task types and the repo folder name are
  stored. `RECKON_HASH_TITLES=1` stores a hash instead of the title.
  Nothing is sent anywhere.

## Scripts

Run them from inside the repository you are estimating, by path to the
skill folder:

```
python3 scripts/reckon.py where                 # create/print the store
python3 scripts/reckon.py profile               # build or refresh the profile
python3 scripts/reckon.py record --title "Fix login loop" --type bugfix --guess-min 20 --model <id>   # quick
python3 scripts/reckon.py calibrate --type feature --rounds 48 --guess 90
python3 scripts/reckon.py record --title "Add CSV export" --type feature --level full --rounds 48 --guess-min 90 --b-min 25 --c-min 8
python3 scripts/reckon.py sessions              # list this repo's recent sessions
python3 scripts/reckon.py actual <id> --session <id-prefix>   # measure that session
python3 scripts/reckon.py actual <id> --agent-min 9 --wait-min 6   # or by hand
python3 scripts/reckon.py backfill --limit 200   # seed speed from past sessions
python3 scripts/reckon.py backtest file.jsonl    # leave-one-out check
```

Adding another agent's logs: write a module in `scripts/adapters/` with
`NAME`, `list_sessions()` and `measure()` (see `adapters/__init__.py`) and
register it.

## Evidence

See [references/benchmark.md](references/benchmark.md) for the full
benchmark, and [references/evidence.md](references/evidence.md): AgentTime (agents
overestimate 2.6-3.8x), METR time horizons (reliability falls with task
length), agentpace (3,383 measured runs, heavy tails), DORA 2025 (the
bottleneck moves to review and delivery).

## License

MIT. Version 0.3.0.
