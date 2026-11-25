# Anchors

These anchors are for cold starts. Once your history has measured actuals,
calibration replaces the speed numbers and corrects your round counts
(see calibration.md).

## Contents

- What counts as a round
- Task types
- Size anchors in rounds
- Risk list (pick at most one per module)
- Human touch minutes
- Splitting rules

## What counts as a round

One round is one agent response that calls at least one tool: a file read,
a search, an edit, a shell command, a git or deploy command, a web fetch.
Parallel tool calls in one response are still one round. Thinking without a
tool call is not a round, but its time is included in minutes per round.

Two opposite mistakes are common. Thinking in "steps" undercounts: a small
fix is not 3 rounds, it is locate, read, edit, test, fix, commit. But
listing every activity as its own module overcounts far more: one read
often answers several questions, and a test run, a commit and a report are
part of the module they finish, not extra modules. In a backtest on real
tasks, estimates made with module-by-module padding were about 3x too high.

### Reference classes

Pick the class that matches what you are sizing, and use it as a sanity
check after decomposing. Measured on Claude Code agents:

| Percentile | One request (570 requests) | Whole piece of work, several requests (86 sessions) |
|---|---|---|
| 25th | about 5 | about 25 |
| median | about 9 | about 40 |
| 75th | about 23 | about 75 |
| 90th | about 45 | about 145 |

Most estimates are for one request: a message that asks for one change,
fix, check or answer, often a follow-up in an ongoing conversation. Half of
those finish in under 10 rounds, so start small and justify every round
above 20. Use the whole-work class only when the request covers what would
normally take several rounds of back-and-forth (a feature from a spec, a
multi-part plan). In the benchmark, agents sized single requests about 3x
too large when they anchored on the whole-work numbers. If a single request
comes out above 45 rounds, re-check it or split it.

## Task types

Use one type per module. The task's type, used to group history, is the
type of the module with the most rounds.

| Type | Covers |
|---|---|
| `bugfix` | find and fix a defect, including the regression test |
| `feature` | new behaviour in existing code |
| `refactor` | change structure without changing behaviour |
| `migration` | data or schema change, data backfill, version upgrade |
| `ui` | screens, components, styling, copy on screen |
| `integration` | connecting to another service, API, SDK or repo |
| `infra` | CI, build, deploy, environments, containers |
| `docs` | written documents, specs, READMEs, reports |
| `research` | investigation or comparison that ends in a written answer |
| `ops` | one-off operations: run a job, change a setting, clean data |

## Size anchors in rounds

| Size | Rounds | Typical examples |
|---|---|---|
| XS | 3-8 | change a config value or copy text; one-line fix at a known place; answer a question from docs you already have |
| S | 8-20 | bugfix with a clear repro in 1-2 files; small feature in one layer; add tests for an existing function; short investigation with a written answer |
| M | 20-45 | feature across 2-3 layers with tests; bug with an unknown cause; migration plus code change; new screen; comparison or audit across several areas |
| L | over 45 | split it, or check that it is not several modules |

Pick a number inside the range: the low third when the location and the
change are already known, the high third when you must search or run
things several times.

Module rounds already include reading, editing, running the module's own
tests, fixing what they show and committing. Add an extra only when the
task explicitly asks for it, and never add an extra and a module for the
same work:

| Extra work (only when asked) | Rounds |
|---|---|
| run the whole test suite and fix fallout | +3-8 |
| open a PR and wait for checks | +3-6 |
| deploy to a shared environment and verify | +4-10 |
| check one user flow by hand through a browser or simulator tool | +3-6 per flow |
| write a report or hand-off note | +1-3 |

When to make a module and when to add an extra: a module changes
something (code, config, data, a document). Running, checking, deploying
and reporting are extras on the module they finish.

## Risk list (pick at most one per module)

Most modules have no risk. Apply one only when the request, the profile
or the code you read names it; a hunch is not evidence. In the benchmark,
agents put a risk on 6 tasks in 10, and dropping the list made estimates
better. Expect about 1 module in 4 to carry one.

Multiply the module's rounds (including its extras) by the single largest
factor that applies. Never multiply two factors together. If more than two
apply, the module is too vague: ask a question or split it instead. Record
module rounds before risk; the task total is after risk and integration.

| Code | Risk | Factor |
|---|---|---|
| R1 | Unfamiliar area: no docs or profile notes cover the code being changed (a missing estimation history alone is not R1) | x1.3 |
| R2 | External API, SDK or third-party behaviour you cannot read the source of | x1.5 |
| R3 | Concurrency, timing, flaky or slow tests | x1.5 |
| R4 | Migration of existing data, or an operation that is hard to undo | x1.5 |
| R5 | An environment the agent cannot drive itself (physical device, native GUI without automation, hardware, production access it lacks); a test server or browser the agent can reach is not R5 | x2 (and add B touches) |
| R6 | Vague or conflicting requirements | x1.5 (prefer asking first) |
| R7 | Contract change across repos or services | x1.3 |
| R8 | Very large legacy or generated file in the change (over ~1,500 lines) | x1.3 |

After all modules: add 15% of the summed rounds for integration when there
are two or more modules.

## Human touch minutes

Count every point where the person (not a coordinating agent or bot) must
act. When the task comes from another agent that will review the result,
that review is not B. Count the person's final review only when they will
read the diff or the document themselves. Use these until history gives
measured numbers.

| Touch | Minutes |
|---|---|
| answer a question or pick an option | 3 |
| go-ahead or approval | 1 |
| sign-in, secret, one-time code | 3 |
| review a plan or spec | 10 |
| review a diff: small / medium / large | 5 / 15 / 30 |
| manual check in a browser or app | 10 |
| install a build and test on a real device | 15 |

## Splitting rules

- Any module over 45 rounds after risk: split it into modules that each
  end in a commit, a passing test run or another checkpoint.
- Any task with A+C P90 over 2 hours: propose a split into tasks that can
  be estimated, run and checked on their own. Until the split is agreed,
  report the unsplit total and list the proposed pieces with their rounds.
- Agent success rates fall as tasks get longer (see evidence.md), so a long
  unsplit task is not just slower; it is more likely to need a redo.
