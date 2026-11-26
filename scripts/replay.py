#!/usr/bin/env python3
"""Replay: see what reckon would have said about work you already did.

Picks a few recent requests from your own agent transcripts (default 12),
asks an agent for its gut guess on each one in a single batched call, then
shows, side by side with how long each request really took:

  - the agent's raw answer ("how many minutes will this take you?"),
  - reckon at cold start (no history of yours),
  - reckon once calibrated on your history (leave-one-out over the sample),

plus what the estimating itself cost: wall time, tokens and dollars, total
and per request, and as a share of the work it sized.

This is a demonstration on your own data, not part of the skill: SKILL.md
never loads it, and it writes nothing to history.jsonl. The report is saved
under <store>/replay/.

Privacy: the request text (first message of each request, and the message
before it as context) is sent to the model through your own `claude` CLI,
the same way your normal agent sessions are. Use --list to see exactly what
would be sent, or --prompt-only / --guesses to use another agent.

Usage:
  python3 replay.py                    # 12 recent requests of this repo
  python3 replay.py -n 6 --model haiku # fewer requests, cheaper model
  python3 replay.py --list             # show the selected requests only
  python3 replay.py --prompt-only      # print the prompt for another agent
  python3 replay.py --guesses g.jsonl  # score guesses you got elsewhere
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reckon  # noqa: E402
from adapters import ADAPTERS  # noqa: E402

DEFAULT_N = 12
MAX_N = 30
MAX_PER_SESSION = 3
MIN_PROMPT_CHARS = 25
MIN_ROUNDS = 2
MIN_AGENT_MIN = 0.3
TEXT_LIMIT = 600
CONTEXT_LIMIT = 300
ACTIVE_S = 600          # a request whose session changed this recently may still be running

INSTRUCTIONS = """You are replaying the reckon skill above on requests that were already done.
For each request below, do step 1 of Quick mode only: your gut guess of how many
minutes of your own working time it would take you, the AI coding agent that does
the work (full repository and shell access in real use). One number per request.
Do not read code or decompose; you have no tools here, only the request text and,
when given, the message before it. Return ONLY JSON lines, one per request, no prose:
{"id": "<id>", "guess_min": <number>}"""


# ---------------------------------------------------------------- transcripts

def _usage_tokens(usage):
    return sum(int(usage.get(k) or 0) for k in
               ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))


def clip(text, limit):
    """Keep the start and the end of a long message: the ask is usually in one of them."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = limit * 2 // 5
    return text[:head] + " [...] " + text[-(limit - head):]


def select(project, n, any_project=False, agent="claude-code"):
    """The newest eligible requests, at most MAX_PER_SESSION per session."""
    ad = ADAPTERS[agent]
    paths = ad.list_sessions(None if any_project else project)
    picked, seen = [], set()
    now = time.time()
    for path in paths:
        sid = ad.session_name(path)
        candidates = []
        for p in reversed(ad.requests(path)):
            if len(candidates) >= MAX_PER_SESSION:
                break
            key = (int(p["ts"]), p["text"][:200])
            if key in seen:          # the same request in a resumed or forked copy of a session
                continue
            seen.add(key)
            if p["last"] and now - p["last_event"] < ACTIVE_S:   # may still be running
                continue
            if p["rounds"] < MIN_ROUNDS or len(p["text"]) < MIN_PROMPT_CHARS or p["text"].startswith("/"):
                continue
            if not any_project and p["cwd"] and not reckon._in_project({"cwd": p["cwd"]}, project):
                continue
            m = ad.measure(path, since=p["ts"])
            if not m or m["agent_min"] < MIN_AGENT_MIN:
                continue
            candidates.append(dict(p, session=sid[:getattr(ad, "SHORT_ID", 8)], agent=ad.NAME, agent_min=m["agent_min"], wait_min=m["wait_min"],
                                   measured_rounds=m["rounds"], model=m.get("model")))
        picked.extend(candidates)
        if len(picked) >= n * 3:
            break
    picked.sort(key=lambda r: r["ts"], reverse=True)
    picked = picked[:n]
    for k, r in enumerate(picked, 1):
        r["id"] = f"r{k:02d}"
    return picked


# ---------------------------------------------------------------- estimating

def build_prompt(reqs):
    blocks = []
    for r in reqs:
        block = f"### {r['id']}\n"
        if r["context"]:
            block += f"Message before it (context): {clip(r['context'], CONTEXT_LIMIT)}\n"
        block += f"Request: {clip(r['text'], TEXT_LIMIT)}"
        blocks.append(block)
    return "\n\n".join(blocks)


def system_prompt():
    with open(os.path.join(os.path.dirname(HERE), "SKILL.md"), encoding="utf-8") as fh:
        return "# SKILL.md\n\n" + fh.read() + "\n\n" + INSTRUCTIONS


def parse_guesses(text):
    out = {}
    for line in (text or "").splitlines():
        line = line.strip().strip(",")
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        try:
            g = float(row.get("guess_min"))
        except (TypeError, ValueError):
            continue
        if row.get("id") and g > 0:
            out[str(row["id"])] = g
    return out


def ask_claude(reqs, model, claude_bin):
    """One batched call. Returns (guesses, cost dict)."""
    cmd = [claude_bin, "-p", build_prompt(reqs), "--output-format", "json", "--no-session-persistence",
           "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--system-prompt", system_prompt()]
    if model:
        cmd += ["--model", model]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=tempfile.gettempdir())
    sec = time.time() - t0
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        sys.exit("the claude CLI did not return JSON:\n" + (proc.stderr or proc.stdout)[-800:])
    if data.get("is_error"):
        sys.exit("the claude CLI reported an error: " + str(data.get("result"))[:500])
    usage = data.get("usage") or {}
    models = list((data.get("modelUsage") or {}).keys())
    cost = {"calls": 1, "seconds": round(sec, 1), "tokens": _usage_tokens(usage),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "usd": data.get("total_cost_usd"), "model": ", ".join(models) or model or "CLI default"}
    return parse_guesses(data.get("result")), cost


# ---------------------------------------------------------------- scoring

def _item(r, guess):
    return {"estimate": {"guess_min": guess}, "actual": {"agent_min": r["agent_min"], "model": r.get("model")}}


def score(reqs, guesses):
    rows = []
    for r in reqs:
        g = guesses.get(r["id"])
        if not g:
            continue
        cold = reckon.apply_guess(g, {})
        others = [_item(o, guesses[o["id"]]) for o in reqs if o is not r and guesses.get(o["id"])]
        cal = reckon.calibration(others)
        (c50, c90), used = reckon.combine(None, g, cal)
        rows.append(dict(r, guess=g, cold=cold, cal=(c50, c90), cal_used=used,
                         cal_n=(cal.get("guess") or {}).get("n", 0)))
    return rows


def _median(values):
    v = sorted(values)
    if not v:
        return None
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def summary(rows, key):
    """key(row) -> (p50, p90 or None)."""
    errs, in50, in90, logs = [], 0, 0, []
    for r in rows:
        p50, p90 = key(r)
        a = r["agent_min"]
        errs.append(abs(a - p50) / a)
        in50 += a <= p50
        in90 += p90 is not None and a <= p90
        logs.append(math.log(a / p50))
    n = len(rows)
    return {"median_error_pct": round(100 * _median(errs)), "at_or_below_p50_pct": round(100 * in50 / n),
            "at_or_below_p90_pct": round(100 * in90 / n) if key(rows[0])[1] is not None else None,
            "actual_over_p50": round(math.exp(sum(logs) / n), 2)}


# ---------------------------------------------------------------- report

def _mins(x):
    if x is None:
        return "-"
    if x >= 90:
        return f"{x / 60:.1f} h"
    return f"{x:.1f}" if x < 10 else f"{x:.0f}"


def _range(p):
    return f"{_mins(p[0])}-{_mins(p[1])}"


def _short(text, width=48):
    line = " ".join(text.split())
    return line if len(line) <= width else line[:width - 1] + "…"


def report(rows, cost, project, hide_text=False):
    n = len(rows)
    raw = summary(rows, lambda r: (r["guess"], None))
    cold = summary(rows, lambda r: r["cold"])
    calibrated = all(r["cal_used"] == "guess calibrated" for r in rows)
    cal = summary(rows, lambda r: r["cal"]) if calibrated else None
    work_min = sum(r["agent_min"] for r in rows)
    work_tok = sum(r["work_tokens"] for r in rows)
    lines = [f"# reckon replay: {n} past requests", "",
             f"Agent: {rows[0].get('agent', 'claude-code')}. Project: {project or 'all projects'}. Made {datetime.now().isoformat(timespec='minutes')}.",
             "Minutes are agent minutes (the agent's working time, waits excluded).", "",
             "| # | Request | Actual | Agent's raw answer | reckon, cold start (P50-P90) | reckon, calibrated (P50-P90) |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        mark = lambda p: "✓" if r["agent_min"] <= p[1] else "✗"
        req = "" if hide_text else _short(r["text"])
        cal_cell = f"{_range(r['cal'])} {mark(r['cal'])}" if calibrated else "n/a"
        lines.append(f"| {r['id'][1:]} | {req} | {_mins(r['agent_min'])} | {_mins(r['guess'])} | "
                     f"{_range(r['cold'])} {mark(r['cold'])} | {cal_cell} |")
    lines += ["", "✓ = the actual was at or below the P90.", "",
              "| | Median error | Actual / P50 | At or below P50 | At or below P90 |", "|---|---|---|---|---|",
              f"| Agent's raw answer | {raw['median_error_pct']}% | {raw['actual_over_p50']} | "
              f"{raw['at_or_below_p50_pct']}% | n/a |",
              f"| reckon, cold start | {cold['median_error_pct']}% | {cold['actual_over_p50']} | "
              f"{cold['at_or_below_p50_pct']}% | {cold['at_or_below_p90_pct']}% |",
              (f"| reckon, calibrated | {cal['median_error_pct']}% | {cal['actual_over_p50']} | "
               f"{cal['at_or_below_p50_pct']}% | {cal['at_or_below_p90_pct']}% |" if cal else
               f"| reckon, calibrated | n/a: needs at least {reckon.MIN_ALL_ITEMS + 1} requests (-n) | | | |"), ""]
    lines += ["**Cost of estimating** (" + cost.get("model", "?") + "):", ""]
    sec, tok, usd = cost.get("seconds"), cost.get("tokens"), cost.get("usd")
    lines.append("| | Total | Per request | Share of the work it sized |")
    lines.append("|---|---|---|---|")
    if sec is not None:
        lines.append(f"| Time | {sec:.0f} s | {sec / n:.1f} s | {100 * sec / 60 / work_min:.1f}% of {work_min:.0f} agent minutes |")
    if tok:
        share = f"{100 * tok / work_tok:.2f}% of {work_tok:,} tokens" if work_tok else "n/a"
        lines.append(f"| Tokens | {tok:,} | {tok // n:,} | {share} (counting cached context the agent re-read) |")
    if usd is not None:
        lines.append(f"| Dollars (API prices) | {usd:.4f} | {usd / n:.4f} | - |")
    lines += ["", f"One batched call for all {n} requests. In live use one estimate is one guess and one",
              "script call inside the conversation, so it costs about the per-request figure plus the",
              "skill text (SKILL.md, about 2,000 tokens) the first time it is loaded.", "",
              "**Read this before quoting it:**",
              "- Blind replay: the estimator saw only the request text and the message before it, not",
              "  the repository or the conversation, so live guesses can differ.",
              "- Calibrated = leave-one-out: each request is scaled by the other "
              f"{n - 1} requests' actual/guess ratios,",
              "  which is what reckon does once your history has 8 or more finished items. With this",
              "  few requests the numbers move a lot from one sample to the next.",
              "- Actual = measured from the transcript, from the request until the next message you sent.",
              "- This replay writes nothing to history.jsonl. To calibrate for real, record estimates",
              "  and actuals as SKILL.md describes."]
    return "\n".join(lines) + "\n", {"raw": raw, "cold": cold, "calibrated": cal, "cost": cost,
                                     "work_agent_min": round(work_min, 1), "work_tokens": work_tok}


def save(text, data, rows):
    folder = os.path.join(reckon.store_dir(), "replay")
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    md, js = os.path.join(folder, stamp + ".md"), os.path.join(folder, stamp + ".json")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write(text)
    keep = ("id", "session", "agent_min", "wait_min", "measured_rounds", "work_tokens", "model", "guess",
            "cold", "cal", "cal_used", "cal_n")
    with open(js, "w", encoding="utf-8") as fh:
        json.dump(dict(data, requests=[{k: r[k] for k in keep if k in r} for r in rows]), fh, indent=1)
    return md


# ---------------------------------------------------------------- main

def _count(text):
    n = int(text)
    if not 3 <= n <= MAX_N:
        raise argparse.ArgumentTypeError(f"must be between 3 and {MAX_N}")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(prog="replay", description=__doc__.split("\n\n")[0])
    ap.add_argument("-n", type=_count, default=DEFAULT_N, help=f"how many past requests (3-{MAX_N}, default {DEFAULT_N})")
    ap.add_argument("--project", help="repository or folder whose sessions to use (default: this repo)")
    ap.add_argument("--any-project", action="store_true", help="use sessions of every project")
    ap.add_argument("--agent", default="claude-code", choices=sorted(ADAPTERS),
                    help="whose past sessions to replay (default claude-code)")
    ap.add_argument("--model", help="model for the guesses (default: the claude CLI default)")
    ap.add_argument("--claude-bin", default=os.environ.get("CLAUDE_BIN") or "claude")
    ap.add_argument("--list", action="store_true", help="show the selected requests and stop")
    ap.add_argument("--prompt-only", action="store_true", help="print the estimation prompt and stop")
    ap.add_argument("--guesses", help="JSON lines {id, guess_min} from another agent; skips the model call")
    ap.add_argument("--hide-text", action="store_true", help="show request ids only in the report")
    args = ap.parse_args(argv)

    project = None if args.any_project else os.path.abspath(args.project or reckon.repo_root() or os.getcwd())
    reqs = select(project, args.n, args.any_project, args.agent)
    if len(reqs) < 3:
        sys.exit(f"found {len(reqs)} usable past requests in {project or 'any project'}; need at least 3. " +
                 ("Try --any-project, or another --project." if not args.any_project else "Try another --agent."))
    if args.list:
        for r in reqs:
            print(f"{r['id']}  {datetime.fromtimestamp(r['ts']).isoformat(timespec='minutes')}  "
                  f"{r['agent_min']:>5.1f} min  {_short(r['text'], 70)}")
        return
    if args.prompt_only:
        print(system_prompt() + "\n\n---\n\n" + build_prompt(reqs))
        return
    if args.guesses:
        with open(args.guesses, encoding="utf-8") as fh:
            guesses = parse_guesses(fh.read())
        cost = {"calls": 0, "model": "guesses from " + os.path.basename(args.guesses)}
    else:
        if not shutil.which(args.claude_bin) and not os.path.exists(args.claude_bin):
            sys.exit(f"'{args.claude_bin}' not found. Install Claude Code, pass --claude-bin, "
                     "or use --prompt-only with another agent and then --guesses.")
        guesses, cost = ask_claude(reqs, args.model, args.claude_bin)
    rows = score(reqs, guesses)
    if len(rows) < 3:
        sys.exit(f"got usable guesses for {len(rows)} of {len(reqs)} requests; need at least 3")
    if len(rows) < len(reqs):
        reckon.warn(f"no usable guess for {len(reqs) - len(rows)} request(s); they are left out")
    text, data = report(rows, cost, project, args.hide_text)
    path = save(text, data, rows)
    print(text)
    print(f"Saved to {path}")


if __name__ == "__main__":
    main()
