"""Measure Claude Code sessions from their local JSONL transcripts.

Claude Code writes one transcript per session under
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl (override the root with
CLAUDE_CONFIG_DIR). This adapter only reads those files. It returns numbers
and a short title; it never copies prompt text or code anywhere else.

Definitions (see references/calibration.md):
  round        one assistant API response on the main chain that calls >= 1 tool
  turn         from a human prompt to the last agent event before the next one
  agent_min    time inside turns spent thinking, calling tools and reading
               results, minus wait_min and idle_min
  wait_min     time inside shell/monitor tool calls longer than
               WAIT_THRESHOLD_S (builds, CI watches, deploys, long test runs)
  idle_min     silent gaps inside a turn longer than IDLE_THRESHOLD_S that no
               long tool call explains (the agent was parked, e.g. waiting
               for a background job or another agent to report back)
  human_turns  human prompts after the first one (answers, corrections, go-aheads)
  human_gap_min time between the end of a turn and the next human prompt
  wall_min     first event to last event
  model        the model that wrote most of the agent's responses

Known limits of these definitions:
  subagents    a subagent's own records (sidechain) are skipped, so its rounds
               are not counted. The main agent's time blocked on a subagent
               call is a silent gap: it counts as agent_min up to
               IDLE_THRESHOLD_S per gap and as idle_min (dropped) above it.
  background   a shell command started in the background returns at once, so
               it is never a wait. Time spent polling it counts as agent_min;
               a single silent gap over IDLE_THRESHOLD_S counts as idle_min.

Measuring one request instead of the whole session: measure(path, since=T)
measures the turn in which T falls, from T on. If the agent called no tool
between T and the end of that turn (it only reported the estimate and
stopped), the request is the next turn instead (the go-ahead). `turns=N`
adds the N turns that follow.
"""

import glob
import json
import os
import re
from datetime import datetime

from .common import IDLE_THRESHOLD_S, WAIT_THRESHOLD_S, Trace, compute  # noqa: F401
from .common import requests as common_requests
from .common import window as _window  # noqa: F401

NAME = "claude-code"
WAIT_TOOLS = {"Bash", "BashOutput", "Monitor", "PowerShell"}
AUTO_PREFIXES = (
    "[Request interrupted",
    "<local-command",
    "<task-notification",
    "This session is being continued",
    "Caveat:",
)


def root():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def list_sessions(project_filter=None):
    """Return transcript paths, newest first. project_filter matches the cwd."""
    paths = glob.glob(os.path.join(root(), "*", "*.jsonl"))
    paths.sort(key=os.path.getmtime, reverse=True)
    if not project_filter:
        return paths
    enc = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(project_filter))
    keep = []
    for p in paths:
        folder = os.path.basename(os.path.dirname(p))
        # The folder name is a lossy encoding of the cwd; callers still
        # compare the transcript's real cwd by path component.
        if folder == enc or folder.startswith(enc + "-"):
            keep.append(p)
    return keep


def _ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "tool_result":
                    return None
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
        return "\n".join(parts) if parts else None
    return None


_INJECTED = re.compile(
    r"<(system-reminder|local-command-caveat|local-command-stdout|command-message|command-args)>.*?</\1>",
    re.DOTALL,
)
_COMMAND = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)


def clean_prompt(text):
    """Drop harness-injected blocks; return what the person actually typed."""
    if text is None:
        return None
    text = _INJECTED.sub("", text)
    text = _COMMAND.sub(lambda m: m.group(1).strip(), text)
    return text.strip()


def _is_human(rec):
    if rec.get("type") != "user" or rec.get("isSidechain") or rec.get("isMeta"):
        return False
    if rec.get("isCompactSummary") or rec.get("isVisibleInTranscriptOnly"):
        return False
    if rec.get("promptSource") == "system" or rec.get("turnOrigin") == "task_notification":
        return False
    text = clean_prompt(_text_of((rec.get("message") or {}).get("content")))
    if not text or text.startswith(AUTO_PREFIXES):
        return False
    return True


def load(path):
    """Read one transcript into a common.Trace."""
    trace = Trace(os.path.splitext(os.path.basename(path))[0], NAME)
    round_ts = {}        # round id -> timestamp
    tool_start = {}      # tool_use_id -> (ts, name)
    seen_usage = set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            kind = rec.get("type")
            if kind == "ai-title":
                trace.title = rec.get("aiTitle") or trace.title
            elif kind == "summary" and not trace.title:
                trace.title = rec.get("summary")
            elif kind == "pr-link" and rec.get("prUrl"):
                if rec["prUrl"] not in trace.prs:
                    trace.prs.append(rec["prUrl"])
            elif kind == "cost-state":
                trace.cost = {"cost_usd": round(rec.get("totalCostUSD") or 0, 2),
                              "lines_changed": (rec.get("totalLinesAdded") or 0) + (rec.get("totalLinesRemoved") or 0)}
            if kind not in ("user", "assistant") or rec.get("isSidechain"):
                continue
            ts = _ts(rec.get("timestamp"))
            if ts is None:
                continue
            trace.cwd = rec.get("cwd") or trace.cwd
            trace.branch = rec.get("gitBranch") or trace.branch
            msg = rec.get("message") or {}
            if kind == "assistant":
                if msg.get("model") and not msg["model"].startswith("<"):
                    trace.models.append((ts, msg["model"]))
                mid = msg.get("id") or rec.get("requestId") or rec.get("uuid")
                usage = msg.get("usage") or {}
                if usage and mid not in seen_usage:
                    seen_usage.add(mid)
                    trace.usage.append((ts, sum(int(usage.get(k) or 0) for k in (
                        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")), None))
                content = msg.get("content") or []
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        round_ts.setdefault(mid, ts)
                        tool_start[block.get("id")] = (ts, block.get("name"))
                trace.events.append((ts, "agent"))
            else:
                if _is_human(rec):
                    trace.events.append((ts, "human"))
                    trace.prompts.append((ts, clean_prompt(_text_of(msg.get("content"))) or ""))
                    continue
                content = msg.get("content")
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        start = tool_start.pop(block.get("tool_use_id"), None)
                        if start and start[1] in WAIT_TOOLS and ts - start[0] > WAIT_THRESHOLD_S:
                            trace.waits.append((start[0], ts))
                trace.events.append((ts, "agent"))
    trace.rounds = list(round_ts.values())
    return trace


def session_name(path):
    return os.path.splitext(os.path.basename(path))[0]


def measure(path, since=None, turns=0):
    """Measure one transcript, or one request in it when `since` is given.

    `since` is a Unix time (when the estimate was recorded); see the module
    docstring for which turns are then measured. Returns a dict, or None when
    the transcript holds no human prompt or nothing after `since`."""
    return compute(load(path), since=since, turns=turns)


def requests(path):
    return common_requests(load(path))

