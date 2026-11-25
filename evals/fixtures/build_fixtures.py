#!/usr/bin/env python3
"""Rebuild the eval fixtures (standard library only).

    python3 evals/fixtures/build_fixtures.py

Writes, next to this file:
  store/history.jsonl     12 finished quick-mode items and one open estimate,
                          produced by scripts/reckon.py itself; only ids and
                          times are then set to fixed values
  claude-config/projects/-work-example-app/f1x7ure0-....jsonl
                          a synthetic agent transcript with three requests:
                          30 min of earlier work, the 1-minute request the
                          open estimate is for, and 5 min of later work

Everything is invented. No real transcript or task is involved.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts", "reckon.py")
MODEL = "example-model-1"
SESSION = "f1x7ure0-0000-4000-8000-000000000001"
CWD = "/work/example-app"
DAY = datetime(2026, 3, 10, 9, 0, 0, tzinfo=timezone.utc)   # the transcript starts here

# title, type, gut guess (min), measured agent minutes. actual/guess sorted:
# 0.10 0.12 0.15 0.18 0.20 0.20 0.22 0.25 0.30 0.35 0.40 0.60
# -> g50 = 0.21 (mean of the 6th and 7th), g90 = 0.40 (the 11th of 12)
FINISHED = [
    ("Fix login redirect loop", "bugfix", 20, 5.0),
    ("Add CSV export button", "feature", 50, 9.0),
    ("Rename settings module", "refactor", 20, 2.0),
    ("Update README badges", "docs", 5, 3.0),
    ("Add rate limit to public API", "feature", 50, 11.0),
    ("Fix flaky date test", "bugfix", 25, 3.0),
    ("Dark mode toggle", "ui", 30, 6.0),
    ("Bump CI runner image", "infra", 10, 2.0),
    ("Explain the retry policy", "research", 10, 3.0),
    ("Fix currency rounding", "bugfix", 20, 7.0),
    ("Add empty state to orders page", "ui", 15, 6.0),
    ("Remove dead feature flag", "refactor", 20, 3.0),
]


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(env, *args):
    out = subprocess.run([sys.executable, SCRIPT, *[str(a) for a in args]], env=env, capture_output=True,
                         text=True, check=True)
    return json.loads(out.stdout)


def build_history(target):
    tmp = tempfile.mkdtemp(prefix="reckon-fixture-")
    try:
        env = dict(os.environ, RECKON_HOME=os.path.join(tmp, "store"), HOME=tmp)
        env.pop("RECKON_HASH_TITLES", None)
        for title, task_type, guess, actual in FINISHED:
            made = run(env, "record", "--title", title, "--type", task_type, "--guess-min", guess,
                       "--model", MODEL, "--agent", "example-agent")
            run(env, "actual", made["id"], "--agent-min", actual, "--model", MODEL)
        run(env, "record", "--title", "Fix footer typo", "--type", "docs", "--guess-min", 5,
            "--model", MODEL, "--agent", "example-agent")
        with open(os.path.join(tmp, "store", "history.jsonl"), encoding="utf-8") as fh:
            items = [json.loads(line) for line in fh]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for n, item in enumerate(items):
        if n < len(FINISHED):
            when = DAY - timedelta(days=len(FINISHED) - n, hours=-n % 5)
        else:
            when = DAY + timedelta(seconds=2105)     # 5 s into the second request of the transcript
        slug = item["id"][11:]
        item.update(id="fixture-" + slug, date=when.strftime("%Y-%m-%d"), ts=iso(when), repo="example-app")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item) + "\n")


def build_transcript(target):
    turns = [
        (0, [60 * k for k in range(1, 30)], 1800, "Add pagination to the orders API and its tests."),
        (2100, [2120, 2140, 2160], 2165, "How long will it take to fix the typo in the footer? Then do it."),
        (3000, [3000 + 60 * k for k in range(1, 5)], 3300, "Now bump the copyright year everywhere."),
    ]
    os.makedirs(os.path.dirname(target), exist_ok=True)
    n = 0
    with open(target, "w", encoding="utf-8") as fh:
        def put(rec, at):
            rec.update(timestamp=iso(DAY + timedelta(seconds=at)), cwd=CWD, sessionId=SESSION)
            fh.write(json.dumps(rec) + "\n")
        for prompt_at, rounds, end_at, prompt in turns:
            put({"type": "user", "message": {"role": "user", "content": prompt}}, prompt_at)
            for at in rounds:
                n += 1
                put({"type": "assistant", "message": {
                    "id": f"msg{n}", "model": MODEL, "role": "assistant",
                    "content": [{"type": "tool_use", "id": f"tu{n}", "name": "Read", "input": {}}]}}, at)
                put({"type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": f"tu{n}", "content": "ok"}]}}, at + 1)
            put({"type": "assistant", "message": {"id": f"end{n}", "model": MODEL, "role": "assistant",
                                                  "content": [{"type": "text", "text": "Done."}]}}, end_at)


if __name__ == "__main__":
    build_history(os.path.join(HERE, "store", "history.jsonl"))
    build_transcript(os.path.join(HERE, "claude-config", "projects", "-work-example-app", SESSION + ".jsonl"))
    print("fixtures written under", HERE)
