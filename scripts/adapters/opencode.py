"""Measure OpenCode sessions from its local SQLite database.

OpenCode (2.x) keeps every session in one SQLite file,
$XDG_DATA_HOME/opencode/opencode.db (default ~/.local/share/opencode), with
tables `session_v2` (one row per session: directory, title, model) and
`session_message` (one row per message: type user / assistant / idle / ...,
a `data` JSON column). This adapter opens it read-only and never copies text
anywhere. Override the file with RECKON_OPENCODE_DB.

Mapping onto the common definitions (adapters/common.py):
  human prompt  a `user` message
  agent event   an assistant message's start, each tool call's start, run and
                completion, and the message's completion
  round         an assistant message with at least one tool call
  wait          a shell-like tool call (shell, bash, execute) running longer
                than WAIT_THRESHOLD_S
  model         assistant data.model.id; provider data.model.providerID
  tokens / cost per assistant message (data.tokens, data.cost)

Sessions started by another session (subagents, `parent_id` set) are not
listed; their time shows in the parent as the time it waited on them.

A "path" for this adapter is "<database file>#<session id>". Tested against
OpenCode 2.2.5; an older or newer schema is reported, not guessed at.
"""

import json
import os
import sqlite3

from .common import WAIT_THRESHOLD_S, Trace, compute
from .common import requests as common_requests

NAME = "opencode"
SHORT_ID = 16     # "ses_" plus 12 characters
WAIT_TOOLS = {"shell", "bash", "execute"}


def db_path():
    if os.environ.get("RECKON_OPENCODE_DB"):
        return os.environ["RECKON_OPENCODE_DB"]
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "opencode", "opencode.db")


def _connect(path):
    con = sqlite3.connect("file:" + path + "?mode=ro", uri=True, timeout=5)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    if not {"session_v2", "session_message"} <= tables:
        con.close()
        raise RuntimeError(f"{path}: not an OpenCode 2.x database (no session_v2/session_message tables)")
    return con


def _split(path):
    db, _, sid = path.rpartition("#")
    return db, sid


def _inside(directory, project):
    real, root = os.path.realpath(directory), os.path.realpath(project)
    try:
        return os.path.commonpath([real, root]) == root
    except ValueError:
        return False


def list_sessions(project_filter=None):
    """Return "<db>#<session id>" for each top-level session, newest first."""
    db = db_path()
    if not os.path.exists(db):
        return []
    con = _connect(db)
    try:
        rows = con.execute("select id, directory from session_v2 where parent_id is null "
                           "order by time_updated desc").fetchall()
    finally:
        con.close()
    return [f"{db}#{sid}" for sid, directory in rows
            if not project_filter or (directory and _inside(directory, project_filter))]


def session_name(path):
    return _split(path)[1]


def _ms(value):
    return value / 1000.0 if isinstance(value, (int, float)) and value > 0 else None


def load(path):
    db, sid = _split(path)
    con = _connect(db)
    try:
        row = con.execute("select directory, title, model from session_v2 where id = ?", (sid,)).fetchone()
        if not row:
            return None
        messages = con.execute("select type, time_created, data from session_message "
                               "where session_id = ? order by seq", (sid,)).fetchall()
    finally:
        con.close()
    trace = Trace(sid, NAME)
    trace.cwd, trace.title = row[0], row[1]
    try:
        trace.provider = (json.loads(row[2]) or {}).get("providerID") if row[2] else None
    except ValueError:
        pass
    for kind, created, raw in messages:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        times = data.get("time") or {}
        start = _ms(times.get("created")) or _ms(created)
        if start is None:
            continue
        if kind == "user":
            trace.events.append((start, "human"))
            trace.prompts.append((start, (data.get("text") or "").strip()))
            continue
        if kind != "assistant":
            continue
        trace.events.append((start, "agent"))
        model = data.get("model") or {}
        trace.models.append((start, model.get("id")))
        trace.provider = model.get("providerID") or trace.provider
        tools = [c for c in data.get("content") or [] if isinstance(c, dict) and c.get("type") == "tool"]
        if tools:
            first = min((_ms((c.get("time") or {}).get("created")) or start) for c in tools)
            trace.rounds.append(first)
        for c in tools:
            t = c.get("time") or {}
            for key in ("created", "ran", "completed"):
                if _ms(t.get(key)):
                    trace.events.append((_ms(t[key]), "agent"))
            ran, done = _ms(t.get("ran")) or _ms(t.get("created")), _ms(t.get("completed"))
            if c.get("name") in WAIT_TOOLS and ran and done and done - ran > WAIT_THRESHOLD_S:
                trace.waits.append((ran, done))
        end = _ms(times.get("completed"))
        if end:
            trace.events.append((end, "agent"))
        tok = data.get("tokens") or {}
        if tok:
            cache = tok.get("cache") or {}
            total = sum(int(tok.get(k) or 0) for k in ("input", "output", "reasoning")) + \
                sum(int(cache.get(k) or 0) for k in ("read", "write"))
            cost = data.get("cost")
            trace.usage.append((start, total, float(cost) if isinstance(cost, (int, float)) else None))
    return trace


def measure(path, since=None, turns=0):
    trace = load(path)
    return compute(trace, since=since, turns=turns) if trace else None


def requests(path):
    trace = load(path)
    return common_requests(trace) if trace else []


