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
| header | `<!-- reckon-commit: <full HEAD hash> -->` and the date | `git rev-parse HEAD` |
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
3. Otherwise run `git diff --stat <commit>..HEAD`. If fewer than about 30
   files changed and no manifest or CI file changed, update only the
   affected lines and Notes entries, then write the new commit hash.
4. If a manifest or CI file changed, or the diff is large, rebuild the
   mechanical sections and keep Notes, checking each note against the diff.

## Token budget

| Step | Rough cost |
|---|---|
| first build (commands + reading docs + writing Notes) | 5-15k tokens, once |
| reading a current profile | 1-2k tokens |
| incremental refresh | 1-3k tokens |

If the first build would cost more than the work being estimated, skip it,
estimate at the Light level from the task text alone, and say so in the
assumptions.
