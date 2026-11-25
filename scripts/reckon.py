#!/usr/bin/env python3
"""reckon: optional helper for the reckon skill (Python 3.9+, standard library only).

Commands:
  where                      create (if needed) and print the .reckon store
  profile [--force]          build or refresh .reckon/profile.md and sources.json
  record  --title ... ...    append an estimate to history.jsonl
          --batch FILE       ... or one estimate per line of a JSONL file
  actual  ID [--session S] [--turns N]
                             fill an estimate's actual from the request that
                             followed it in a measured session
          ID --whole-session ... or from the whole session
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
import time
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapters import ADAPTERS  # noqa: E402

COLD_MPR = 0.2
COLD_SPREAD = 3.0
SPEED_P90_PAD = 1.5
MIN_TYPE_ITEMS = 5
MIN_ALL_ITEMS = 8
MIN_SPEED_ITEMS = 10
RECENT_WINDOW = 40      # per method: the most recent finished items that method can use
MIN_MODEL_ITEMS = 8     # use only same-model items once the window holds this many
# Quick mode: the agent's gut guess of its own minutes, rescaled by history.
# Cold prior fitted on the benchmark history only (not the test set): with
# this skill loaded, agents guessed about 8x their measured minutes.
COLD_GUESS_R50 = 0.12
COLD_GUESS_R90 = 0.6
COLD_GUESS_SPREAD = COLD_GUESS_R90 / COLD_GUESS_R50

TYPES = ("bugfix", "feature", "refactor", "migration", "ui", "integration", "infra", "docs", "research", "ops")
NARROW_NOTE = ("the P90 from history was not above the P50 (too few or too uniform items), "
               "so the cold-start spread was used and confidence is LOW")


def warn(text):
    print("reckon: " + text, file=sys.stderr)


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


_CREATED = []   # the store path, when this run created it


def store_dir(create=True):
    """RECKON_HOME, else <main repo root>/.reckon, else ~/.reckon outside git."""
    path = os.environ.get("RECKON_HOME")
    if not path:
        root = repo_root()
        path = os.path.join(root if root else os.path.expanduser("~"), ".reckon")
    path = os.path.abspath(path)
    if create:
        if not os.path.isdir(path) and path not in _CREATED:
            _CREATED.append(path)
            warn(f"created the store {path} (it ignores itself through its own .gitignore); "
                 "tell the person once")
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


class HistoryLock:
    """Exclusive lock for writers: a lock file created with O_EXCL.

    Works on every platform and on shared folders. A lock older than
    STALE_S is taken over (its owner crashed)."""
    STALE_S = 30
    WAIT_S = 15

    def __enter__(self):
        self.path = os.path.join(store_dir(), "history.lock")
        deadline = time.time() + self.WAIT_S
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > self.STALE_S:
                        os.remove(self.path)
                        continue
                except OSError:
                    continue
                if time.time() > deadline:
                    sys.exit(f"history is locked by another reckon process ({self.path}); "
                             "try again, or delete that file if nothing is running")
                time.sleep(0.05)

    def __exit__(self, *_exc):
        try:
            os.remove(self.path)
        except OSError:
            pass


def load_entries(quiet=False):
    """Every non-blank line of history.jsonl as {"raw", "item", "line"}.

    A line that is not a JSON object keeps item=None: it is reported on
    stderr, left out of calibration and written back unchanged."""
    path = history_path()
    entries = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for number, raw in enumerate(fh, 1):
                raw = raw.rstrip("\r\n")
                if not raw.strip():
                    continue
                item = None
                try:
                    item = json.loads(raw)
                except ValueError as exc:
                    reason = str(exc)
                else:
                    if not isinstance(item, dict):
                        item, reason = None, "not a JSON object"
                if item is None and not quiet:
                    warn(f"{path} line {number} is not valid JSON ({reason}); "
                         "it is ignored for calibration and kept unchanged. Fix it by hand.")
                entries.append({"raw": raw, "item": item, "line": number})
    return entries


def load_history(quiet=False):
    return [e["item"] for e in load_entries(quiet) if e["item"] is not None]


def _dump(item):
    return json.dumps(item, ensure_ascii=False)


def save_entries(entries):
    """Rewrite history.jsonl. Call with the lock held. Lines whose entry has
    no "changed" flag are written back byte for byte."""
    path = history_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write((_dump(e["item"]) if e.get("changed") else e["raw"]) + "\n")
    os.replace(tmp, path)


def append_items(items):
    """Append lines to history.jsonl. Call with the lock held."""
    path = history_path()
    lead = ""
    if os.path.exists(path) and os.path.getsize(path):
        with open(path, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                lead = "\n"     # a hand edit left no final newline
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(lead + "".join(_dump(i) + "\n" for i in items))


def _epoch(text):
    """Unix time of an ISO date or datetime (naive values are local time)."""
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def item_time(item):
    """When an item happened: the estimate time, else the measured start, else its date."""
    return _epoch(item.get("ts")) or _epoch((item.get("actual") or {}).get("start")) or _epoch(item.get("date"))


def sort_entries(entries):
    """Stable sort into time order. Unparsed or undated lines stay behind
    the line they followed."""
    keyed, last = [], float("-inf")
    for e in entries:
        t = item_time(e["item"]) if e["item"] is not None else None
        last = t if t is not None else last
        keyed.append((last, e))
    keyed.sort(key=lambda pair: pair[0])
    return [e for _t, e in keyed]


def now_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hashing():
    return os.environ.get("RECKON_HASH_TITLES") == "1"


def clean_title(title):
    title = (title or "").strip()
    return title.splitlines()[0].strip()[:80] if title else ""


def title_hash(title):
    return hashlib.sha1(clean_title(title).encode("utf-8")).hexdigest()[:12]


def safe_title(title):
    return "sha1:" + title_hash(title) if hashing() else clean_title(title)


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


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def finished(items):
    return [i for i in items if isinstance(i.get("actual"), dict) and _num(i["actual"].get("agent_min")) > 0]


def _est(item):
    return item.get("estimate") if isinstance(item.get("estimate"), dict) else {}


def ratio(item):
    """actual agent minutes / rounds-based baseline (rounds x cold speed).

    The baseline uses the fixed cold-start speed so ratios never depend on
    an earlier calibration (no feedback loop)."""
    return item["actual"]["agent_min"] / (item["estimate"]["rounds"] * COLD_MPR)


def window(done, usable, model=None):
    """The window rule (references/calibration.md, "The window").

    Of the finished items a method can use, take the RECENT_WINDOW most
    recent (history is in time order). If at least MIN_MODEL_ITEMS of those
    were done by `model`, keep only those. Returns (items, model or None)."""
    recent = [i for i in done if usable(i)][-RECENT_WINDOW:]
    if model:
        same = [i for i in recent if i["actual"].get("model") == model]
        if len(same) >= MIN_MODEL_ITEMS:
            return same, model
    return recent, None


def calibration(items, task_type=None, model=None):
    """Decide the state of each method and return the factors to apply.

    Each method has its own window (see `window`): the guess uses finished
    items that have a guess, the rounds ratio those that have estimated
    rounds, the speed those with measured rounds."""
    done = finished(items)
    out = {}

    # rounds: ratio of actual minutes to estimated rounds x cold speed
    with_est, r_model = window(done, lambda i: _num(_est(i).get("rounds")) > 0, model)
    same = [i for i in with_est if task_type and i.get("type") == task_type]
    pool, scope = None, None
    if len(same) >= MIN_TYPE_ITEMS:
        pool, scope = same, "type:" + task_type
    elif len(with_est) >= MIN_ALL_ITEMS:
        pool, scope = with_est, "all types"

    # speed: measured minutes per measured round
    speed_items, s_model = window(done, lambda i: _num(i["actual"].get("rounds")) >= 5, model)
    mprs = [i["actual"]["agent_min"] / i["actual"]["rounds"] for i in speed_items]
    if len(mprs) >= MIN_SPEED_ITEMS:
        mpr50, mpr90, speed = quantile(mprs, 0.5), quantile(mprs, 0.9), True
    else:
        mpr50, mpr90, speed = COLD_MPR, COLD_MPR * COLD_SPREAD, False
    out.update(mpr50=round(mpr50, 3), mpr90=round(mpr90, 3), speed_n=len(mprs), window=len(with_est))

    if pool:
        ratios = [ratio(i) for i in pool]
        r50, r90 = quantile(ratios, 0.5), quantile(ratios, 0.9)
        high = scope.startswith("type:") and len(pool) >= 10 and r50 > 0 and r90 / r50 <= 2
        out.update(method="calibrated", scope=scope, n=len(pool), r50=round(r50, 3), r90=round(r90, 3),
                   confidence="HIGH" if high else "MEDIUM")
        if r90 <= r50:
            out.update(narrow=True, confidence="LOW")
        if r_model:
            out["model"] = r_model
    elif speed:
        out.update(method="speed", n=len(mprs), confidence="MEDIUM")
        if s_model:
            out["model"] = s_model
    else:
        out.update(method="cold", n=0, confidence="LOW")

    # waits and human turns: the most recent finished items, any method
    recent = done[-RECENT_WINDOW:]
    of_type = [i for i in recent if task_type and i.get("type") == task_type]
    waits = [i["actual"]["wait_min"] for i in of_type if i["actual"].get("wait_min") is not None]
    turns = [i["actual"]["human_turns"] for i in of_type if i["actual"].get("human_turns") is not None]
    if len(waits) >= 3:
        out["wait_floor_min"] = round(quantile(waits, 0.5), 1)
    if len(turns) >= 3:
        out["human_turns_p50"] = quantile(turns, 0.5)
    # c_ratio: every finished item with a wait estimate counts, quick or full
    c_pairs = [i for i in done if _est(i).get("c_min") is not None
               and i["actual"].get("wait_min") is not None][-RECENT_WINDOW:]
    est_c = sum(_num(_est(i)["c_min"]) for i in c_pairs)
    if sum(1 for i in c_pairs if _num(_est(i)["c_min"]) > 0) >= MIN_TYPE_ITEMS and est_c > 0:
        # ratio of sums, not a median of ratios: most estimated waits never happen,
        # so a median of per-item ratios collapses to 0
        out["c_ratio"] = round(sum(_num(i["actual"]["wait_min"]) for i in c_pairs) / est_c, 2)

    # guess: ratio of actual minutes to the gut guess
    g_items, g_model = window(done, lambda i: _num(_est(i).get("guess_min")) > 0, model)
    if len(g_items) >= MIN_ALL_ITEMS:
        g = [i["actual"]["agent_min"] / i["estimate"]["guess_min"] for i in g_items]
        g50, g90 = quantile(g, 0.5), quantile(g, 0.9)
        out["guess"] = {"method": "calibrated", "n": len(g_items), "g50": round(g50, 4), "g90": round(g90, 4)}
        if g90 <= g50:
            out["guess"]["narrow"] = True
        if g_model:
            out["guess"]["model"] = g_model
    else:
        out["guess"] = {"method": "cold", "n": len(g_items), "g50": COLD_GUESS_R50, "g90": COLD_GUESS_R90}
    return out


def cold_minutes(rounds):
    p50 = rounds * COLD_MPR
    return p50, p50 * COLD_SPREAD


def apply(rounds, cal):
    """A_p50/A_p90 from rounds: calibrated ratio, measured speed, or cold start."""
    method = cal.get("method", "cold")
    if method == "calibrated":
        base = rounds * COLD_MPR
        p50 = base * cal["r50"]
        p90 = p50 * COLD_SPREAD if cal.get("narrow") else base * cal["r90"]
    elif method == "speed":
        p50, p90 = rounds * cal["mpr50"], rounds * cal["mpr90"] * SPEED_P90_PAD
    else:
        p50, p90 = cold_minutes(rounds)
    return round(p50, 1), round(max(p90, p50), 1)


def apply_guess(guess, cal):
    g = cal.get("guess") or {"g50": COLD_GUESS_R50, "g90": COLD_GUESS_R90}
    p50 = guess * g["g50"]
    p90 = p50 * COLD_GUESS_SPREAD if g.get("narrow") else guess * g["g90"]
    return round(p50, 1), round(max(p90, p50), 1)


def combine(rounds, guess, cal):
    """A_p50/A_p90 and the method used, from rounds, a gut guess, or both.

    This is the precedence table of references/calibration.md. Rounds count
    as calibrated when they have a history ratio or a measured speed.

      rounds          guess        used
      none            any          guess
      any             none         rounds
      cold            cold         guess (cold)
      cold            calibrated   guess (calibrated)
      speed or ratio  cold         rounds
      speed or ratio  calibrated   geometric mean of the two
    """
    g_cal = (cal.get("guess") or {}).get("method") == "calibrated"
    r_cal = cal.get("method", "cold") != "cold"
    r = apply(rounds, cal) if rounds else None
    g = apply_guess(guess, cal) if guess else None
    if r and g and r_cal and g_cal:
        return (round(math.sqrt(r[0] * g[0]), 1), round(math.sqrt(r[1] * g[1]), 1)), "rounds+guess"
    if g and not (r and r_cal):
        return g, "guess " + ("calibrated" if g_cal else "cold")
    return r, "rounds " + cal.get("method", "cold")


def confidence_of(method, cal):
    """Confidence and an optional note for an estimate made with `method`."""
    uses_rounds, uses_guess = method.startswith("rounds"), "guess" in method
    narrow = (uses_rounds and cal.get("narrow")) or (uses_guess and (cal.get("guess") or {}).get("narrow"))
    if narrow:
        return "LOW", NARROW_NOTE
    if method == "guess cold":
        return "LOW", None
    if method == "guess calibrated":
        return "MEDIUM", None
    return cal.get("confidence", "LOW"), None


# ---------------------------------------------------------------- profile

DOC_CANDIDATES = ["AGENTS.md", "CLAUDE.md", "GEMINI.md", ".github/copilot-instructions.md", "README.md",
                  "README.rst", "ARCHITECTURE.md", "CONTRIBUTING.md", "llms.txt", "docs", "doc", "adr",
                  "docs/adr", "docs/decisions"]
MANIFESTS = ["package.json", "pyproject.toml", "setup.py", "requirements.txt", "Cargo.toml", "go.mod",
             "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "pubspec.yaml",
             "Package.swift", "Makefile", "justfile", "Taskfile.yml", "docker-compose.yml", "compose.yaml",
             "Dockerfile", "*.csproj", "*.sln"]
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


REFRESH_FILES = 30      # refresh the profile only when at least this many files changed ...


def _needs_refresh(root, old_commit, head):
    """... or when a manifest or CI file changed. Returns (bool, reason)."""
    names = _git("diff", "--name-only", f"{old_commit}..{head}", cwd=root)
    if names is None:
        return True, "the stamped commit is not in this checkout"
    changed = [n for n in names.splitlines() if n]
    for name in changed:
        root_file = "/" not in name
        manifest = root_file and any(name.endswith(m[1:]) if m.startswith("*") else name == m for m in MANIFESTS)
        ci = any(name == c or name.startswith(c + "/") for c in CI_GLOBS)
        if manifest or ci:
            return True, f"{name} changed"
    if len(changed) >= REFRESH_FILES:
        return True, f"{len(changed)} files changed"
    return False, f"{len(changed)} files changed since {old_commit[:12]}, under the threshold of {REFRESH_FILES}"


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
    if old_commit and old_commit != "no-git" and head != "no-git" and not args.force:
        if old_commit == head:
            print(f"profile is current ({head[:12]}): {path}")
            return
        stale, reason = _needs_refresh(root, old_commit, head)
        if not stale:
            print(f"profile is current enough ({reason}): {path}")
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


def detect_agent():
    """Name of the agent running this script, or "unknown"."""
    if os.environ.get("CLAUDECODE") == "1":
        return "claude-code"
    return "unknown"


def _positive(spec, key, where, allow_zero=False):
    value = spec.get(key)
    if value is None:
        return None
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) \
        and (value >= 0 if allow_zero else value > 0)
    if not ok:
        raise ValueError(f"{where}{key} must be a number {'>= 0' if allow_zero else 'above 0'}, got {value!r}")
    return int(value) if float(value).is_integer() else value


def build_item(spec, cal, taken, where=""):
    """Validate one estimate request and turn it into a history item.

    Raises ValueError with a message for the person on bad input."""
    title = clean_title(spec.get("title") if isinstance(spec.get("title"), str) else "")
    if not title:
        raise ValueError(f"{where}title is empty")
    task_type = spec.get("type")
    if task_type not in TYPES:
        raise ValueError(f"{where}type must be one of {', '.join(TYPES)}; got {task_type!r}")
    rounds = _positive(spec, "rounds", where)
    guess = _positive(spec, "guess_min", where)
    a_p50, a_p90 = _positive(spec, "a_p50", where), _positive(spec, "a_p90", where)
    b_min = _positive(spec, "b_min", where, allow_zero=True) or 0
    c_min = _positive(spec, "c_min", where, allow_zero=True) or 0
    if not rounds and not guess:
        raise ValueError(f"{where}give guess_min (quick mode) and/or rounds (full mode)")
    if a_p90 and not a_p50:
        raise ValueError(f"{where}a_p90 needs a_p50")
    level = spec.get("level") or ("full" if rounds else "quick")
    if level not in ("quick", "full"):
        raise ValueError(f"{where}level must be quick or full")
    confidence = spec.get("confidence")
    if confidence is not None and confidence not in ("LOW", "MEDIUM", "HIGH"):
        raise ValueError(f"{where}confidence must be LOW, MEDIUM or HIGH")
    modules = spec.get("modules") or []
    if not isinstance(modules, list):
        raise ValueError(f"{where}modules must be a JSON list")

    note = None
    if a_p50:
        # numbers worked out by hand: say so instead of naming a method
        method = "manual"
        a_p90 = a_p90 or round(a_p50 * COLD_SPREAD, 1)
        if a_p90 < a_p50:
            raise ValueError(f"{where}a_p90 is below a_p50")
        confidence = confidence or "LOW"
    else:
        (a_p50, a_p90), method = combine(rounds, guess, cal)
        auto, note = confidence_of(method, cal)
        confidence = confidence or auto

    today = date.today().isoformat()
    item_id = spec.get("id") or f"{today}-{title_hash(title) if hashing() else slug(title)}"
    base, n = item_id, 2
    while item_id in taken:
        item_id, n = f"{base}-{n}", n + 1
    taken.add(item_id)
    estimate = {"rounds": rounds, "guess_min": guess, "a_p50": a_p50, "a_p90": a_p90, "b_min": b_min,
                "c_min": c_min, "method": method, "confidence": confidence, "modules": modules}
    if spec.get("model"):
        estimate["model"] = spec["model"]
    if c_min > 0 and cal.get("c_ratio") is not None:
        # c_min stays as estimated so the ratio never feeds on itself
        estimate["c_ratio"] = cal["c_ratio"]
        estimate["c_corrected"] = round(c_min * cal["c_ratio"], 1)
    item = {"id": item_id, "date": today, "ts": now_ts(), "title": safe_title(title), "type": task_type,
            "repo": os.path.basename(repo_root() or os.getcwd()), "agent": spec.get("agent") or detect_agent(),
            "level": level, "estimate": estimate}
    shown = {"id": item_id, "a_p50": a_p50, "a_p90": a_p90, "method": method, "confidence": confidence}
    if b_min:
        shown["b_min"] = b_min
    if c_min:
        shown["c_min"] = c_min
        if "c_corrected" in estimate:
            shown.update(c_ratio=estimate["c_ratio"], c_corrected=estimate["c_corrected"])
    if note:
        shown["note"] = note
    return item, shown


def _batch_specs(path):
    specs = []
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                spec = json.loads(line)
            except ValueError as exc:
                sys.exit(f"{path} line {number}: not valid JSON ({exc}); nothing was recorded")
            if not isinstance(spec, dict):
                sys.exit(f"{path} line {number}: not a JSON object; nothing was recorded")
            if "guess_min" not in spec and "guess" in spec:
                spec["guess_min"] = spec.pop("guess")
            specs.append((f"{path} line {number}: ", spec))
    if not specs:
        sys.exit(f"{path} holds no estimates")
    return specs


def cmd_record(args):
    if args.batch:
        specs = _batch_specs(args.batch)
    else:
        if args.title is None or args.type is None:
            sys.exit("record needs --title and --type (or --batch FILE)")
        try:
            modules = json.loads(args.modules) if args.modules else []
        except ValueError as exc:
            sys.exit(f"--modules is not valid JSON ({exc})")
        specs = [("", {"title": args.title, "type": args.type, "rounds": args.rounds,
                       "guess_min": args.guess_min, "a_p50": args.a_p50, "a_p90": args.a_p90,
                       "b_min": args.b_min, "c_min": args.c_min, "confidence": args.confidence,
                       "modules": modules, "level": args.level, "id": args.id})]
    with HistoryLock():
        items = load_history()
        taken = {i.get("id") for i in items}
        built, cals = [], {}
        for where, spec in specs:
            for key, default in (("model", args.model), ("agent", args.agent)):
                if spec.get(key) is None:
                    spec[key] = default
            key = (spec.get("type"), spec.get("model"))
            try:
                if key not in cals:
                    cals[key] = calibration(items, spec.get("type") if spec.get("type") in TYPES else None,
                                            spec.get("model"))
                built.append(build_item(spec, cals[key], taken, where))
            except ValueError as exc:
                sys.exit(f"{exc}" + ("; nothing was recorded" if args.batch else ""))
        append_items([item for item, _shown in built])
    for _item, shown in built:
        if _CREATED:
            shown["store_created"] = _CREATED[0]
        print(json.dumps(shown, ensure_ascii=False))


def _adapter(name):
    if name not in ADAPTERS:
        sys.exit(f"unknown agent '{name}'. Known: {', '.join(ADAPTERS)}")
    return ADAPTERS[name]


HISTORY_KEYS = ("session_id", "source", "scope", "start", "model", "rounds", "agent_min", "wait_min", "idle_min",
                "human_turns", "human_gap_min", "wall_min")


def _public(m, keys=HISTORY_KEYS):
    return {k: m[k] for k in keys if k in m}


def _in_project(m, root):
    """True when the session's cwd is the project root or inside it.

    Compares whole path components: /repo/app2 is not inside /repo/app."""
    cwd = m.get("cwd")
    if not cwd:
        return True
    cwd, root = os.path.realpath(cwd), os.path.realpath(root)
    try:
        return os.path.commonpath([cwd, root]) == root
    except ValueError:      # different drives
        return False


def _find(entries, item_id):
    for e in entries:
        if e["item"] is not None and e["item"].get("id") == item_id:
            return e
    return None


def _measure_for(args, item):
    """Measure the session (or the one request in it) that did the work."""
    ad = _adapter(args.agent)
    root = repo_root() or os.getcwd()
    since = None if args.whole_session else _epoch(item.get("ts"))
    if since is None and args.turns:
        sys.exit("--turns needs an estimate with a recorded time (ts) and cannot be used with --whole-session")
    sessions = ad.list_sessions(None if args.any_project else root)
    if args.session and args.session != "latest":
        sessions = [p for p in sessions if os.path.basename(p).startswith(args.session)]
    for p in sessions:
        m = ad.measure(p, since=since, turns=args.turns) if since is not None else ad.measure(p)
        if not m or not (args.any_project or _in_project(m, root)):
            continue
        if since is not None and not m["rounds"]:
            continue    # no agent work after the estimate in this session
        return m, since
    if since is not None:
        sys.exit("no agent work found after the time this estimate was recorded "
                 f"({item['ts']}) in the matching session(s). Run `sessions` to pick another one, "
                 "use --whole-session, or pass --agent-min to record by hand")
    sys.exit("no matching session found; run `sessions` to pick one, or pass --agent-min to record by hand")


def cmd_actual(args):
    found = _find(load_entries(), args.id)
    if not found:
        sys.exit(f"no estimate with id {args.id}")
    notes = []
    if args.agent_min is not None:
        actual = {"agent_min": args.agent_min, "rounds": args.rounds, "wait_min": args.wait_min or 0,
                  "idle_min": 0, "human_turns": args.human_turns, "wall_min": args.wall_min,
                  "source": "manual"}
        if args.model:
            actual["model"] = args.model
    else:
        m, since = _measure_for(args, found["item"])
        actual = _public(m)
        actual["sessions"] = [actual.pop("session_id")[:8]]
        if since is None and m.get("prompts", 1) > 1:
            why = "--whole-session was given" if args.whole_session else \
                "this estimate has no recorded time (ts), so no single request could be selected"
            notes.append(f"measured the WHOLE session, which holds {m['prompts']} human prompts ({why}). "
                         "If the session did other work too, this actual is too high: "
                         "record it by hand with --agent-min instead")
        elif since is not None and m.get("other_prompts"):
            notes.append(f"measured one request ({actual['human_turns']} follow-up turn(s) included) out of "
                         f"a session with {m['prompts']} human prompts; add --turns N if later turns "
                         "belonged to this request")
    for note in notes:
        warn(note)
    with HistoryLock():
        entries = load_entries(quiet=True)
        entry = _find(entries, args.id)
        if not entry:
            sys.exit(f"estimate {args.id} disappeared from history while measuring")
        entry["item"]["actual"] = actual
        entry["changed"] = True
        save_entries(entries)
    est = _est(entry["item"])
    out = {"id": args.id, "estimate_a_p50": est.get("a_p50"), "estimate_a_p90": est.get("a_p90"),
           "actual_agent_min": actual["agent_min"], "within_p90":
           bool(est.get("a_p90")) and actual["agent_min"] <= est["a_p90"],
           "scope": actual.get("scope", "manual")}
    if notes:
        out["note"] = " ".join(notes)
    print(json.dumps(out))


def cmd_sessions(args):
    ad = _adapter(args.agent)
    root = repo_root() or os.getcwd()
    paths = ad.list_sessions(None if args.any_project else root)
    shown = 0
    for p in paths:
        m = ad.measure(p)
        if not m or not (args.any_project or _in_project(m, root)):
            continue
        extra = _public(m, ("prompts", "cost_usd", "prs"))
        print(json.dumps({**_public(m), **extra, "title": safe_title(m.get("title"))}, ensure_ascii=False))
        shown += 1
        if shown >= args.limit:
            break


def cmd_backfill(args):
    ad = _adapter(args.agent)
    root = repo_root() or os.getcwd()
    paths = ad.list_sessions(None if args.any_project else root)
    measured = []
    for p in paths[: args.limit]:
        m = ad.measure(p)
        if m and m["rounds"] >= 5 and (args.any_project or _in_project(m, root)):
            measured.append(m)
    with HistoryLock():
        entries = load_entries()
        known = {s for e in entries if e["item"] is not None
                 for s in (e["item"].get("actual") or {}).get("sessions", [])}
        added = 0
        for m in measured:
            actual = _public(m)
            sid = actual.pop("session_id")[:8]
            if sid in known:
                continue
            known.add(sid)
            actual["sessions"] = [sid]
            item = {"id": f"measured-{sid}", "kind": "measured", "date": m["start"][:10],
                    "title": safe_title(m.get("title")), "type": None, "agent": ad.NAME, "actual": actual}
            entries.append({"raw": _dump(item), "item": item, "line": None})
            added += 1
        # calibration reads the file in time order: keep it so
        save_entries(sort_entries(entries))
    print(f"added {added} measured sessions to {history_path()} (history kept in start-time order)")


def cmd_calibrate(args):
    cal = calibration(load_history(), args.type, args.model)
    if args.rounds or args.guess:
        (cal["a_p50"], cal["a_p90"]), cal["used"] = combine(args.rounds, args.guess, cal)
        cal["used_confidence"], note = confidence_of(cal["used"], cal)
        if note:
            cal["note"] = note
    print(json.dumps(cal, indent=2))


def cmd_backtest(args):
    with open(args.file, encoding="utf-8") as fh:
        rows = []
        for number, line in enumerate(fh, 1):
            if line.strip():
                try:
                    row = json.loads(line)
                except ValueError:
                    warn(f"{args.file} line {number} is not valid JSON; skipped")
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    rows = [r for r in finished(rows) if _num(_est(r).get("rounds")) > 0 or _num(_est(r).get("guess_min")) > 0]
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
            x["cold_p50"], x["cold_p90"] = (round(v, 1) for v in cold_minutes(rounds))
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


def _above_zero(text):
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be above 0, got {text}")
    return value


def _zero_or_more(text):
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {text}")
    return value


def _whole_above_zero(text):
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be above 0, got {text}")
    return value


def _count(text):
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {text}")
    return value


def main(argv=None):
    p = argparse.ArgumentParser(prog="reckon", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("where").set_defaults(fn=cmd_where)

    sp = sub.add_parser("profile")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_profile)

    sp = sub.add_parser("record")
    sp.add_argument("--title")
    sp.add_argument("--type", choices=TYPES)
    sp.add_argument("--batch", metavar="FILE", help="JSONL file, one estimate per line with title, type, "
                    "guess_min and optionally rounds, model, b_min, c_min, id, modules, confidence")
    sp.add_argument("--rounds", type=_above_zero, help="full mode: total rounds after integration")
    sp.add_argument("--guess-min", type=_above_zero, help="your gut minutes for doing it yourself")
    sp.add_argument("--model", help="model id of the agent that will do the work; omit when unknown")
    sp.add_argument("--a-p50", type=_above_zero, help="a range worked out by hand; stored as method manual")
    sp.add_argument("--a-p90", type=_above_zero)
    sp.add_argument("--b-min", type=_zero_or_more, default=0)
    sp.add_argument("--c-min", type=_zero_or_more, default=0)
    sp.add_argument("--confidence", choices=("LOW", "MEDIUM", "HIGH"))
    sp.add_argument("--modules", help="JSON list of modules")
    sp.add_argument("--level", choices=("quick", "full"), help="default: full when --rounds is given")
    sp.add_argument("--agent", help="agent that will do the work (default: detected, else unknown)")
    sp.add_argument("--id")
    sp.set_defaults(fn=cmd_record)

    sp = sub.add_parser("actual")
    sp.add_argument("id")
    sp.add_argument("--session", default="latest", help="session id prefix (default: the newest that fits)")
    sp.add_argument("--turns", type=_count, default=0, help="follow-up turns that belong to the request")
    sp.add_argument("--whole-session", action="store_true",
                    help="measure the whole session instead of the one request after the estimate")
    sp.add_argument("--agent", default="claude-code", help="which transcript adapter to use")
    sp.add_argument("--any-project", action="store_true")
    sp.add_argument("--agent-min", type=_above_zero)
    sp.add_argument("--rounds", type=_whole_above_zero)
    sp.add_argument("--wait-min", type=_zero_or_more)
    sp.add_argument("--human-turns", type=_count)
    sp.add_argument("--wall-min", type=_zero_or_more)
    sp.add_argument("--model", help="model id that did the work (manual actuals)")
    sp.set_defaults(fn=cmd_actual)

    for name, fn, limit in (("sessions", cmd_sessions, 10), ("backfill", cmd_backfill, 200)):
        sp = sub.add_parser(name)
        sp.add_argument("--agent", default="claude-code", help="which transcript adapter to use")
        sp.add_argument("--limit", type=int, default=limit)
        sp.add_argument("--any-project", action="store_true")
        sp.set_defaults(fn=fn)

    sp = sub.add_parser("calibrate")
    sp.add_argument("--type", choices=TYPES)
    sp.add_argument("--rounds", type=_above_zero)
    sp.add_argument("--guess", "--guess-min", dest="guess", type=_above_zero, help="gut minutes guess to rescale")
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
