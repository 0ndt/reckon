"""Tests for scripts/reckon.py (standard library only, Python 3.9+).

Run from the skill folder:

    python3 -m unittest discover -s tests -v

Everything runs against synthetic stores and synthetic transcripts in a
temporary folder; HOME, RECKON_HOME and CLAUDE_CONFIG_DIR all point there, so
no real agent transcript is ever read. Set RECKON_TEST_TMP to choose where
the temporary folders go, and RECKON_SCRIPT to test another copy of the
script.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPT = os.environ.get("RECKON_SCRIPT") or os.path.join(SKILL, "scripts", "reckon.py")
FIXTURES = os.path.join(SKILL, "evals", "fixtures")
T0 = 1_780_000_000      # a fixed Unix time for synthetic data


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_module():
    spec = importlib.util.spec_from_file_location("reckon_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def guess_item(n, guess, actual, model="model-a", task_type="bugfix", **estimate):
    """A finished quick-mode history item, as `record` + `actual` would leave it."""
    est = {"rounds": None, "guess_min": guess, "a_p50": 1, "a_p90": 2, "b_min": 0, "c_min": 0,
           "method": "guess cold", "confidence": "LOW", "modules": []}
    est.update(estimate)
    return {"id": f"g{n}", "date": "2026-01-01", "ts": iso(T0 + n * 600), "title": f"task {n}",
            "type": task_type, "level": "quick", "estimate": est,
            "actual": {"agent_min": actual, "wait_min": 0, "idle_min": 0, "model": model, "source": "manual"}}


def measured_item(n, minutes, rounds, model="model-a", at=None):
    """A measured-only item, as `backfill` writes it."""
    start = datetime.fromtimestamp(at if at is not None else T0 + n * 600).isoformat(timespec="minutes")
    return {"id": f"measured-{n:08d}", "kind": "measured", "date": start[:10], "title": f"session {n}",
            "type": None, "agent": "claude-code",
            "actual": {"agent_min": minutes, "rounds": rounds, "wait_min": 0, "idle_min": 0, "model": model,
                       "start": start, "sessions": [f"{n:08d}"]}}


class Case(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("RECKON_TEST_TMP")
        if base:
            os.makedirs(base, exist_ok=True)
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="reckon-test-", dir=base))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = os.path.join(self.tmp, "store")
        self.work = os.path.join(self.tmp, "work")
        self.config = os.path.join(self.tmp, "agent-config")
        os.makedirs(self.work)
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("RECKON_HASH_TITLES", "CLAUDECODE", "PYTHONPATH")}
        self.env.update(RECKON_HOME=self.store, HOME=os.path.join(self.tmp, "home"), USERPROFILE=self.tmp,
                        CLAUDE_CONFIG_DIR=self.config, GIT_CEILING_DIRECTORIES=self.tmp,
                        PYTHONDONTWRITEBYTECODE="1")

    # -- helpers
    def run_cli(self, *args, ok=True, env=None, cwd=None):
        full = dict(self.env)
        full.update(env or {})
        proc = subprocess.run([sys.executable, SCRIPT, *[str(a) for a in args]], cwd=cwd or self.work,
                              env=full, capture_output=True, text=True, timeout=120)
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        return proc

    def json_cli(self, *args, **kw):
        proc = self.run_cli(*args, **kw)
        return json.loads(proc.stdout), proc

    def history_file(self):
        return os.path.join(self.store, "history.jsonl")

    def write_history(self, lines):
        os.makedirs(self.store, exist_ok=True)
        with open(self.history_file(), "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write((line if isinstance(line, str) else json.dumps(line)) + "\n")

    def read_lines(self):
        with open(self.history_file(), encoding="utf-8") as fh:
            return [line.rstrip("\n") for line in fh if line.strip()]

    def read_items(self):
        out = []
        for line in self.read_lines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def item(self, item_id):
        return [i for i in self.read_items() if i.get("id") == item_id][0]

    def transcript(self, session_id, turns, cwd=None, model="model-a"):
        """Write a synthetic Claude Code transcript.

        turns: list of (prompt_time, [times of tool rounds], end_time)."""
        cwd = cwd or self.work
        folder = os.path.join(self.config, "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, session_id + ".jsonl")
        n = 0
        with open(path, "w", encoding="utf-8") as fh:
            def put(rec):
                rec["cwd"] = cwd
                fh.write(json.dumps(rec) + "\n")
            for prompt_at, rounds, end_at in turns:
                put({"type": "user", "timestamp": iso(prompt_at),
                     "message": {"role": "user", "content": "synthetic prompt"}})
                for at in rounds:
                    n += 1
                    put({"type": "assistant", "timestamp": iso(at),
                         "message": {"id": f"msg{n}", "model": model, "role": "assistant",
                                     "content": [{"type": "tool_use", "id": f"tu{n}", "name": "Read", "input": {}}]}})
                    put({"type": "user", "timestamp": iso(at + 1),
                         "message": {"role": "user",
                                     "content": [{"type": "tool_result", "tool_use_id": f"tu{n}", "content": "ok"}]}})
                put({"type": "assistant", "timestamp": iso(end_at),
                     "message": {"id": f"end{n}", "model": model, "role": "assistant",
                                 "content": [{"type": "text", "text": "done"}]}})
        return path

    def three_request_session(self, session_id="aaaa1111-multi"):
        """30 min of earlier work, then a 1-minute request, then 5 more minutes.

        Returns the time at which the 1-minute request was estimated."""
        first = (T0, [T0 + 60 * k for k in range(1, 30)], T0 + 1800)
        est_at = T0 + 2100 + 5
        second = (T0 + 2100, [T0 + 2100 + 20, T0 + 2100 + 40, T0 + 2100 + 60], T0 + 2100 + 65)
        third = (T0 + 3000, [T0 + 3000 + 60 * k for k in range(1, 5)], T0 + 3300)
        self.transcript(session_id, [first, second, third])
        return est_at

    def pending(self, item_id, ts=None, guess=5):
        item = {"id": item_id, "date": "2026-01-01", "title": "pending", "type": "bugfix", "level": "quick",
                "estimate": {"rounds": None, "guess_min": guess, "a_p50": 0.6, "a_p90": 3.0, "b_min": 0,
                             "c_min": 0, "method": "guess cold", "confidence": "LOW", "modules": []}}
        if ts is not None:
            item["ts"] = iso(ts)
        return item


# ---------------------------------------------------------------- 1. per-request actuals

class PerRequestActual(Case):
    def test_default_measures_only_the_request_after_the_estimate(self):
        est_at = self.three_request_session()
        self.write_history([self.pending("typo", est_at)])
        out, proc = self.json_cli("actual", "typo", "--session", "aaaa1111")
        self.assertAlmostEqual(out["actual_agent_min"], 1.0, places=1)
        self.assertEqual(out["scope"], "request")
        actual = self.item("typo")["actual"]
        self.assertEqual(actual["rounds"], 3)
        self.assertEqual(actual["human_turns"], 0)
        self.assertIn("3 human prompts", proc.stderr)

    def test_record_stores_the_time_of_the_estimate(self):
        before = time.time()
        out, _ = self.json_cli("record", "--title", "Fix typo", "--type", "docs", "--guess-min", 5)
        ts = self.item(out["id"])["ts"]
        stamp = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        self.assertLessEqual(abs(stamp - before), 60)

    def test_record_stores_the_skill_version(self):
        out, _ = self.json_cli("record", "--title", "Fix typo", "--type", "docs", "--guess-min", 5)
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as fh:
            version = re.search(r'version: "([^"]+)"', fh.read()).group(1)
        self.assertEqual(self.item(out["id"])["skill_version"], version)

    def test_record_then_actual_end_to_end(self):
        out, _ = self.json_cli("record", "--title", "Fix typo", "--type", "docs", "--guess-min", 5)
        now = int(time.time())
        self.transcript("bbbb2222-live", [
            (now - 3000, [now - 3000 + 60 * k for k in range(1, 20)], now - 1800),
            (now - 30, [now - 20, now + 30, now + 60, now + 90], now + 120),
        ])
        res, _ = self.json_cli("actual", out["id"])
        self.assertGreater(res["actual_agent_min"], 1.5)
        self.assertLess(res["actual_agent_min"], 2.5)

    def test_turns_adds_follow_up_turns(self):
        est_at = self.three_request_session()
        self.write_history([self.pending("typo", est_at)])
        out, _ = self.json_cli("actual", "typo", "--session", "aaaa1111", "--turns", 1)
        self.assertAlmostEqual(out["actual_agent_min"], 6.0, places=1)
        self.assertEqual(self.item("typo")["actual"]["human_turns"], 1)

    def test_whole_session_is_an_explicit_option(self):
        est_at = self.three_request_session()
        self.write_history([self.pending("typo", est_at)])
        out, proc = self.json_cli("actual", "typo", "--session", "aaaa1111", "--whole-session")
        self.assertAlmostEqual(out["actual_agent_min"], 36.1, places=1)
        self.assertEqual(out["scope"], "session")
        self.assertIn("WHOLE session", proc.stderr)

    def test_estimate_without_a_time_warns_that_the_whole_session_was_measured(self):
        self.three_request_session()
        self.write_history([self.pending("legacy")])
        out, proc = self.json_cli("actual", "legacy", "--session", "aaaa1111")
        self.assertEqual(out["scope"], "session")
        self.assertIn("WHOLE session", proc.stderr)
        self.assertIn("3 human prompts", proc.stderr)
        self.assertIn("note", out)

    def test_go_ahead_turn_is_measured_when_the_estimate_turn_did_no_work(self):
        # prompt "how long?", estimate, reply; then "go" and 3 minutes of work; then another request
        self.transcript("cccc3333-goahead", [
            (T0, [T0 + 5], T0 + 20),                       # the round at T0+5 is the record call itself
            (T0 + 300, [T0 + 300 + 60, T0 + 300 + 120], T0 + 300 + 180),
            (T0 + 900, [T0 + 900 + 60 * k for k in range(1, 10)], T0 + 1500),
        ])
        self.write_history([self.pending("feature", T0 + 6)])
        out, _ = self.json_cli("actual", "feature", "--session", "cccc3333")
        self.assertAlmostEqual(out["actual_agent_min"], 3.0, places=1)
        self.assertEqual(self.item("feature")["actual"]["human_turns"], 1)

    def test_full_mode_order_measures_the_work_not_the_report(self):
        # full-mode.md step 7: calibrate, write the report, then record as the last tool call
        self.transcript("dddd4444-fullmode", [
            (T0, [T0 + 5, T0 + 10, T0 + 15], T0 + 30),     # calibrate, write report, record
            (T0 + 300, [T0 + 300 + 60, T0 + 300 + 120], T0 + 300 + 180),
            (T0 + 900, [T0 + 900 + 60 * k for k in range(1, 10)], T0 + 1500),
        ])
        self.write_history([self.pending("plan", T0 + 16)])
        out, _ = self.json_cli("actual", "plan", "--session", "dddd4444")
        self.assertAlmostEqual(out["actual_agent_min"], 3.0, places=1)

    def test_calibrate_and_record_print_the_same_full_mode_numbers(self):
        args = ["--type", "feature", "--rounds", 81]
        cal, _ = self.json_cli("calibrate", *args, "--guess", 240)
        rec, _ = self.json_cli("record", "--title", "Plan", *args, "--guess-min", 240)
        self.assertEqual((cal["a_p50"], cal["a_p90"]), (rec["a_p50"], rec["a_p90"]))

    def test_no_work_after_the_estimate_is_an_error_not_a_wrong_number(self):
        self.three_request_session()
        self.write_history([self.pending("late", T0 + 99999)])
        proc = self.run_cli("actual", "late", "--session", "aaaa1111", ok=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--whole-session", proc.stderr)
        self.assertNotIn("actual", self.item("late"))

    def test_eval_fixture_gives_the_documented_numbers(self):
        shutil.copytree(os.path.join(FIXTURES, "store"), self.store)
        env = {"CLAUDE_CONFIG_DIR": os.path.join(FIXTURES, "claude-config")}
        cal, _ = self.json_cli("calibrate", "--guess", 20, "--model", "example-model-1")
        self.assertEqual((cal["guess"]["method"], cal["guess"]["n"]), ("calibrated", 12))
        self.assertEqual((cal["a_p50"], cal["a_p90"]), (4.2, 8.0))
        out, _ = self.json_cli("actual", "fixture-fix-footer-typo", "--session", "f1x7ure0", "--any-project", env=env)
        self.assertAlmostEqual(out["actual_agent_min"], 1.0, places=1)
        whole, _ = self.json_cli("actual", "fixture-fix-footer-typo", "--session", "f1x7ure0", "--any-project",
                                 "--whole-session", env=env)
        self.assertAlmostEqual(whole["actual_agent_min"], 36.1, places=1)


# ---------------------------------------------------------------- 2. precedence of rounds and guess

class Precedence(Case):
    SPEED = [measured_item(n, 2.0 + 0.1 * (n % 3), 10) for n in range(12)]      # about 0.2 min per round
    GUESSES = [guess_item(n, 20, 2.0 + 0.2 * (n % 5)) for n in range(10)]       # g about 0.1-0.14

    def used(self, history, *args):
        self.write_history(history)
        out, _ = self.json_cli("calibrate", "--type", "feature", *args)
        return out

    def test_cold_start_the_guess_drives_the_number(self):
        out = self.used([], "--rounds", 48, "--guess", 90)
        self.assertEqual((out["used"], out["a_p50"], out["a_p90"]), ("guess cold", 8.1, 40.5))

    def test_cold_start_rounds_alone(self):
        out = self.used([], "--rounds", 48)
        self.assertEqual((out["used"], out["a_p50"], out["a_p90"]), ("rounds cold", 9.6, 28.8))

    def test_measured_speed_beats_a_cold_guess(self):
        out = self.used(self.SPEED, "--rounds", 40, "--guess", 30)
        self.assertEqual(out["method"], "speed")
        self.assertEqual(out["used"], "rounds speed")
        self.assertNotEqual(out["a_p50"], 2.7)       # 30 x 0.09, the cold guess

    def test_measured_speed_and_calibrated_guess_are_combined(self):
        out = self.used(self.SPEED + self.GUESSES, "--rounds", 40, "--guess", 30)
        self.assertEqual(out["used"], "rounds+guess")

    def test_cold_rounds_and_calibrated_guess_use_the_guess(self):
        out = self.used(self.GUESSES, "--rounds", 40, "--guess", 30)
        self.assertEqual(out["used"], "guess calibrated")

    def test_record_prints_what_calibrate_prints(self):
        self.write_history(self.SPEED)
        cal, _ = self.json_cli("calibrate", "--type", "feature", "--rounds", 40, "--guess", 30)
        rec, _ = self.json_cli("record", "--title", "Both", "--type", "feature", "--rounds", 40, "--guess-min", 30)
        self.assertEqual((rec["a_p50"], rec["a_p90"], rec["method"]), (cal["a_p50"], cal["a_p90"], cal["used"]))

    def test_precedence_table_in_the_docs_matches_the_code(self):
        with open(os.path.join(SKILL, "references", "calibration.md"), encoding="utf-8") as fh:
            text = fh.read()
        rows = re.findall(r"^\| (none|any|cold|speed or ratio) +\| (none|any|cold|calibrated) +\| ([^|]+?) +\|$",
                          text, re.M)
        self.assertEqual(len(rows), 6, "precedence table not found in calibration.md")
        mod = load_module()
        cal = {"cold": {"method": "cold"},
               "speed or ratio": {"method": "speed", "mpr50": 0.2, "mpr90": 0.4}}
        gcal = {"cold": {"method": "cold", "g50": 0.09, "g90": 0.45},
                "calibrated": {"method": "calibrated", "g50": 0.2, "g90": 0.5}}
        for r_state, g_state, used in rows:
            rounds = None if r_state == "none" else 40
            guess = None if g_state == "none" else 30
            for rs in (["cold", "speed or ratio"] if r_state in ("none", "any") else [r_state]):
                for gs in (["cold", "calibrated"] if g_state in ("none", "any") else [g_state]):
                    _range, method = mod.combine(rounds, guess, dict(cal[rs], guess=gcal[gs]))
                    expect = {"guess": "guess", "rounds": "rounds", "geometric mean of the two": "rounds+guess"}[used]
                    self.assertTrue(method == expect or method.startswith(expect + " "),
                                    f"{r_state}/{g_state}: docs say {used}, code used {method}")


class DocsMatchTheScript(Case):
    def json_lines(self, relative):
        with open(os.path.join(SKILL, relative), encoding="utf-8") as fh:
            blocks = re.findall(r"```json\n(.*?)```", fh.read(), re.S)
        lines = [line for block in blocks for line in block.splitlines() if line.startswith("{")]
        return [json.loads(line) for line in lines]

    def test_by_hand_line_in_skill_md_is_what_the_script_computes(self):
        (item,) = self.json_lines("SKILL.md")
        est = item["estimate"]
        cal, _ = self.json_cli("calibrate", "--guess", est["guess_min"])
        self.assertEqual((cal["a_p50"], cal["a_p90"], cal["used"]), (est["a_p50"], est["a_p90"], est["method"]))
        # eight such lines, finished by hand, calibrate the guess
        done = []
        for n in range(8):
            line = json.loads(json.dumps(item))
            line.update(id=f"hand-{n}", actual={"agent_min": 3.5, "model": "m", "source": "manual"})
            done.append(line)
        self.write_history(done)
        cal, _ = self.json_cli("calibrate", "--guess", 20)
        self.assertEqual((cal["guess"]["method"], cal["guess"]["n"], cal["guess"]["g50"]), ("calibrated", 8, 0.175))

    def test_schema_examples_in_calibration_md_are_what_the_script_computes(self):
        quick, full = self.json_lines(os.path.join("references", "calibration.md"))
        for item in (quick, full):
            est = item["estimate"]
            args = ["calibrate", "--type", item["type"], "--guess", est["guess_min"]]
            if est["rounds"]:
                args += ["--rounds", est["rounds"]]
            cal, _ = self.json_cli(*args)
            self.assertEqual((cal["a_p50"], cal["a_p90"], cal["used"]), (est["a_p50"], est["a_p90"], est["method"]))
            self.assertEqual(item["level"], "full" if est["rounds"] else "quick")
        self.assertEqual(sum(m["rounds"] for m in full["estimate"]["modules"]), 70)

    def test_one_script_path_form_everywhere(self):
        bad = []
        for base, _dirs, names in os.walk(SKILL):
            if ".git" in base.split(os.sep):
                continue
            for name in names:
                if name.endswith(".md"):
                    with open(os.path.join(base, name), encoding="utf-8") as fh:
                        for number, line in enumerate(fh, 1):
                            for hit in re.finditer(r"(\S*)scripts/reckon\.py", line):
                                if hit.group(1) not in ("<skill-dir>/", "`<skill-dir>/"):
                                    bad.append(f"{name}:{number}: {line.strip()}")
        self.assertEqual(bad, [])

    def test_full_mode_records_last(self):
        with open(os.path.join(SKILL, "references", "full-mode.md"), encoding="utf-8") as fh:
            step7 = fh.read().split("## 7.")[1]
        self.assertLess(step7.index("reckon.py calibrate"), step7.index(".reckon/estimates/"))
        self.assertLess(step7.index(".reckon/estimates/"), step7.index("reckon.py record"))

    def test_version_is_the_same_everywhere(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as fh:
            version = re.search(r'version: "([^"]+)"', fh.read()).group(1)
        with open(os.path.join(SKILL, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn(f"Version {version}.", readme)
        self.assertIn(f"**{version}**", readme)


# ---------------------------------------------------------------- 3. window per method, time order

class Windows(Case):
    def test_measured_items_do_not_push_guess_items_out_of_the_window(self):
        history = [guess_item(n, 20, 4.0) for n in range(10)]
        history += [measured_item(100 + n, 3.0, 12) for n in range(40)]
        self.write_history(history)
        out, _ = self.json_cli("calibrate", "--guess", 20)
        self.assertEqual((out["guess"]["method"], out["guess"]["n"]), ("calibrated", 10))

    def test_guess_window_is_the_last_40_items_that_have_a_guess(self):
        history = [guess_item(n, 10, 9.0) for n in range(10)]           # old, g = 0.9
        history += [guess_item(100 + n, 10, 2.0) for n in range(40)]    # recent, g = 0.2
        self.write_history(history)
        out, _ = self.json_cli("calibrate", "--guess", 10)
        self.assertEqual((out["guess"]["n"], out["guess"]["g50"], out["guess"]["g90"]), (40, 0.2, 0.2))

    def test_same_model_scope_counts_only_items_the_method_can_use(self):
        history = [guess_item(n, 20, 4.0, model="model-a") for n in range(10)]
        history += [measured_item(100 + n, 3.0, 12, model="model-b") for n in range(8)]
        self.write_history(history)
        out, _ = self.json_cli("calibrate", "--guess", 20, "--model", "model-b")
        self.assertEqual((out["guess"]["method"], out["guess"]["n"]), ("calibrated", 10))
        self.assertNotIn("model", out["guess"])

    def test_same_model_scope_applies_once_8_guess_items_match(self):
        history = [guess_item(n, 10, 5.0, model="model-a") for n in range(10)]
        history += [guess_item(100 + n, 10, 1.0, model="model-b") for n in range(8)]
        self.write_history(history)
        out, _ = self.json_cli("calibrate", "--guess", 10, "--model", "model-b")
        self.assertEqual((out["guess"]["n"], out["guess"]["g50"], out["guess"].get("model")), (8, 0.1, "model-b"))
        mixed, _ = self.json_cli("calibrate", "--guess", 10)
        self.assertEqual(mixed["guess"]["n"], 18)

    def test_backfill_keeps_history_in_start_time_order(self):
        # mtime order (newest first) is what the adapter lists; start times are what must be sorted
        for n, start in enumerate((T0 + 7200, T0 + 3600, T0)):
            path = self.transcript(f"5e55{n}000-order", [(start, [start + 30 * k for k in range(1, 9)], start + 300)])
            os.utime(path, (start, start))
        self.write_history([guess_item(0, 20, 4.0)])      # ts = T0, before two of the sessions
        self.run_cli("backfill")
        starts = [i["actual"].get("start") or i.get("ts") for i in self.read_items()]
        ids = [i["id"] for i in self.read_items()]
        self.assertEqual(len(ids), 4)
        self.assertEqual(ids[-1], "measured-5e550000", f"newest session must be last, got {ids} / {starts}")
        self.assertEqual(ids[-2], "measured-5e551000")

    def test_backfill_then_calibrate_uses_the_newest_sessions(self):
        # 45 old slow sessions (1 min/round), then 45 new fast ones (0.1 min/round)
        for n in range(90):
            start = T0 + n * 4000
            gap = 60 if n < 45 else 6
            path = self.transcript(f"{n:04d}aaaa-s", [(start, [start + gap * k for k in range(1, 11)],
                                                      start + gap * 10 + gap)])
            os.utime(path, (start, start))
        self.run_cli("backfill")
        out, _ = self.json_cli("calibrate")
        self.assertEqual(out["speed_n"], 40)
        self.assertLess(out["mpr50"], 0.2)
        newest = self.read_items()[-45:]
        self.assertTrue(all(i["actual"]["agent_min"] < 2 for i in newest), "newest sessions are not last")


# ---------------------------------------------------------------- 4. hand edits and concurrent writers

class HistorySafety(Case):
    BROKEN = '{"id": "hand-edited", "type": "bugfix", "estimate": {"guess_min": 10,}, }'

    def test_unparsed_line_survives_actual_and_is_reported(self):
        self.write_history([guess_item(1, 20, 4.0), self.BROKEN, self.pending("p", T0)])
        proc = self.run_cli("actual", "p", "--agent-min", 3)
        self.assertEqual(self.read_lines()[1], self.BROKEN)
        self.assertEqual(self.item("p")["actual"]["agent_min"], 3)
        self.assertRegex(proc.stderr, r"line 2\b")

    def test_unparsed_line_survives_backfill_next_to_its_neighbour(self):
        start = T0 - 5000
        self.transcript("0ld00000-s", [(start, [start + 30 * k for k in range(1, 9)], start + 300)])
        self.write_history([guess_item(1, 20, 4.0), self.BROKEN])
        proc = self.run_cli("backfill")
        lines = self.read_lines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[2], self.BROKEN)
        self.assertIn("measured-0ld00000", lines[0])
        self.assertRegex(proc.stderr, r"line 2\b")

    def test_untouched_lines_are_written_back_byte_for_byte(self):
        odd = '{"id":"spaced",   "type":"docs","estimate":{"guess_min":7},"note":"kept as typed"}'
        self.write_history([odd, self.pending("p", T0)])
        self.run_cli("actual", "p", "--agent-min", 3)
        self.assertEqual(self.read_lines()[0], odd)

    def test_record_after_a_file_without_final_newline(self):
        os.makedirs(self.store)
        with open(self.history_file(), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(guess_item(1, 20, 4.0)))        # no newline
        self.run_cli("record", "--title", "Next", "--type", "docs", "--guess-min", 5)
        self.assertEqual(len(self.read_items()), 2)

    def test_writers_wait_for_the_lock(self):
        self.write_history([self.pending("p", T0)])
        lock = os.path.join(self.store, "history.lock")
        with open(lock, "w", encoding="utf-8") as fh:
            fh.write("held by the test")
        proc = subprocess.Popen([sys.executable, SCRIPT, "actual", "p", "--agent-min", "3"], cwd=self.work,
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(1.5)
        self.assertIsNone(proc.poll(), "the writer did not wait for the lock")
        self.assertNotIn("actual", self.item("p"))
        os.remove(lock)
        proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.item("p")["actual"]["agent_min"], 3)

    def test_parallel_writers_lose_nothing(self):
        self.write_history([self.pending(f"p{n}", T0) for n in range(6)])
        cmds = [["record", "--title", f"Parallel {n}", "--type", "docs", "--guess-min", "5"] for n in range(6)]
        cmds += [["actual", f"p{n}", "--agent-min", str(n + 1)] for n in range(6)]
        procs = [subprocess.Popen([sys.executable, SCRIPT, *c], cwd=self.work, env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for c in cmds]
        for p in procs:
            p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0)
        items = self.read_items()
        self.assertEqual(len(items), 12)
        self.assertEqual(len(self.read_lines()), 12)
        self.assertEqual(sorted(i["actual"]["agent_min"] for i in items if "actual" in i), [1, 2, 3, 4, 5, 6])
        self.assertEqual(len({i["id"] for i in items}), 12)
        self.assertFalse(os.path.exists(os.path.join(self.store, "history.lock")))


# ---------------------------------------------------------------- 5. input validation

class Validation(Case):
    def rejected(self, *args):
        proc = self.run_cli(*args, ok=False)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertTrue(proc.stderr.strip())
        return proc

    def test_non_positive_guess_rounds_and_minutes_are_rejected(self):
        self.rejected("record", "--title", "x", "--type", "docs", "--guess-min", -5)
        self.rejected("record", "--title", "x", "--type", "docs", "--guess-min", 0)
        self.rejected("record", "--title", "x", "--type", "docs", "--rounds", -3)
        self.rejected("record", "--title", "x", "--type", "docs", "--guess-min", 5, "--a-p50", -1)
        self.assertFalse(os.path.exists(self.history_file()) and self.read_lines())
        self.write_history([self.pending("p", T0)])
        self.rejected("actual", "p", "--agent-min", 0)
        self.rejected("actual", "p", "--agent-min", -2)
        self.assertNotIn("actual", self.item("p"))

    def test_type_is_validated(self):
        proc = self.rejected("record", "--title", "x", "--type", "bug", "--guess-min", 5)
        self.assertIn("bugfix", proc.stderr)
        self.rejected("calibrate", "--type", "bug")

    def test_whitespace_title_is_a_clean_error(self):
        proc = self.rejected("record", "--title", "   ", "--type", "docs", "--guess-min", 5)
        self.assertIn("title", proc.stderr)

    def test_level_is_inferred_from_the_arguments(self):
        full, _ = self.json_cli("record", "--title", "Full", "--type", "feature", "--rounds", 30, "--guess-min", 40)
        quick, _ = self.json_cli("record", "--title", "Quick", "--type", "feature", "--guess-min", 40)
        self.assertEqual(self.item(full["id"])["level"], "full")
        self.assertEqual(self.item(quick["id"])["level"], "quick")

    def test_agent_defaults_to_unknown_unless_detected_or_given(self):
        a, _ = self.json_cli("record", "--title", "A", "--type", "docs", "--guess-min", 5)
        b, _ = self.json_cli("record", "--title", "B", "--type", "docs", "--guess-min", 5, env={"CLAUDECODE": "1"})
        c, _ = self.json_cli("record", "--title", "C", "--type", "docs", "--guess-min", 5, "--agent", "other-agent")
        self.assertEqual([self.item(x["id"])["agent"] for x in (a, b, c)], ["unknown", "claude-code", "other-agent"])

    def test_hand_entered_range_is_stored_as_manual(self):
        out, _ = self.json_cli("record", "--title", "By hand", "--type", "docs", "--guess-min", 20,
                               "--a-p50", 3, "--a-p90", 7)
        est = self.item(out["id"])["estimate"]
        self.assertEqual((est["method"], est["a_p50"], est["a_p90"]), ("manual", 3, 7))
        self.assertEqual(out["method"], "manual")

    def test_batch_records_every_line_or_nothing(self):
        batch = os.path.join(self.tmp, "backlog.jsonl")
        with open(batch, "w", encoding="utf-8") as fh:
            fh.write('{"title": "Fix login loop", "type": "bugfix", "guess_min": 20}\n')
            fh.write('{"title": "Rate limiting", "type": "feature", "guess_min": 45, "c_min": 10}\n')
            fh.write('{"title": "Update badges", "type": "docs", "guess_min": 5}\n')
        proc = self.run_cli("record", "--batch", batch, "--model", "model-a")
        outs = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual([(o["a_p50"], o["a_p90"]) for o in outs], [(1.8, 9.0), (4.0, 20.2), (0.4, 2.2)])
        self.assertEqual(len(self.read_items()), 3)
        with open(batch, "a", encoding="utf-8") as fh:
            fh.write('{"title": "Bad", "type": "docs", "guess_min": -1}\n')
        bad = self.rejected("record", "--batch", batch)
        self.assertIn("line 4", bad.stderr)
        self.assertEqual(len(self.read_items()), 3)


# ---------------------------------------------------------------- 6. privacy

class Privacy(Case):
    def test_hashed_title_does_not_leak_through_the_id(self):
        out, _ = self.json_cli("record", "--title", "Secret customer acme bug", "--type", "bugfix",
                               "--guess-min", 5, env={"RECKON_HASH_TITLES": "1"})
        with open(self.history_file(), encoding="utf-8") as fh:
            text = fh.read().lower()
        for word in ("secret", "customer", "acme"):
            self.assertNotIn(word, text)
            self.assertNotIn(word, out["id"].lower())
        self.assertTrue(self.item(out["id"])["title"].startswith("sha1:"))

    def test_plain_title_still_gives_a_readable_id(self):
        out, _ = self.json_cli("record", "--title", "Fix login loop", "--type", "bugfix", "--guess-min", 5)
        self.assertTrue(out["id"].endswith("-fix-login-loop"))


# ---------------------------------------------------------------- 7. degenerate ranges

class DegenerateRange(Case):
    def test_identical_ratios_do_not_print_a_zero_width_range(self):
        self.write_history([guess_item(n, 10, 1.0) for n in range(8)])     # every g = 0.1
        out, _ = self.json_cli("record", "--title", "Narrow", "--type", "bugfix", "--guess-min", 20)
        self.assertEqual(out["a_p50"], 2.0)
        self.assertGreater(out["a_p90"], out["a_p50"])
        self.assertEqual(out["confidence"], "LOW")
        self.assertIn("P90", out["note"])
        self.assertEqual(self.item(out["id"])["estimate"]["confidence"], "LOW")

    def test_a_real_spread_is_left_alone(self):
        self.write_history([guess_item(n, 10, 1.0 + n * 0.2) for n in range(8)])
        out, _ = self.json_cli("record", "--title", "Wide", "--type", "bugfix", "--guess-min", 20)
        self.assertEqual((out["a_p50"], out["a_p90"], out["confidence"]), (3.4, 4.8, "MEDIUM"))
        self.assertNotIn("note", out)


# ---------------------------------------------------------------- 8. wait correction

class WaitRatio(Case):
    def history(self):
        items = []
        for n in range(6):          # quick-mode items: 10 minutes of wait estimated, 2 happened
            item = guess_item(n, 20, 4.0, c_min=10)
            item["actual"]["wait_min"] = 2
            items.append(item)
        return items

    def test_c_ratio_counts_quick_mode_items(self):
        self.write_history(self.history())
        out, _ = self.json_cli("calibrate", "--type", "bugfix")
        self.assertEqual(out.get("c_ratio"), 0.2)

    def test_record_prints_and_stores_the_corrected_wait(self):
        self.write_history(self.history())
        out, _ = self.json_cli("record", "--title", "Deploy", "--type", "infra", "--guess-min", 20, "--c-min", 15)
        self.assertEqual((out["c_min"], out["c_ratio"], out["c_corrected"]), (15, 0.2, 3.0))
        est = self.item(out["id"])["estimate"]
        self.assertEqual((est["c_min"], est["c_corrected"]), (15, 3.0))

    def test_no_correction_without_enough_history(self):
        out, _ = self.json_cli("record", "--title", "Deploy", "--type", "infra", "--guess-min", 20, "--c-min", 15)
        self.assertNotIn("c_corrected", out)


# ---------------------------------------------------------------- 9. project matching, manifests, store

class ProjectAndStore(Case):
    def test_sibling_folder_with_the_same_prefix_is_not_this_project(self):
        mod = load_module()
        root = os.path.join(self.tmp, "repo", "app")
        self.assertTrue(mod._in_project({"cwd": root}, root))
        self.assertTrue(mod._in_project({"cwd": os.path.join(root, "src")}, root))
        self.assertFalse(mod._in_project({"cwd": root + "2"}, root))
        self.assertFalse(mod._in_project({"cwd": os.path.join(self.tmp, "repo")}, root))

    def test_sessions_of_a_sibling_folder_are_not_listed(self):
        sibling = self.work + "-2"
        os.makedirs(sibling)
        self.transcript("51b11111-x", [(T0, [T0 + 30 * k for k in range(1, 8)], T0 + 300)], cwd=sibling)
        self.transcript("0wn11111-x", [(T0, [T0 + 30 * k for k in range(1, 8)], T0 + 300)])
        proc = self.run_cli("sessions")
        self.assertIn("0wn11111", proc.stdout)
        self.assertNotIn("51b11111", proc.stdout)

    def test_manifest_list_has_no_stack_specific_names(self):
        mod = load_module()
        self.assertNotIn("hooks.py", mod.MANIFESTS)
        self.assertNotIn("app.json", mod.MANIFESTS)

    def test_store_outside_git_has_a_fixed_location(self):
        env = {"RECKON_HOME": ""}
        a = self.run_cli("where", env=env).stdout.strip()
        other = os.path.join(self.tmp, "elsewhere")
        os.makedirs(other)
        b = self.run_cli("where", env=env, cwd=other).stdout.strip()
        self.assertEqual(a, b)
        self.assertEqual(a, os.path.join(self.env["HOME"], ".reckon"))

    def test_creating_the_store_is_announced_once(self):
        first = self.run_cli("record", "--title", "A", "--type", "docs", "--guess-min", 5)
        second = self.run_cli("record", "--title", "B", "--type", "docs", "--guess-min", 5)
        self.assertIn("store_created", json.loads(first.stdout))
        self.assertIn("created the store", first.stderr)
        self.assertNotIn("store_created", json.loads(second.stdout))
        self.assertEqual(second.stderr.strip(), "")


if __name__ == "__main__":
    unittest.main()
