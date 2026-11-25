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
"""

import glob
import json
import os
import re
from datetime import datetime

NAME = "claude-code"
WAIT_THRESHOLD_S = 120
IDLE_THRESHOLD_S = 600
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


def measure(path):
    """Measure one transcript. Returns a dict or None if it holds no human prompt."""
    events = []          # (ts, kind) for main-chain user/assistant records
    humans = []          # timestamps of human prompts
    first_prompt = None
    rounds = set()
    tool_start = {}      # tool_use_id -> (ts, name)
    waits = []          # (start, end) of long shell/monitor tool calls
    title = None
    prs = []
    cost = None
    session_id = os.path.splitext(os.path.basename(path))[0]
    cwd = branch = None
    models = {}

    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            kind = rec.get("type")
            if kind == "ai-title":
                title = rec.get("aiTitle") or title
            elif kind == "summary" and not title:
                title = rec.get("summary")
            elif kind == "pr-link" and rec.get("prUrl"):
                if rec["prUrl"] not in prs:
                    prs.append(rec["prUrl"])
            elif kind == "cost-state":
                cost = rec
            if kind not in ("user", "assistant") or rec.get("isSidechain"):
                continue
            ts = _ts(rec.get("timestamp"))
            if ts is None:
                continue
            cwd = rec.get("cwd") or cwd
            branch = rec.get("gitBranch") or branch
            msg = rec.get("message") or {}
            if kind == "assistant":
                if msg.get("model") and not msg["model"].startswith("<"):
                    models[msg["model"]] = models.get(msg["model"], 0) + 1
                content = msg.get("content") or []
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        rounds.add(msg.get("id") or rec.get("requestId") or rec.get("uuid"))
                        tool_start[block.get("id")] = (ts, block.get("name"))
                events.append((ts, "agent"))
            else:
                if _is_human(rec):
                    humans.append(ts)
                    events.append((ts, "human"))
                    text = clean_prompt(_text_of(msg.get("content")))
                    if first_prompt is None and not text.startswith("/"):
                        first_prompt = text
                    continue
                content = msg.get("content")
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        start = tool_start.pop(block.get("tool_use_id"), None)
                        if start and start[1] in WAIT_TOOLS and ts - start[0] > WAIT_THRESHOLD_S:
                            waits.append((start[0], ts))
                events.append((ts, "agent"))

    if not humans:
        return None
    events.sort()
    agent_s = wait_s = idle_s = gap_s = 0.0
    in_turn = False
    prev = None
    for ts, who in events:
        if who == "human":
            if in_turn and prev is not None:
                gap_s += max(0.0, ts - prev)
            in_turn, prev = True, ts
            continue
        if not in_turn or prev is None:
            continue
        step = max(0.0, ts - prev)
        covered = sum(max(0.0, min(ts, b) - max(prev, a)) for a, b in waits)
        covered = min(step, covered)
        wait_s += covered
        rest = step - covered
        if rest > IDLE_THRESHOLD_S:
            idle_s += rest
        else:
            agent_s += rest
        prev = ts

    if not title and first_prompt:
        title = first_prompt.splitlines()[0][:80]
    result = {
        "session_id": session_id,
        "source": NAME,
        "title": title or session_id,
        "start": datetime.fromtimestamp(events[0][0]).isoformat(timespec="minutes"),
        "cwd": cwd,
        "branch": branch,
        "model": max(models, key=models.get) if models else None,
        "prs": prs,
        "rounds": len(rounds),
        "agent_min": round(agent_s / 60, 1),
        "wait_min": round(wait_s / 60, 1),
        "idle_min": round(idle_s / 60, 1),
        "human_turns": max(0, len(humans) - 1),
        "human_gap_min": round(gap_s / 60, 1),
        "wall_min": round((events[-1][0] - events[0][0]) / 60, 1),
    }
    if cost:
        result["cost_usd"] = round(cost.get("totalCostUSD") or 0, 2)
        result["lines_changed"] = (cost.get("totalLinesAdded") or 0) + (cost.get("totalLinesRemoved") or 0)
    result["first_prompt"] = first_prompt or ""
    return result
