"""Session adapters: measure finished agent work from local logs.

An adapter is a module with:
  NAME                       short id used on the command line (--agent NAME)
  list_sessions(project)     session handles (usually file paths), newest
                             first; `project` is a path used to keep only
                             that project's sessions, or None for all
  session_name(handle)       the session id, matched by --session prefixes
  SHORT_ID (optional)        characters of the id kept in history (default 8)
  measure(path, since=None, turns=0)
                             dict with at least: session_id, start, rounds,
                             agent_min, wait_min, idle_min, human_turns,
                             wall_min, title, scope, prompts; or None when
                             there is nothing to measure.
                             Without `since` it measures the whole session
                             (scope "session"). With `since` (Unix time of
                             the estimate) it measures only that request
                             (scope "request"): the turn in which `since`
                             falls, from `since` on, or the next turn when
                             the agent called no tool after `since` in that
                             turn; plus `turns` following turns.
                             `prompts` is the number of human prompts in the
                             whole session. Optional keys: provider, tokens,
                             cost_usd.
  requests(handle)           each human prompt with its text, rounds and
                             tokens until the next one (used by replay.py)

The built-in adapters build a common.Trace and measure it with
common.compute, so the definitions are the same for every agent. Contract
tests: tests/test_adapters.py.

To support another agent, add a module here and register it below. Agents
without logs can always record actuals by hand (references/calibration.md).
"""

from . import claude_code, codex, opencode

ADAPTERS = {m.NAME: m for m in (claude_code, opencode, codex)}
