# Output template

## Quick mode

One or two lines in the conversation; no file beyond the history line.

```markdown
About <A_p50>-<A_p90> of agent time (P50-P90, <calibrated on n past tasks | cold start, low confidence>).
<Only if needed:> Needs about <B> of your time (<what>), and a wait of about <C> (<what>).
```

Example: "About 2-6 minutes of agent time (P50-P90, calibrated on 40 past
tasks). Needs your review of the diff, about 5 minutes."

## Full mode

Fill this in, show it to the person, and save it as
`.reckon/estimates/<YYYY-MM-DD>-<slug>.md`.

```markdown
## Estimate: <task title>

Method: <rounds cold | rounds calibrated (n=<n>, <scope>) | rounds+guess> · Read level: <light | standard | deep> · Confidence: <LOW | MEDIUM | HIGH>

| # | Module | Type | Size | Rounds | Risk | Rounds after risk |
|---|--------|------|------|--------|------|-------------------|
| 1 | ...    | ...  | S    | 16     | R2 x1.5 | 24 |
| 2 | ...    | ...  | XS   | 6      | -    | 6 |
|   | Integration (+15%) | | | | | 5 |
|   | **Total** | | | | | **35** |

| Bucket | P50 | P90 | Basis |
|--------|-----|-----|-------|
| A. Agent time | 7 min | 21 min | 35 rounds x 0.2 min, x3 spread |
| B. Human touch | 25 min | | plan review 10 + device test 15 |
| C. Wait | 10 min | | one CI run (template default) |
| **Agent-side elapsed (A+C)** | **17 min** | **31 min** | |

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
- Quick mode never shows the raw gut guess: it is an input, not an
  estimate.

## Worked example

Request: "Add a CSV export button to the orders page. Same columns as the
table, respects the current filters."

1. Quick read: the profile says the orders page lives in
   `web/src/pages/orders/`, the API in `api/orders/`, tests use Vitest and
   pytest, and CI takes about 8 minutes.
2. Modules (each includes its own tests and commit):
   - API endpoint that streams filtered orders as CSV: `feature`, M, 30
     rounds, no risk.
   - Button and download handling on the page: `ui`, S, 12 rounds, no
     risk.
   - Integration: +15% of 42 = 6. Total 48 rounds, close to the median
     task, which is plausible for a two-layer feature.
3. A: 48 x 0.2 = 10 min P50; x3 = 29 min P90 (cold start).
4. B: the person reviews a medium diff (15 min) and checks the file in a
   browser (10 min) = 25 min.
5. C: one CI run, 8 min (from the profile Notes).
6. Report: "About 18 minutes, up to about 37 minutes, plus about 25
   minutes of your time." Confidence LOW (cold start).
