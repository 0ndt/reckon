# Repo profile

The profile is a short Markdown summary of the repository, about 1-2k
tokens, saved at `.reckon/profile.md`. It lets every later estimate start
from a few hundred lines instead of re-reading the repo.

## Contents

- What goes in it
- Building it without Python
- Refreshing it
- Token budget

## What goes in it

| Section | Content | Source |
|---|---|---|
| header | `<!-- reckon-commit: <full HEAD hash> -->`, the date and the branch name | `git rev-parse HEAD` |
| Size | file count, test file count, commits in 90 days, main file types, top-level folders with file counts | `git ls-files`, `git log` |
| Build and test | manifests, CI config, build/test/lint commands | package.json scripts, Makefile targets, CI files |
| Knowledge sources | docs, code indexes and CLIs found (see sources.md) | file checks, PATH |
| Hot files | the 10 files changed most in 90 days | `git log --name-only` |
| Notes | modules and what they do, gotchas, fragile areas, slow steps (long builds, flaky tests), anything that made a past estimate wrong | written by the agent |

The script fills everything except Notes. Notes are the valuable part:
write them from the docs and indexes you read, keep them under about 40
lines, and add a line whenever an actual was far from its estimate and you
know why.

## Building it without Python

Run these and summarise the output into the sections above:

```
git rev-parse HEAD
git ls-files | wc -l
git ls-files | sed 's/.*\.//' | sort | uniq -c | sort -rn | head -8
git ls-files | cut -d/ -f1 | sort | uniq -c | sort -rn | head -12
git log --since=90.days --name-only --pretty=format: | sort | uniq -c | sort -rn | head -10
```

On Windows PowerShell without git-bash, use `git ls-files` with
`Group-Object` or read the folder tree directly. Outside git, list the
folder tree two levels deep and skip the hot-files section.

## Refreshing it

1. Read the commit in the header.
2. If it equals HEAD, use the profile as is.
3. Otherwise run `git diff --name-only <commit>..HEAD`. If fewer than 30
   files changed and no manifest or CI file changed, the profile is
   current enough: use it as is and leave the header alone. A profile is a
   summary, not a snapshot, and worktrees that share one store would
   otherwise rewrite each other's stamp after every commit.
4. If 30 or more files changed, or a manifest or CI file changed, rebuild
   the mechanical sections and keep Notes, checking each note against the
   diff.

`python3 <skill-dir>/scripts/reckon.py profile` applies this rule;
`--force` rebuilds regardless.

## Token budget

| Step | Rough cost |
|---|---|
| first build (commands + reading docs + writing Notes) | 5-15k tokens, once |
| reading a current profile | 1-2k tokens |
| refresh after a large change | 1-3k tokens |

If the first build would cost more than the work being estimated, skip it,
estimate at the Light level from the task text alone, and say so in the
assumptions.
