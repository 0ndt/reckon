---
name: reckon
description: Estimates how long software work will take when AI coding agents do it, in agent minutes instead of human man-days or story points. Quick mode (default) rescales the agent's own gut guess with measured history; full mode counts tool-call rounds per module for plans, backlogs and work that may need splitting. Splits every estimate into agent time, human touch time (reviews, answers, manual or device testing) and wait time (CI, builds, deploys, other people), and gives P50/P90 ranges with a confidence level. Use when asked to estimate, size, scope, plan or schedule a task, feature, sprint or backlog, when filling an effort or timeline column, when someone asks how long something will take, or when an estimate in days or story points appears for agent-built work. Also use after a task to record how long it actually took.
license: MIT
compatibility: Any agent that can read and write files. Python 3 (standard library only) is optional; without it every step runs by hand with the formulas in references/calibration.md. Automatic session measurement currently supports Claude Code transcripts.
metadata:
  version: "0.3.0"
---

# reckon

Agents anchor on human timelines from their training data, so their gut
answer to "how long will this take you?" is several times too high. The
error is consistent, though, so reckon measures it instead of fighting it:
it keeps the gut guess and rescales it with how long past tasks really took.
In a benchmark on 200 real requests, that rescaled guess was as accurate as
a full round-by-round breakdown at about half the cost. The breakdown is
kept for the jobs where its structure matters: plans, backlogs and work
that may need splitting.

## Hard rules

1. Never give man-days, man-hours, story points or a single number. Every
   estimate is a P50/P90 range in agent minutes with a confidence level.
2. Keep three buckets separate:
   - **A. Agent time**: the agent thinking, calling tools, reading results.
   - **B. Human touch time**: reviews, answers to questions, approvals,
     sign-ins, manual or device testing.
   - **C. Wait time**: CI, builds, deploys, store review, other people.
   Calendar time is a consequence of B and C, not of A. In quick mode,
   mention B and C only when the request clearly needs them.
3. Never correct the gut guess yourself (no "agents overestimate, so I'll
   halve it"). Calibration does that, and it only works if the guess is
   made the same way every time.
4. The estimate must cost at most about 5% of the work it sizes. Use quick
   mode unless one of the full-mode triggers below applies.
5. Always record the estimate and, after the work, its actual. Without
   actuals there is nothing to calibrate on.

## The store (once per repo)

All output and cache live in one folder, `.reckon/`, at the root of the main
repository (shared by every git worktree), or in the current directory
outside git. `RECKON_HOME` overrides the location. On first use create it
with a `.gitignore` containing a single `*` line, so the folder ignores
itself and the repo's own `.gitignore` is never touched.

```
.reckon/
  .gitignore     "*"  (edit to "*" + "!history.jsonl" + "!.gitignore" to share history via git)
  profile.md     cached repo profile, stamped with the commit it describes
  sources.json   detected docs and code-index tools
  history.jsonl  one line per estimate, later completed with actuals
  estimates/     one markdown file per estimate
```

With Python: `python3 <skill-dir>/scripts/reckon.py where` creates and
prints it. Always run the scripts from inside the repository being
estimated, giving the path to this skill's folder; running them from the
skill folder would put the store there.

## Choose the mode

| Mode | Use when | Cost |
|---|---|---|
| Quick (default) | one request: a change, fix, check, answer or small feature, including follow-ups in a conversation | one guess and one script call |
| Full | the request is a plan, spec, sprint or backlog with several parts; the quick P90 is over 2 hours; or someone asks for a breakdown | profile, modules, rounds; about 25 s and 100-300k tokens for one task, a few thousand tokens per task when sizing a batch |

When sizing many tasks at once (a backlog, a sprint, an effort column),
do the guesses for all of them in one pass and record them together.

## Quick mode

1. **Guess.** Answer for yourself, the agent that will do the work: "How
   many minutes of my own working time will this take?" One number, from
   the request and what you already know. Do not read code, decompose or
   adjust for bias.
2. **Rescale and record.** With Python, from inside the repository:
   `python3 <skill-dir>/scripts/reckon.py record --title "<short title>"
   --type <type> --guess-min <N> --model <model-id>` prints `a_p50` and
   `a_p90` and appends the estimate to history. Without Python:
   `A_p50 = N x g50`, `A_p90 = N x g90`, where `g` is each recent
   finished item's actual minutes divided by its guess (median and 90th
   percentile over the 40 most recent, same model once 8 exist). With
   fewer than 8 such items, use the cold values `g50 = 0.12`, `g90 = 0.6`
   and say the confidence is LOW.
3. **Report** in one or two lines, for example: "About 2-6 minutes of
   agent time (P50-P90, calibrated on 40 past tasks). Needs your review of
   the diff, about 5 minutes." Add a wait only when the task cannot finish
   without it (a deploy, a CI run).
4. **If the quick P90 is over 2 hours**, switch to full mode for this task:
   it needs a breakdown and probably a split.

Task types for `--type`: bugfix, feature, refactor, migration, ui,
integration, infra, docs, research, ops (see anchors.md).

## Full mode

Make the gut guess first (quick mode step 1) and keep it: the script
combines both estimates once each has calibration, and the guess keeps
calibrating even when few tasks get a full estimate. Then count rounds:

- A round is one agent step that calls at least one tool (read, search,
  edit, run, git). Sanity-check the total against the reference classes in
  [references/anchors.md](references/anchors.md): a single request is
  about 9 rounds at the median, a whole multi-request piece of work about
  40.
- Risk multipliers come only from the fixed list in anchors.md, only with
  evidence: at most one per module, at most x2, never stacked.
- Split any module over 45 rounds, and propose a split for any task whose
  A+C P90 is over 2 hours, into pieces that each end in a commit or other
  checkpoint.

### 1. Load or refresh the repo profile

Read `.reckon/profile.md` if it exists. If its commit is not HEAD, refresh
only what changed (`git diff --stat <commit>..HEAD`). If it does not exist,
build it. Use existing knowledge before reading code, in this order:
repo docs (AGENTS.md, CLAUDE.md, README, ARCHITECTURE, docs/, ADRs,
llms.txt), then existing code indexes (codegraph, graphify, LSP or Serena
tools if the agent has them), then cheap CLIs (rg, ctags, ast-grep,
git log), then manual reading. Never install a tool or build an index;
suggest the command instead. Details:
[references/repo-profile.md](references/repo-profile.md) and
[references/sources.md](references/sources.md).

With Python: `python3 scripts/reckon.py profile` builds or refreshes the
mechanical part; add the "Notes" section yourself (gotchas, fragile areas).

### 2. Read the task at the right level

| Level | Reads | Use when |
|---|---|---|
| Light (default) | task text + profile | almost always; ~3-5k tokens |
| Standard | + grep for touched files, signatures only | task names code you cannot place from the profile |
| Deep | + read the touched files | confidence would be LOW and the task is large (P90 > 1h) |

Before decomposing, look in `history.jsonl` for a similar past estimate and
reuse its decomposition when one exists.

### 3. Decompose and count rounds

Split the task into modules (one coherent change each). For every module
pick a type and a size from [references/anchors.md](references/anchors.md),
take the round range for that size, then apply at most one risk
multiplier. Add 15% integration rounds when there are two or more modules.
Rounds include reading, searching, editing, running tests, fixing, git and
deploy commands, and writing the final report.

### 4. Convert rounds to minutes

Use [references/calibration.md](references/calibration.md):

- **Calibrated** (among the 40 most recent finished items, at least 5 of
  the same type with estimates, or 8 of any type): `A_p50 = R x 0.2 x r50`,
  `A_p90 = R x 0.2 x r90`, where `r` is each item's actual minutes over
  its `rounds x 0.2`. Once 8 finished items were done by the same model as
  the one that will do this task, use only those: speed differs by model
  by up to 5x.
- **Measured speed** (history has measured sessions but few estimates):
  minutes per round from history.
- **Cold start**: 0.2 min per round for P50 and a x3 spread for P90.

With Python: `python3 scripts/reckon.py calibrate --type <type> --model
<model-id> --rounds <R> --guess <N>` prints the numbers to use, combining
rounds and the gut guess.

### 5. Add human touch and wait time

B: count the points where the person must act (plan review, questions,
approvals, sign-ins, manual or device tests, their own final review) and
use the per-touch minutes in anchors.md. A coordinating agent's review is
not B. C: list each step the task requires that is known to take over 2
minutes on its own (CI run, slow build, deploy, waiting for another agent
to finish) with its measured or template duration from
[references/wait-times-template.md](references/wait-times-template.md).
Quick local builds and test runs are already inside A. Waits for other
people (code review, store review, answers) go in calendar notes, not in
C. List a wait only when the task cannot finish without it; in the
benchmark two thirds of estimated waits never happened. Once history has
5 items with an estimated wait, multiply C by `c_ratio`.

### 6. Report

Use [references/output-template.md](references/output-template.md): the
module table, the three buckets with P50/P90, confidence, assumptions, the
top risk, and any split. Save it to `.reckon/estimates/<date>-<slug>.md`
and append one line to `history.jsonl`
(`python3 scripts/reckon.py record --level full --rounds <R> --guess-min <N>
...`, or by hand with the schema in calibration.md).

## After the task: record actuals

Record what it really took so the next estimate is calibrated. With
Claude Code, run `reckon.py sessions` to list this repo's recent sessions
with their start time and title, pick the one that did the work, and run
`reckon.py actual <id> --session <session-id-prefix>`. Use
`--session latest` only when the current session did the whole task and
nothing else. Other agents, or no Python: fill the `actual` object by hand
from start/end times (calibration.md). Never invent actuals.

## Without Python

Probe `python3`, then `python`, then `py -3`, and use the first whose
`--version` reports Python 3. If none does, do everything by hand: the formulas in calibration.md need only medians and multiplication,
the store is plain Markdown and JSONL, and the profile is a short Markdown
file you write. Only automatic transcript measurement is lost; record
actuals from timestamps instead.

## References

- [references/anchors.md](references/anchors.md): task types, size anchors in rounds, risk list, human touch minutes
- [references/calibration.md](references/calibration.md): formulas, history schema, manual mode
- [references/repo-profile.md](references/repo-profile.md): profile format and refresh rules
- [references/sources.md](references/sources.md): detecting docs and code-index tools
- [references/wait-times-template.md](references/wait-times-template.md): wait table to fill from measurements
- [references/output-template.md](references/output-template.md): report format with a worked example
- [references/evidence.md](references/evidence.md): research behind the numbers
- [references/benchmark.md](references/benchmark.md): benchmark results, what works and what does not
