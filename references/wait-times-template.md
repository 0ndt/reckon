# Wait times

Copy this table into `.reckon/profile.md` under Notes (or a `waits` section)
and fill it from real measurements. `<skill-dir>/scripts/reckon.py calibrate --type T`
prints a measured wait floor once history has three or more items of that
type. Until then, use the defaults.

| Wait | Default to use until measured | Measured P50 | Measured P90 | Notes |
|---|---|---|---|---|
| full CI pipeline on a PR | 10 min | | | |
| container or app build | 5 min | | | |
| mobile release build | 10 min | | | |
| deploy to a shared test environment | 10 min | | | |
| database migration on a test environment | 5 min | | | |
| distributing a test build to testers | 5 min | | | |
| app store or marketplace review | (calendar note) | | | not in C |
| a colleague's code review | (calendar note) | | | not in C |
| a person answering a question | (calendar note) | | | not in C |

Count a wait in C only when the task requires that step and it is known
to take over 2 minutes; quick builds and test runs are already in A. A
wait the task might need ("deploy if the tests pass") is not counted; list
it as an assumption. Agents tend to overestimate C, so once history exists
report the `c_corrected` that `record --c-min` prints (C x `c_ratio`).

How to measure: the Claude Code adapter counts any shell command that runs
longer than 2 minutes as wait time, so `actual` fills `wait_min`
automatically. A command started in the background is not counted. By hand, time the command or read the CI run's duration.
