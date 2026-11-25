# Evidence

Why the skill works the way it does. Numbers are as reported by each
source; check the source before quoting them elsewhere. The external
figures below were not re-checked against their sources when 0.3.1 was
prepared, and one (AgentTime) has no link: treat it as secondhand.

## Agents overestimate agent-built work

- **AgentTime** (described as a MATS study, October 2026, 222 tasks).
  **Secondhand: no primary source could be linked or checked.** As
  reported, frontier agents forecast too high in 63-83% of tasks, with a
  typical error of 2.6-3.8x, often guessing about 15 minutes for work that
  took a few, attributed to anchoring on human timelines from training
  data. reckon does not depend on these figures; its own benchmark
  measured the overestimate directly (about 7x for a plain answer).
- **Why Claude Code time estimates are poor** (Towards Data Science): the
  same anchoring explanation, from practice.
  https://towardsdatascience.com/why-claude-code-time-estimates-are-poor/
- **agent-estimation** (ZhangHanDong, MIT): proposes estimating in
  tool-call rounds and converting to wall-clock time only at the end. This
  skill keeps that idea but measures minutes per round instead of assuming
  2-4 minutes. https://skills.sh/zhanghandong/agent-estimation/agent-estimation

## Agent durations are heavy-tailed

- **agentpace** (3,383 measured Claude Code and Codex runs; code MIT, data
  CC0): half the runs finished within 2 minutes and one in ten took more
  than 20; a whole session's P90 is typically about 2.7x its P50; the
  median single-task miss of the P50 is 78%. A fixed 3 minutes per round
  scored clearly worse than measured rates, and a human-time guess was
  worst of all. https://github.com/mohamedzhioua/agentpace
- **METR time horizons**: the task length an agent completes with 80%
  reliability is about 5x shorter than the length it completes with 50%
  reliability, and the horizon has been doubling roughly every 7 months.
  Long unsplit tasks fail more often, not just take longer.
  https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/
- **ai-time-estimator** (noambrand): Monte Carlo ranges from measured
  transcript rates; reports 87% coverage overall but 58% for jobs over 20
  minutes: accuracy drops as work gets longer, one more reason to split
  long tasks into pieces that can be checked on their own.
  https://github.com/noambrand/ai-time-estimator

These are why reckon reports P50 and P90 instead of one number, uses a
cold-start spread of 3x (measured speed alone spreads about 2.6x between
P50 and P90, and round counts add their own error), and splits large
work.

## Measured speed on real sessions

The cold-start speed of 0.2 minutes per round comes from measuring 517
Claude Code sessions with five or more rounds, using the definitions in
`scripts/adapters/claude_code.py`: median 0.21, 75th percentile 0.32, 90th
percentile 0.55 minutes of agent time per round. Speed depends on the
model, reasoning effort and tool latency, so replace it with your own
history as soon as you have ten measured sessions.

## The bottleneck moves to people and waiting

- **DORA 2025 State of AI-assisted Software Development**: AI amplifies
  existing team strengths and weaknesses; throughput gains shift the
  bottleneck downstream to review, testing and delivery.
  https://dora.dev/dora-report-2025/
- **METR developer study (2025)**: experienced open-source developers were
  19% slower with AI tools on their own repositories while believing they
  were about 20% faster. Felt speed is not measured speed.
  https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/

This is why reckon reports human touch time and wait time separately from
agent time, and why calendar time is driven by B and C.

## Validation run for this skill (0.1.0)

Estimates were made blind from the first message of real Claude Code
tasks, by an agent that never saw the outcomes, and compared with the
measured agent time of each task.

- First set (25 tasks, used to tune the anchors): the original anchors
  overcounted rounds about 3x; the plain "how long?" answer was 6.3x too
  high at the median.
- Second set (20 different tasks, anchors frozen; this result did **not**
  hold on the larger benchmark, where the same version's cold-start error
  was 296%): cold-start median error
  31% with 100% of actuals at or below P90; calibrated leave-one-out median
  error 30% with 85% at or below P90 and 50% at or below P50 (cold start:
  55% at or below P50); the plain
  answer was 6.2x too high (median error 521%).
- Wait time was overestimated about 3.6x in total, mostly waits that never
  happened. Human touch time was not validated, because those tasks were
  handed over by a coordinating agent.

The broader benchmark behind 0.2.0 and 0.3.0 (200 test requests split by
time, eight comparison methods, ablations, repeatability, cost, and public
SWE-bench tasks) is in [benchmark.md](benchmark.md). It replaces this
first validation.
