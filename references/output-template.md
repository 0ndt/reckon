# Output template

## Quick mode

One or two lines in the conversation; no file beyond the history line.

```markdown
About <A_p50>-<A_p90> of agent time (P50-P90, <calibrated on n past tasks | cold start, low confidence>).
<Only if needed:> Needs about <B> of your time (<what>), and a wait of about <C> (<what>).
```

Examples: "About 2-12 minutes of agent time (P50-P90, cold start, low
confidence)." and "About 4-8 minutes of agent time (P50-P90, calibrated on
12 past tasks). Needs your review of the diff, about 5 minutes."

## Full mode

Fill this in, show it to the person, and save it as
`.reckon/estimates/<YYYY-MM-DD>-<slug>.md`. The A row is what `record`
or `calibrate` printed, and "Basis" names the method it printed.

```markdown
## Estimate: <task title>

Method: <guess cold | guess calibrated (n=<n>) | rounds speed | rounds calibrated (n=<n>, <scope>) | rounds+guess> · Read level: <Light | Standard | Deep> · Confidence: <LOW | MEDIUM | HIGH>

| # | Module | Type | Size | Rounds | Risk note |
|---|--------|------|------|--------|-----------|
| 1 | ...    | ...  | M    | 26     | R4        |
| 2 | ...    | ...  | S    | 14     | -         |
|   | Integration (+15%) | | | 6 | |
|   | **Total** | | | **46** | |

| Bucket | P50 | P90 | Basis |
|--------|-----|-----|-------|
| A. Agent time | <a_p50> | <a_p90> | <method>: <the inputs, e.g. gut guess rescaled x0.12 / x0.6> |
| B. Human touch | <B> | | <each touch and its minutes> |
| C. Wait | <C> | | <each wait and its minutes; after c_ratio when printed> |
| **Agent-side elapsed (A+C)** | **<sum>** | **<sum>** | |

Calendar notes: <reviews by others, store review, time zones; not counted above>
Assumptions: <what you assumed about scope, environment, access>
Top risk: <the one thing most likely to blow the P90, and how to check it early>
Split: <none | the pieces and their checkpoints>
```

## Rules for the report

- Round every minute value to a sensible unit: whole minutes under an
  hour, then "1h 20m". Never convert to days.
- When the person asked "how long", lead with one sentence: "About <A+C
  P50>, up to <A+C P90>, plus about <B> of your time."
- If the request came with a deadline or a day count, compare it to the
  estimate in one line instead of adopting it.
- If confidence is LOW, say what would raise it (answering a question,
  a Standard-level read, or recording actuals for a few tasks).
- Never show the raw gut guess as an estimate. In quick mode it does not
  appear at all; in full mode it may appear only in the Basis column.
- The breakdown explains the work; it does not make the range more
  accurate. Do not present it as precision.

## Worked example (full mode, cold start)

Request: "Here is the spec for notification preferences: users choose per
channel what they get, we send a daily email digest instead of single
emails, and there is a settings screen for it. How long, and how would
you cut it up?"

A multi-part spec that asks for a breakdown: full mode.

1. Gut guess first, before reading anything: 240 minutes.
2. Light read: the profile says the API is in `api/`, the web app in
   `web/`, email goes through an external provider's SDK, and CI takes
   about 10 minutes.
3. Modules (each includes its own tests and commit):

   | # | Module | Type | Size | Rounds | Risk note |
   |---|--------|------|------|--------|-----------|
   | 1 | Preferences table and API | migration | M | 26 | R4: existing users need default rows |
   | 2 | Daily digest job | integration | M | 30 | R2: provider batching limits unknown |
   | 3 | Settings screen | ui | S | 14 | - |
   |   | Integration (+15% of 70) | | | 11 | |
   |   | **Total** | | | **81** | |

   The risks are notes: the rounds stay as counted.
4. The number, from the script on an empty history:
   `calibrate --type feature --rounds 81 --guess 240` prints
   `a_p50 28.8, a_p90 144.0, used "guess cold"`. At cold start the
   rescaled guess is the estimate (240 x 0.12 and 240 x 0.6). The
   rounds-only figure, `calibrate --rounds 81`, is 16.2 / 48.6: shown as a
   cross-check, not reported as the estimate.
5. B: review the plan (10), review the API and the digest diffs (2 x 15,
   medium) and check the screen in a browser (10) = 50 min.
6. C: three CI runs, one per piece, 3 x 10 = 30 min.
7. A+C: 59 min at P50, 2h 54m at P90. Over 2 hours at P90, so propose a
   split along the modules, risky parts first:
   - piece 1: preferences table and API, with a dry run of the default-row
     migration (checkpoint: migration applied on a test database);
   - piece 2: digest job, starting with one real call to the provider
     (checkpoint: a digest received in a test inbox);
   - piece 3: settings screen (checkpoint: screen saves preferences).
   Each piece then gets its own quick estimate when it starts.
8. Record, then report:
   `record --title "Notification preferences and email digest" --type
   feature --rounds 81 --guess-min 240 --b-min 50 --c-min 30 --modules
   '[...]'` stores method `guess cold`, 28.8 / 144.0, confidence LOW.

   "About 1 hour, up to about 3 hours, plus about 50 minutes of your
   time. Confidence is low: there is no history yet, so the range is wide.
   I suggest three pieces (API, digest job, screen), each with its own
   checkpoint. Top risk: the email provider's batching limits; one real
   call at the start of piece 2 settles it."
