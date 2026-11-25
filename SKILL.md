---
name: reckon
description: Estimates how long software work will take when an AI coding agent does it, as a P50/P90 range of agent minutes calibrated on measured history of past agent work, with human touch time and wait time kept separate. Use when someone asks how long a task, feature, fix or backlog will take an agent, asks to estimate or size agent work or fill an effort column, quotes days or story points for agent-built work, or when recording how long a task actually took. Not for work that people will do.
license: MIT
compatibility: Any agent that can read and write files. Python 3.9+ (standard library only) is optional; without it every step runs by hand. Automatic session measurement currently supports Claude Code transcripts.
metadata:
  version: "0.3.2"
---

# reckon

Agents anchor on human timelines, so their gut answer to "how long will
this take you?" is several times too high, but consistently so. reckon
keeps that gut guess and rescales it with how long past tasks really took.

## Hard rules

1. **Agent work only.** If people will do the work, or a person with an
   agent assisting, say this method does not apply and stop: no agent
   minutes, no history line.
2. **Agent time (A) is always a P50/P90 range in agent minutes** with a
   confidence level. Never man-days, man-hours, story points or one bare
   number.
3. **Three buckets, never mixed:** A, agent time; B, human touch time
   (reviews, answers, approvals, manual tests); C, wait time (CI, builds,
   deploys). B and C are single numbers. In quick mode mention them only
   when the request clearly needs them.
4. **Never correct the gut guess yourself** (no "agents overestimate, so
   I'll halve it"), and never show it: it is an input, not an estimate.
   Calibration only works if the guess is made the same way every time.
5. **Keep it cheap.** Target: at most about 5% of the work. Measured: a
   quick estimate run on its own cost a median 13% of the work's time,
   because the median request is only 7 rounds; inside a conversation it
   is one script call, plus one for the actual, which is 5% only from
   about 40 rounds up. So for a single request spend nothing beyond those
   two calls. When the budget is tight drop, in this order: code reading
   and the profile, the breakdown, the report file, B and C details. Never
   drop the recorded guess and its actual.

## The store

One folder, `.reckon/`, at the root of the main repository (shared by all
git worktrees), or `~/.reckon/` outside git; `RECKON_HOME` overrides it.
It holds `history.jsonl` (one line per estimate) and, for full mode, a
profile and reports. Its own `.gitignore` contains `*`, so it ignores
itself. When it is first created (the script prints `store_created`),
tell the person once where it is.

Run the script from inside the repository being estimated:
`python3 <skill-dir>/scripts/reckon.py <command>`, where `<skill-dir>` is
the folder holding this file.

## Choose the mode

| Mode | Use when | Cost |
|---|---|---|
| Quick (default) | any single request, and every item of a backlog | one guess, one script call |
| Full | the quick P90 is over 2 hours; the request is a multi-part plan or spec that needs a breakdown or a split; or someone asks for a breakdown | reading [references/full-mode.md](references/full-mode.md) and the three references it names (about 8k tokens), plus a repo profile; up to about 10k when the profile and wait-time steps also need repo-profile.md, sources.md and wait-times-template.md |

Full mode is a structure for explaining and splitting large work. It is
not a more accurate number: in the benchmark, counting rounds added
nothing to the rescaled guess. Load full-mode.md only when a row above
applies.

**Backlogs:** make the quick guess for every item in one pass, write one
JSON object per line (`title`, `type`, `guess_min`) to a file and run
`record --batch <file.jsonl>`. Use full mode only for the items whose P90
comes out over 2 hours.

## Quick mode

1. **Guess.** "How many minutes of my own working time will this take?"
   One number, from the request and what you already know. Do not read
   code or decompose. Make each guess fresh; do not steer it toward
   earlier rescaled results in the conversation.
2. **Rescale and record.**
   `python3 <skill-dir>/scripts/reckon.py record --title "<short title>"
   --type <type> --guess-min <N> --model <model-id>` prints an `id`,
   `a_p50`, `a_p90` and `confidence`, and stores the estimate with its
   time. Keep the `id` for step 5. Do not call other tools after
   `record` in the same turn unless they are the work itself.
   - Types: bugfix, feature, refactor, migration, ui, integration, infra,
     docs, research, ops.
   - Omit `--model` if you do not know your model id.
   - If another agent will do the work, add `--agent <name>
     --confidence LOW`: your guess is about your own speed.
   - If a wait is certain, add `--c-min <minutes>`; report `c_corrected`
     when the script prints it.
3. **Report** only the rescaled range, in one or two lines: "About 2-9
   minutes of agent time (P50-P90, cold start, low confidence)." or
   "... (calibrated on 23 past tasks)". Add "Needs about 5 minutes of your
   review" or a wait only when the task cannot finish without it.
4. **If P90 is over 2 hours**, switch to full mode for this task.
5. **Record the actual** as the last tool call of the turn that did the
   work: `python3 <skill-dir>/scripts/reckon.py actual <id>`. It measures
   only this request: from the estimate to the next human prompt (or,
   if you stopped after reporting the estimate, the go-ahead turn that
   followed). If later prompts continued the same request, run it again
   with `--turns N`. If the work ran in another session, pick it with
   `sessions`, then `actual <id> --session <id-prefix>`. Not on Claude
   Code: `actual <id> --agent-min <minutes>`. Never invent an actual.

### Without Python

Probe `python3`, `python`, then `py -3`. If none is Python 3, do the same
by hand:

- `g` = actual minutes / guess for each finished item that has a guess.
  The window rule is in
  [references/calibration.md](references/calibration.md) ("The window").
  With 8 or more items in the window: `A_p50 = N x median(g)`,
  `A_p90 = N x 90th percentile of g`.
- Otherwise use the cold values: `A_p50 = N x 0.09`, `A_p90 = N x 0.45`,
  confidence LOW. They are provisional, from one team's history: the guess
  level moved by up to 1.7x between skill versions, models and days, so a
  cold P50 can be off by that much. Your own history replaces them after
  8 items.
- Append one line to `.reckon/history.jsonl` (`ts` is the UTC time of the
  estimate):

```json
{"id": "2026-03-02-fix-login-loop", "date": "2026-03-02", "ts": "2026-03-02T09:15:00Z", "title": "Fix login loop", "type": "bugfix", "level": "quick", "estimate": {"guess_min": 20, "a_p50": 1.8, "a_p90": 9.0, "method": "guess cold", "confidence": "LOW"}}
```

- After the work, add the actual to that same line, before its last `}`,
  from the start and end times you noted for this request only:
  `, "actual": {"agent_min": 3.5, "model": "<model-id>", "source": "manual"}`

## References

- [references/full-mode.md](references/full-mode.md): the full-mode workflow; it names the other files it needs
- [references/calibration.md](references/calibration.md): window rule, rounds-or-guess table, formulas, confidence, history schema, how actuals are measured
- [references/benchmark.md](references/benchmark.md): results and limits; not needed to estimate
