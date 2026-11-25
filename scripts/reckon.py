#!/usr/bin/env python3
"""reckon: optional helper for the reckon skill (Python 3 standard library only).

Commands:
  where                      create (if needed) and print the .reckon store
  profile [--force]          build or refresh .reckon/profile.md and sources.json
  record  --title ... ...    append an estimate to history.jsonl
  actual  ID [--session S]   fill an estimate's actual from a measured session
          ID --agent-min N   ... or from numbers you measured by hand
  sessions [--limit N]       list recent measured agent sessions
  backfill [--limit N]       add measured past sessions to history (actuals only)
  calibrate [--type T] [--rounds R] [--guess G] [--model M]
                             print the numbers to convert rounds or a gut
                             minutes guess to minutes
  backtest FILE              leave-one-out check of estimates against actuals

Every command only reads and writes inside the store, except that `actual`,
`sessions` and `backfill` read agent transcripts on this machine.
"""

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapters import ADAPTERS  # noqa: E402

COLD_MPR = 0.2
COLD_SPREAD = 3.0
SPEED_P90_PAD = 1.5
MIN_TYPE_ITEMS = 5
MIN_ALL_ITEMS = 8
MIN_SPEED_ITEMS = 10
RECENT_WINDOW = 40      # calibrate on the most recent finished items only (speed drifts)
MIN_MODEL_ITEMS = 8     # use only same-model history once this many exist
# Quick mode: the agent's gut guess of its own minutes, rescaled by history.
# Cold prior fitted on the benchmark history only (not the test set): with
# this skill loaded, agents guessed about 8x their measured minutes.
COLD_GUESS_R50 = 0.12
COLD_GUESS_R90 = 0.6


# ---------------------------------------------------------------- store

def _git(*args, cwd=None):
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def repo_root():
    """Root of the main working tree (shared by all worktrees), or None."""
    common = _git("rev-parse", "--path-format=absolute", "--git-common-dir") \
        or _git("rev-parse", "--git-common-dir")
    if common:
        common = os.path.abspath(common)
        if os.path.basename(common) == ".git":
            return os.path.dirname(common)
    return _git("rev-parse", "--show-toplevel")


def work_tree():
    """Root of the checkout being worked on (the current worktree)."""
    return _git("rev-parse", "--show-toplevel") or os.getcwd()


def store_dir(create=True):
    path = os.environ.get("RECKON_HOME") or os.path.join(repo_root() or os.getcwd(), ".reckon")
    if create:
        os.makedirs(os.path.join(path, "estimates"), exist_ok=True)
        ignore = os.path.join(path, ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w", encoding="utf-8") as fh:
                fh.write("# reckon store: everything here is ignored by default.\n*\n"
                         "# To share history through git, uncomment the next two lines:\n"
                         "# !.gitignore\n# !history.jsonl\n")
    return path


def history_path():
    return os.path.join(store_dir(), "history.jsonl")


def load_history():
    path = history_path()
    items = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        items.append(json.loads(line))
                    except ValueError:
                        pass
    return items


def save_history(items):
    path = history_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def append_history(item):
    with open(history_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False) + "\n")


def safe_title(title):
    title = (title or "").strip().splitlines()[0][:80] if title else ""
    if os.environ.get("RECKON_HASH_TITLES") == "1":
        return "sha1:" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]
    return title


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "task"


# ---------------------------------------------------------------- stats

def quantile(values, q):
    """Nearest-rank percentile; with fewer than 10 values q>=0.9 returns max."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if q >= 0.9 and len(vals) < 10:
        return vals[-1]
    if q == 0.5:
        n = len(vals)
        mid = n // 2
        return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2
    return vals[min(len(vals) - 1, max(0, math.ceil(q * len(vals)) - 1))]


def finished(items):
    return [i for i in items if ((i.get("actual") or {}).get("agent_min") or 0) > 0]


def ratio(item):
    """actual agent minutes / rounds-based baseline (rounds x cold speed).

    The baseline uses the fixed cold-start speed so ratios never depend on
    an earlier calibration (no feedback loop)."""
    return item["actual"]["agent_min"] / (item["estimate"]["rounds"] * COLD_MPR)


def calibration(items, task_type=None, model=None):
    """Decide the method and return the factors to apply.

    Uses the most recent RECENT_WINDOW finished items (history is in time
    order), and only items done by the same model once MIN_MODEL_ITEMS exist:
    measured speed differs by model and drifts over time."""
    done = finished(items)
    model_scope = None
    if model:
        same_model = [i for i in done if (i.get("actual") or {}).get("model") == model]
        if len(same_model) >= MIN_MODEL_ITEMS:
            done, model_scope = same_model, model
    done = done[-RECENT_WINDOW:]
    with_est = [i for i in done if ((i.get("estimate") or {}).get("rounds") or 0) > 0]
    same = [i for i in with_est if task_type and i.get("type") == task_type]
    pool, scope = None, None
    if len(same) >= MIN_TYPE_ITEMS:
        pool, scope = same, "type:" + task_type
    elif len(with_est) >= MIN_ALL_ITEMS:
        pool, scope = with_est, "all types"

    speed_items = [i for i in done if (i["actual"].get("rounds") or 0) >= 5]
    mprs = [i["actual"]["agent_min"] / i["actual"]["rounds"] for i in speed_items]
    if len(mprs) >= MIN_SPEED_ITEMS:
        mpr50, mpr90, speed = quantile(mprs, 0.5), quantile(mprs, 0.9), True
    else:
        mpr50, mpr90, speed = COLD_MPR, COLD_MPR * COLD_SPREAD, False

    out = {"mpr50": round(mpr50, 3), "mpr90": round(mpr90, 3), "speed_n": len(mprs), "window": len(done)}
    if model_scope:
        out["model"] = model_scope
    if pool:
        ratios = [ratio(i) for i in pool]
        r50, r90 = quantile(ratios, 0.5), quantile(ratios, 0.9)
        high = scope.startswith("type:") and len(pool) >= 10 and r50 > 0 and r90 / r50 <= 2
        out.update(method="calibrated", scope=scope, n=len(pool), r50=round(r50, 3), r90=round(r90, 3),
                   confidence="HIGH" if high else "MEDIUM")
    elif speed:
        out.update(method="speed", n=len(mprs), confidence="MEDIUM")
    else:
        out.update(method="cold", n=0, confidence="LOW")

    waits = [i["actual"].get("wait_min") for i in done
             if task_type and i.get("type") == task_type and i["actual"].get("wait_min") is not None]
    turns = [i["actual"].get("human_turns") for i in done
             if task_type and i.get("type") == task_type and i["actual"].get("human_turns") is not None]
    if len(waits) >= 3:
        out["wait_floor_min"] = round(quantile(waits, 0.5), 1)
    c_pairs = [i for i in with_est if (i.get("estimate") or {}).get("c_min") is not None
               and i["actual"].get("wait_min") is not None]
    est_c = sum(i["estimate"]["c_min"] for i in c_pairs)
    if sum(1 for i in c_pairs if i["estimate"]["c_min"] > 0) >= MIN_TYPE_ITEMS and est_c > 0:
        # ratio of sums, not a median of ratios: most estimated waits never happen,
        # so a median of per-item ratios collapses to 0
        out["c_ratio"] = round(sum(i["actual"]["wait_min"] for i in c_pairs) / est_c, 2)
    if len(turns) >= 3:
        out["human_turns_p50"] = quantile(turns, 0.5)
    g_items = [i for i in done if ((i.get("estimate") or {}).get("guess_min") or 0) > 0]
    if len(g_items) >= MIN_ALL_ITEMS:
        g = [i["actual"]["agent_min"] / i["estimate"]["guess_min"] for i in g_items]
        out["guess"] = {"method": "calibrated", "n": len(g_items),
                        "g50": round(quantile(g, 0.5), 4), "g90": round(quantile(g, 0.9), 4)}
    else:
        out["guess"] = {"method": "cold", "n": len(g_items), "g50": COLD_GUESS_R50, "g90": COLD_GUESS_R90}
    return out


def raw_minutes(rounds, cal):
    """A_p50 and A_p90 before history ratios (cold start or measured speed)."""
    if cal["method"] == "cold" or cal.get("speed_n", 0) < MIN_SPEED_ITEMS:
        p50 = rounds * COLD_MPR
        return p50, p50 * COLD_SPREAD
    return rounds * cal["mpr50"], rounds * cal["mpr90"] * SPEED_P90_PAD


def apply(rounds, cal):
    if cal["method"] == "calibrated":
        base = rounds * COLD_MPR
        p50, p90 = base * cal["r50"], base * cal["r90"]
    else:
        p50, p90 = raw_minutes(rounds, cal)
    return round(p50, 1), round(max(p90, p50), 1)


def apply_guess(guess, cal):
    g = cal.get("guess") or {"g50": COLD_GUESS_R50, "g90": COLD_GUESS_R90}
    p50, p90 = guess * g["g50"], guess * g["g90"]
    return round(p50, 1), round(max(p90, p50), 1)


def combine(rounds, guess, cal):
    """A_p50/A_p90 and the method used, from rounds, a gut guess, or both.

    Both calibrated: geometric mean of the two. Otherwise the guess wins
    when it is calibrated or when there are no rounds; rounds win when only
    they are calibrated."""
    g_cal = (cal.get("guess") or {}).get("method") == "calibrated"
    r = apply(rounds, cal) if rounds else None
    g = apply_guess(guess, cal) if guess else None
    if r and g and cal["method"] == "calibrated" and g_cal:
        return (round(math.sqrt(r[0] * g[0]), 1), round(math.sqrt(r[1] * g[1]), 1)), "rounds+guess"
    if g and (g_cal or not r or cal["method"] != "calibrated"):
        return g, "guess " + ("calibrated" if g_cal else "cold")
    return r, "rounds " + cal["method"]


# ---------------------------------------------------------------- profile

DOC_CANDIDATES = ["AGENTS.md", "CLAUDE.md", "GEMINI.md", ".github/copilot-instructions.md", "README.md",
                  "README.rst", "ARCHITECTURE.md", "CONTRIBUTING.md", "llms.txt", "docs", "doc", "adr",
                  "docs/adr", "docs/decisions"]
MANIFESTS = ["package.json", "pyproject.toml", "setup.py", "requirements.txt", "Cargo.toml", "go.mod",
             "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "pubspec.yaml",
             "Package.swift", "Makefile", "justfile", "Taskfile.yml", "docker-compose.yml", "compose.yaml",
             "Dockerfile", "*.csproj", "*.sln", "hooks.py", "app.json"]
CI_GLOBS = [".github/workflows", ".gitlab-ci.yml", ".circleci", "azure-pipelines.yml", "Jenkinsfile",
            "bitbucket-pipelines.yml", ".buildkite"]
TOOLS = ["rg", "ctags", "ast-grep", "sg", "codegraph", "graphify", "repomix", "tree-sitter", "serena"]


def _which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        for ext in ("", ".exe", ".cmd", ".bat"):
            p = os.path.join(d, name + ext)
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
    return None


def _exists(root, rel):
    if "*" in rel:
        suffix = rel.replace("*", "")
        try:
            return any(f.endswith(suffix) for f in os.listdir(root))
        except OSError:
            return False
    return os.path.exists(os.path.join(root, rel))


def detect_sources(root):
    docs = [d for d in DOC_CANDIDATES if _exists(root, d)]
    indexes = {}
    if os.path.isdir(os.path.join(root, ".codegraph")):
        indexes["codegraph"] = ".codegraph/ (query with the codegraph CLI or its MCP tools)"
    for rel in ("graphify-out/GRAPH_REPORT.md", "graphify-out/graph.json"):
        if os.path.exists(os.path.join(root, rel)):
            indexes["graphify"] = rel
            break
    if os.path.exists(os.path.join(root, "tags")):
        indexes["ctags"] = "tags"
    if os.path.isdir(os.path.join(root, ".serena")):
        indexes["serena"] = ".serena/"
    tools = {t: bool(_which(t)) for t in TOOLS}
    suggest = []
    if tools.get("codegraph") and "codegraph" not in indexes:
        suggest.append("codegraph is installed but this repo has no index; the owner can build one")
    if tools.get("graphify") and "graphify" not in indexes:
        suggest.append("graphify is installed but this repo has no graphify-out/; the owner can build one")
    return {"docs": docs, "indexes": indexes, "tools": tools, "suggestions": suggest}


def _file_list(root):
    out = _git("ls-files", cwd=root)
    if out is not None:
        return [l for l in out.splitlines() if l]
    files = []
    skip = {".git", "node_modules", ".venv", "venv", "dist", "build", "target", ".reckon", "__pycache__"}
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        for n in names:
            files.append(os.path.relpath(os.path.join(base, n), root))
        if len(files) > 50000:
            break
    return files


def _scripts(root):
    lines = []
    pkg = os.path.join(root, "package.json")
    if os.path.exists(pkg):
        try:
            with open(pkg, encoding="utf-8") as fh:
                scripts = json.load(fh).get("scripts") or {}
            for k in ("build", "test", "lint", "typecheck", "dev", "start"):
                if k in scripts:
                    lines.append(f"npm run {k}: {scripts[k]}"[:140])
        except (OSError, ValueError):
            pass
    mk = os.path.join(root, "Makefile")
    if os.path.exists(mk):
        try:
            with open(mk, encoding="utf-8", errors="replace") as fh:
                targets = re.findall(r"^([A-Za-z0-9_.-]+):", fh.read(), re.M)
            if targets:
                lines.append("make targets: " + ", ".join(sorted(set(targets))[:15]))
        except OSError:
            pass
    return lines


def build_profile(root, previous_notes=""):
    files = _file_list(root)
    exts, dirs = {}, {}
    tests = 0
    files = [f.replace(os.sep, "/") for f in files]
    for f in files:
        ext = os.path.splitext(f)[1].lower() or "(none)"
        exts[ext] = exts.get(ext, 0) + 1
        top = f.split("/")[0] if "/" in f else "."
        dirs[top] = dirs.get(top, 0) + 1
        if re.search(r"(^|/)(tests?|__tests__|spec)(/|$)|[._-](test|spec)\.", f):
            tests += 1
    head = _git("rev-parse", "HEAD", cwd=root) or "no-git"
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=root) or "-"
    hot = _git("log", "--since=90.days", "--name-only", "--pretty=format:", cwd=root) or ""
    churn = {}
    for f in hot.splitlines():
        if f:
            churn[f] = churn.get(f, 0) + 1
    commits_90d = len((_git("log", "--since=90.days", "--oneline", cwd=root) or "").splitlines())
    sources = detect_sources(root)
    manifests = [m for m in MANIFESTS if _exists(root, m)]
    ci = [c for c in CI_GLOBS if _exists(root, c)]

    lines = [
        "# Repo profile",
        "",
        f"<!-- reckon-commit: {head} -->",
        f"Generated {datetime.now().isoformat(timespec='minutes')} on branch `{branch}`, commit `{head[:12]}`.",
        "",
        "## Size",
        f"- {len(files)} tracked files, about {tests} test files, {commits_90d} commits in the last 90 days",
        "- Main file types: " + ", ".join(f"{e} {n}" for e, n in sorted(exts.items(), key=lambda x: -x[1])[:8]),
        "- Top-level folders: " + ", ".join(f"{d} ({n})" for d, n in sorted(dirs.items(), key=lambda x: -x[1])[:12]),
        "",
        "## Build and test",
        "- Manifests: " + (", ".join(manifests) or "none found"),
        "- CI: " + (", ".join(ci) or "none found"),
    ]
    lines += [f"- {s}" for s in _scripts(root)]
    lines += [
        "",
        "## Knowledge sources (use before reading code)",
        "- Docs: " + (", ".join(sources["docs"]) or "none found"),
        "- Code indexes: " + (", ".join(f"{k}: {v}" for k, v in sources["indexes"].items()) or "none"),
        "- CLIs available: " + (", ".join(t for t, ok in sources["tools"].items() if ok) or "none"),
    ]
    lines += [f"- Suggestion: {s}" for s in sources["suggestions"]]
    lines += ["", "## Hot files (most changed in 90 days)"]
    lines += [f"- {f} ({n})" for f, n in sorted(churn.items(), key=lambda x: -x[1])[:10]] or ["- none"]
    lines += ["", "## Notes", previous_notes.strip() or
              "<!-- Agent: add modules, gotchas, fragile areas, slow steps. Kept on refresh. -->", ""]
    return "\n".join(lines), sources, head


def cmd_profile(args):
    root = work_tree()
    store = store_dir()
    path = os.path.join(store, "profile.md")
    notes, old_commit = "", None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r"reckon-commit: (\S+)", text)
        old_commit = m.group(1) if m else None
        if "## Notes" in text:
            notes = text.split("## Notes", 1)[1]
    head = _git("rev-parse", "HEAD", cwd=root) or "no-git"
    if old_commit == head and head != "no-git" and not args.force:
        print(f"profile is current ({head[:12]}): {path}")
        return
    changed = ""
    if old_commit and old_commit != "no-git" and head != "no-git":
        changed = _git("diff", "--stat", f"{old_commit}..{head}", cwd=root) or ""
    text, sources, head = build_profile(root, notes)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(os.path.join(store, "sources.json"), "w", encoding="utf-8") as fh:
        json.dump(sources, fh, indent=2)
    print(f"profile written: {path}")
    if changed:
        print("changed since the last profile (check whether Notes still hold):")
        print("\n".join(changed.splitlines()[-15:]))


# ---------------------------------------------------------------- commands

def cmd_where(_args):
    print(store_dir())


def cmd_record(args):
    items = load_history()
    if not args.rounds and not args.guess_min:
        sys.exit("give --guess-min (quick mode) and/or --rounds (full mode)")
    cal = calibration(items, args.type, args.model)
    method = cal["method"]
    if args.a_p50:
        a_p50 = args.a_p50
        a_p90 = args.a_p90 or round(args.a_p50 * COLD_SPREAD, 1)
    else:
        (a_p50, a_p90), method = combine(args.rounds, args.guess_min, cal)
    confidence = args.confidence or cal["confidence"]
    if not args.confidence and method.startswith("guess"):
        confidence = "MEDIUM" if method.endswith("calibrated") else "LOW"
    item_id = args.id or f"{date.today().isoformat()}-{slug(args.title)}"
    existing = {i.get("id") for i in items}
    base, n = item_id, 2
    while item_id in existing:
        item_id, n = f"{base}-{n}", n + 1
    modules = json.loads(args.modules) if args.modules else []
    item = {
        "id": item_id, "date": date.today().isoformat(), "title": safe_title(args.title),
        "type": args.type, "repo": os.path.basename(repo_root() or os.getcwd()),
        "agent": args.agent, "level": args.level,
        "estimate": {"rounds": args.rounds, "guess_min": args.guess_min, "a_p50": a_p50, "a_p90": a_p90,
                     "b_min": args.b_min, "c_min": args.c_min, "method": method,
                     "confidence": confidence, "modules": modules},
    }
    append_history(item)
    print(json.dumps({"id": item_id, "a_p50": a_p50, "a_p90": a_p90, "method": method,
                      "confidence": item["estimate"]["confidence"]}))


def _adapter(name):
    if name not in ADAPTERS:
        sys.exit(f"unknown agent '{name}'. Known: {', '.join(ADAPTERS)}")
    return ADAPTERS[name]


HISTORY_KEYS = ("session_id", "source", "start", "model", "rounds", "agent_min", "wait_min", "idle_min",
                "human_turns", "human_gap_min", "wall_min")


def _public(m, keys=HISTORY_KEYS):
    return {k: m[k] for k in keys if k in m}


def _in_project(m, root):
    cwd = m.get("cwd")
    return not cwd or os.path.abspath(cwd).startswith(os.path.abspath(root))


def cmd_actual(args):
    items = load_history()
    match = [i for i in items if i.get("id") == args.id]
    if not match:
        sys.exit(f"no estimate with id {args.id}")
    item = match[0]
    if args.agent_min is not None:
        actual = {"agent_min": args.agent_min, "rounds": args.rounds, "wait_min": args.wait_min or 0,
                  "idle_min": 0, "human_turns": args.human_turns, "wall_min": args.wall_min,
                  "source": "manual"}
        if args.model:
            actual["model"] = args.model
    else:
        ad = _adapter(args.agent)
        root = repo_root() or os.getcwd()
        sessions = ad.list_sessions(None if args.any_project else root)
        if args.session and args.session != "latest":
            sessions = [p for p in sessions if os.path.basename(p).startswith(args.session)]
        m = None
        for p in sessions:
            m = ad.measure(p)
            if m and (args.any_project or _in_project(m, root)):
                break
            m = None
        if not m:
            sys.exit("no matching session found; run `sessions` to pick one, or pass --agent-min to record by hand")
        actual = _public(m)
        actual["sessions"] = [actual.pop("session_id")[:8]]
    item["actual"] = actual
    save_history(items)
    est = item.get("estimate") or {}
    print(json.dumps({"id": args.id, "estimate_a_p50": est.get("a_p50"), "estimate_a_p90": est.get("a_p90"),
                      "actual_agent_min": actual["agent_min"], "within_p90":
                      bool(est.get("a_p90")) and actual["agent_min"] <= est["a_p90"]}))


def cmd_sessions(args):
    ad = _adapter(args.agent)
    root = repo_root() or os.getcwd()
    paths = ad.list_sessions(None if args.any_project else root)
    shown = 0
    for p in paths:
        m = ad.measure(p)
        if not m or not (args.any_project or _in_project(m, root)):
            continue
        extra = _public(m, ("cost_usd", "prs"))
        print(json.dumps({**_public(m), **extra, "title": safe_title(m.get("title"))}, ensure_ascii=False))
        shown += 1
        if shown >= args.limit:
            break


def cmd_backfill(args):
    ad = _adapter(args.agent)
    items = load_history()
    known = {s for i in items for s in (i.get("actual") or {}).get("sessions", [])}
    root = repo_root() or os.getcwd()
    paths = ad.list_sessions(None if args.any_project else root)
    added = 0
    for p in paths[: args.limit]:
        m = ad.measure(p)
        if not m or m["rounds"] < 5 or m["session_id"][:8] in known:
            continue
        if not (args.any_project or _in_project(m, root)):
            continue
        actual = _public(m)
        sid = actual.pop("session_id")[:8]
        actual["sessions"] = [sid]
        items.append({"id": f"measured-{sid}", "kind": "measured", "date": m["start"][:10],
                      "title": safe_title(m.get("title")), "type": None, "agent": ad.NAME,
                      "actual": actual})
        added += 1
    save_history(items)
    print(f"added {added} measured sessions to {history_path()}")


def cmd_calibrate(args):
    cal = calibration(load_history(), args.type, args.model)
    if args.rounds or args.guess:
        (cal["a_p50"], cal["a_p90"]), cal["used"] = combine(args.rounds, args.guess, cal)
    print(json.dumps(cal, indent=2))


def cmd_backtest(args):
    with open(args.file, encoding="utf-8") as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
    rows = [r for r in rows if ((r.get("estimate") or {}).get("rounds") or (r.get("estimate") or {}).get("guess_min"))
            and ((r.get("actual") or {}).get("agent_min") or 0) > 0]
    if len(rows) < 3:
        sys.exit("need at least 3 items with an estimate (rounds or guess_min) and actual.agent_min")
    results = []
    for i, r in enumerate(rows):
        others = rows[:i] + rows[i + 1:]
        cal = calibration(others, r.get("type"), (r.get("actual") or {}).get("model"))
        e = r["estimate"]
        rounds, guess = e.get("rounds"), e.get("guess_min")
        x = {"id": r.get("id"), "type": r.get("type"), "actual": r["actual"]["agent_min"],
             "naive": (r.get("naive") or {}).get("minutes")}
        (x["p50"], x["p90"]), x["method"] = combine(rounds, guess, cal)
        if rounds:
            x["rounds_p50"], x["rounds_p90"] = apply(rounds, cal)
            x["cold_p50"], x["cold_p90"] = (round(v, 1) for v in raw_minutes(rounds, {"method": "cold"}))
        if guess:
            x["guess_p50"], x["guess_p90"] = apply_guess(guess, cal)
            x["guess_cold_p50"], x["guess_cold_p90"] = apply_guess(guess, {})
        results.append(x)

    def summary(key50, key90=None):
        sel = [x for x in results if x.get(key50)]
        if not sel:
            return None
        errs = [abs(x["actual"] - x[key50]) / x["actual"] for x in sel]
        out = {"n": len(sel), "median_abs_pct_err": round(100 * quantile(errs, 0.5), 1)}
        if key90:
            out["share_at_or_below_p50"] = round(100 * sum(x["actual"] <= x[key50] for x in sel) / len(sel), 1)
            out["share_at_or_below_p90"] = round(100 * sum(x["actual"] <= x[key90] for x in sel) / len(sel), 1)
        return out

    report = {"n": len(results), "methods": {}}
    for x in results:
        report["methods"][x["method"]] = report["methods"].get(x["method"], 0) + 1
    report["used_leave_one_out"] = summary("p50", "p90")
    for label, k in (("rounds_calibrated", "rounds"), ("rounds_cold", "cold"),
                     ("guess_calibrated", "guess"), ("guess_cold", "guess_cold")):
        sm = summary(k + "_p50", k + "_p90")
        if sm:
            report[label] = sm
    if any(x["naive"] for x in results):
        report["naive_guess"] = summary("naive")
        nv = [x for x in results if x["naive"]]
        report["naive_guess"]["median_overestimate_x"] = round(quantile([x["naive"] / x["actual"] for x in nv], 0.5), 2)
    if args.verbose:
        report["items"] = results
    print(json.dumps(report, indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(prog="reckon", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("where").set_defaults(fn=cmd_where)

    sp = sub.add_parser("profile")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_profile)

    sp = sub.add_parser("record")
    sp.add_argument("--title", required=True)
    sp.add_argument("--type", required=True)
    sp.add_argument("--rounds", type=float, help="full mode: total rounds after risk and integration")
    sp.add_argument("--guess-min", type=float, help="quick mode: your gut minutes for doing it yourself")
    sp.add_argument("--model", help="model id of the agent that will do the work")
    sp.add_argument("--a-p50", type=float)
    sp.add_argument("--a-p90", type=float)
    sp.add_argument("--b-min", type=float, default=0)
    sp.add_argument("--c-min", type=float, default=0)
    sp.add_argument("--confidence")
    sp.add_argument("--modules", help="JSON list of modules")
    sp.add_argument("--level", default="quick", help="quick or full")
    sp.add_argument("--agent", default="claude-code")
    sp.add_argument("--id")
    sp.set_defaults(fn=cmd_record)

    sp = sub.add_parser("actual")
    sp.add_argument("id")
    sp.add_argument("--session", default="latest")
    sp.add_argument("--agent", default="claude-code")
    sp.add_argument("--any-project", action="store_true")
    sp.add_argument("--agent-min", type=float)
    sp.add_argument("--rounds", type=int)
    sp.add_argument("--wait-min", type=float)
    sp.add_argument("--human-turns", type=int)
    sp.add_argument("--wall-min", type=float)
    sp.add_argument("--model", help="model id that did the work (manual actuals)")
    sp.set_defaults(fn=cmd_actual)

    for name, fn, limit in (("sessions", cmd_sessions, 10), ("backfill", cmd_backfill, 200)):
        sp = sub.add_parser(name)
        sp.add_argument("--agent", default="claude-code")
        sp.add_argument("--limit", type=int, default=limit)
        sp.add_argument("--any-project", action="store_true")
        sp.set_defaults(fn=fn)

    sp = sub.add_parser("calibrate")
    sp.add_argument("--type")
    sp.add_argument("--rounds", type=float)
    sp.add_argument("--guess", type=float, help="gut minutes guess to rescale")
    sp.add_argument("--model", help="model id of the agent that will do the work")
    sp.set_defaults(fn=cmd_calibrate)

    sp = sub.add_parser("backtest")
    sp.add_argument("file")
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(fn=cmd_backtest)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
