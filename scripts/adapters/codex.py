"""Measure OpenAI Codex CLI sessions from their local rollout files.

Codex CLI writes one JSONL file per session under
$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<local time>-<session id>.jsonl
(CODEX_HOME defaults to ~/.codex). Every line has `timestamp` (ISO, UTC),
`type` and `payload`. This adapter only reads those files and never copies
text anywhere.

Mapping onto the common definitions (adapters/common.py):
  human prompt  an `event_msg` item_completed whose item is a UserMessage
                (what the person typed; injected instructions are separate
                developer/user `response_item` messages and are not counted)
  agent event   every other record's timestamp
  round         one model response that called a tool: a `response_item` of
                type custom_tool_call, function_call or local_shell_call,
                counted once per response id. In "code mode" (0.17x) the
                model has a single `exec` tool and one call may run several
                commands; those commands are not separate rounds.
  wait          a CommandExecution item that ran longer than WAIT_THRESHOLD_S
  model         turn_context.model; provider session_meta.model_provider
  tokens        token_usage_record.usage.total_tokens per model response
                (no dollar cost in the log)

Tested against Codex CLI 0.172.0.
"""

import glob
import json
import os
from datetime import datetime

from .common import WAIT_THRESHOLD_S, Trace, compute
from .common import requests as common_requests

NAME = "codex"
SHORT_ID = 23     # ids are UUIDv7: the first 8 characters are only a timestamp
TOOL_CALLS = {"custom_tool_call", "function_call", "local_shell_call"}


def root():
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def _ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _meta(path):
    """The session_meta payload (first records only), or {}."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh):
                if n > 20:
                    break
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("type") == "session_meta":
                    return rec.get("payload") or {}
    except OSError:
        pass
    return {}


def _inside(directory, project):
    real, base = os.path.realpath(directory), os.path.realpath(project)
    try:
        return os.path.commonpath([real, base]) == base
    except ValueError:
        return False


def list_sessions(project_filter=None):
    """Rollout files, newest first; only sessions started inside the project."""
    paths = glob.glob(os.path.join(root(), "sessions", "*", "*", "*", "rollout-*.jsonl"))
    paths.sort(key=os.path.getmtime, reverse=True)
    if not project_filter:
        return paths
    keep = []
    for p in paths:
        cwd = _meta(p).get("cwd")
        if cwd and _inside(cwd, project_filter):
            keep.append(p)
    return keep


def session_name(path):
    meta = _meta(path)
    if meta.get("id"):
        return meta["id"]
    base = os.path.splitext(os.path.basename(path))[0]
    return "-".join(base.split("-")[-5:])


def _text(content):
    parts = []
    for c in content if isinstance(content, list) else []:
        if isinstance(c, dict) and isinstance(c.get("text"), str):
            parts.append(c["text"])
    return "\n".join(parts).strip()


def load(path):
    trace = Trace(session_name(path), NAME)
    rounds, model = {}, None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            ts = _ts(rec.get("timestamp"))
            if ts is None:
                continue
            kind, p = rec.get("type"), rec.get("payload") or {}
            if kind == "session_meta":
                trace.cwd = p.get("cwd") or trace.cwd
                trace.provider = p.get("model_provider") or trace.provider
                git = p.get("git") or {}
                trace.branch = git.get("branch") if isinstance(git, dict) else None
                continue
            if kind == "turn_context":
                model = p.get("model") or model
                trace.cwd = p.get("cwd") or trace.cwd
                continue
            item = p.get("item") or {}
            if kind == "event_msg" and p.get("type") == "item_completed" and item.get("type") == "UserMessage":
                at = (p.get("started_at_ms") or 0) / 1000.0 or ts
                trace.events.append((at, "human"))
                trace.prompts.append((at, _text(item.get("content"))))
                continue
            trace.events.append((ts, "agent"))
            if kind == "response_item" and p.get("type") in TOOL_CALLS:
                rounds.setdefault(p.get("id") or p.get("call_id") or ts, ts)
            elif kind == "token_usage_record":
                usage = p.get("usage") or {}
                trace.usage.append((ts, int(usage.get("total_tokens") or 0), None))
                trace.models.append((ts, model))
            elif kind == "event_msg" and p.get("type") == "item_completed" and \
                    item.get("type") in ("CommandExecution", "FileChange"):
                end = (p.get("completed_at_ms") or 0) / 1000.0
                dur = item.get("duration") or {}
                secs = (dur.get("secs") or 0) + (dur.get("nanos") or 0) / 1e9 if isinstance(dur, dict) else 0
                if item.get("type") == "CommandExecution" and end and secs > WAIT_THRESHOLD_S:
                    trace.waits.append((end - secs, end))
    trace.rounds = list(rounds.values())
    return trace


def measure(path, since=None, turns=0):
    return compute(load(path), since=since, turns=turns)


def requests(path):
    return common_requests(load(path))
