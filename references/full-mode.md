# Full mode

Load this only when SKILL.md sends you here: the quick P90 is over 2
hours, the request is a multi-part plan or spec that needs a breakdown or
a split, or someone asked for a breakdown.

## Contents

- What full mode is for
- What to read
- 1. Guess and record nothing yet
- 2. Load the repo profile
- 3. Read the task at the right level
- 4. Decompose and count rounds
- 5. Get the number
- 6. Add human touch and wait time
- 7. Report and record

## What full mode is for

Full mode gives a **structure**: modules someone can check, the places
where a person must act, the waits, and a proposed split. It does not
give a more accurate number than quick mode. In the benchmark
(benchmark.md):

- counting rounds was no more accurate than the rescaled gut guess, and
  combining the two added nothing;
- the requests that got full mode were small (22 minutes at most), so
  there is no measurement on the large work full mode is meant for;
- the 2-hour split rule could not be tested, and dropping the risk
  multipliers of earlier versions made estimates better.

So the number still comes from the script's precedence rule (step 5), and
at cold start that is the rescaled guess. Present the breakdown as an
explanation and a plan, not as extra precision.

## What to read

This file, [anchors.md](anchors.md), [calibration.md](calibration.md)
and [output-template.md](output-template.md): about 8k tokens together.
Plus the repo profile (1-2k tokens once built; 5-15k tokens to build it
the first time). If that is more than about 5% of the work being sized,
skip the profile, work from the task text, and say so under Assumptions.

## 1. Guess and record nothing yet

Make the gut guess exactly as in quick mode (SKILL.md, step 1), before
reading anything, and keep it. If quick mode already recorded this task,
reuse that guess; do not make a second, better-informed one. The
full-mode record in step 7 is then a second history line for the same
task: put the actual on that line only (a line without an actual is
ignored by calibration).

## 2. Load the repo profile

Read `.reckon/profile.md` if it exists. Refresh it only when at least 30
files changed since the commit in its header, or a manifest or CI file
changed; `python3 <skill-dir>/scripts/reckon.py profile` applies that
rule and builds the mechanical part. Add or correct the "Notes" section
yourself (gotchas, fragile areas, slow steps). If there is no profile,
build it, using existing knowledge before reading code, in this order:
repo docs (AGENTS.md, CLAUDE.md, README, ARCHITECTURE, docs/, ADRs,
llms.txt), existing code indexes, cheap CLIs (rg, ctags, ast-grep,
git log), then manual reading. Never install a tool or build an index;
suggest the command instead. Details: [repo-profile.md](repo-profile.md)
and [sources.md](sources.md).

## 3. Read the task at the right level

| Level | Reads | Use when |
|---|---|---|
| Light (default) | task text + profile | almost always; about 3-5k tokens |
| Standard | + grep for touched files, signatures only | the task names code you cannot place from the profile |
| Deep | + read the touched files | confidence would be LOW and the task is large (P90 over 1 hour) |

Before decomposing, look in `history.jsonl` for a similar past estimate
and reuse its decomposition when one exists.

## 4. Decompose and count rounds

- A round is one agent step that calls at least one tool (read, search,
  edit, run, git).
- Split the task into modules, one coherent change each. For every module
  pick a type and a size from anchors.md and a number of rounds inside
  that size's range.
- Add 15% integration rounds when there are two or more modules.
- Sanity-check the total against the reference classes in anchors.md: a
  single request is about 9 rounds at the median, a whole multi-request
  piece of work about 40.
- Risks are **notes, not multipliers**. If the request, the profile or the
  code you read names a risk from the list in anchors.md, write its code
  next to the module and name the biggest one as "Top risk" in the report.
  Do not change the rounds or the minutes for it.
- Split any module over 45 rounds into modules that each end in a commit
  or another checkpoint.

## 5. Get the number

```
python3 <skill-dir>/scripts/reckon.py calibrate --type <type> --model <model-id> --rounds <R> --guess <N>
```

prints `a_p50`, `a_p90` and `used`: which of rounds and guess gave the
number, by the table in calibration.md ("Rounds or guess"). At cold start
it is the rescaled guess; rounds take over or are combined once history
calibrates them. Report exactly that range. You may show the rounds-only
figure (`calibrate --rounds <R>`) as a cross-check, labelled as such.
Without Python, apply the same table by hand with the formulas in
calibration.md.

If A+C at P90 is over 2 hours, propose a split into pieces that can be
estimated, run and checked on their own, each ending in a commit or other
checkpoint. Until the split is agreed, report the unsplit total and list
the pieces.

## 6. Add human touch and wait time

- **B:** list each point where the person must act (plan review,
  questions, approvals, sign-ins, manual or device tests, their own final
  review) and add up the per-touch minutes in anchors.md. A coordinating
  agent's review is not B.
- **C:** list each step the task requires that is known to take over 2
  minutes on its own (CI run, slow build, deploy, waiting for another
  agent), with its measured or default duration from
  [wait-times-template.md](wait-times-template.md). Quick local builds and
  test runs are already inside A. List a wait only when the task cannot
  finish without it: in the benchmark two thirds of estimated waits never
  happened. Waits for other people (code review, store review, answers) go
  in calendar notes, not in C.
- `record --c-min <C>` prints `c_corrected` once history can correct
  waits; report that figure.

## 7. Report and record

`record` must be the **last tool call** of the estimate turn: `actual`
measures from the moment of `record`, so any tool call after it (such as
writing the report file) would be counted as the work. In this order:

1. Get the numbers without storing anything:
   `python3 <skill-dir>/scripts/reckon.py calibrate --type <type> --rounds <R> --guess <N> --model <model-id>`
   (`record` will store the same `a_p50` / `a_p90`).
2. Fill in the full-mode template in [output-template.md](output-template.md)
   and save it as `.reckon/estimates/<date>-<slug>.md`.
3. Record, and make no further tool call in this turn:

```
python3 <skill-dir>/scripts/reckon.py record --title "<title>" --type <type> --rounds <R> --guess-min <N> --b-min <B> --c-min <C> --model <model-id> --modules '<JSON list>'
```

(`--rounds` makes it a full-mode item; by hand, use the schema in
calibration.md.) Then show the report in your reply.

After the work, record the actual as in SKILL.md, step 5. A full-mode
task usually spans several turns or sessions: use `--turns N`, or
`--whole-session` when a session did this task and nothing else, or add
up the minutes and pass `--agent-min`.
