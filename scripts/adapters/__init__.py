"""Session adapters: measure finished agent work from local logs.

An adapter is a module with:
  NAME                       short id used on the command line (--agent NAME)
  list_sessions(project)     transcript paths, newest first; `project` is a
                             path used to keep only that project's sessions,
                             or None for all
  measure(path)              dict with at least: session_id, start, rounds,
                             agent_min, wait_min, idle_min, human_turns,
                             wall_min, title; or None when there is nothing
                             to measure

To support another agent, add a module here and register it below. Agents
without logs can always record actuals by hand (references/calibration.md).
"""

from . import claude_code

ADAPTERS = {claude_code.NAME: claude_code}
