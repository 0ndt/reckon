"""Tests for scripts/replay.py, on synthetic transcripts and a fake claude CLI."""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import shutil
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REPLAY = os.path.join(SKILL, "scripts", "replay.py")
T0 = 1767225600  # 2026-01-01T00:00:00Z

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, re, sys
prompt = sys.argv[sys.argv.index("-p") + 1]
ids = re.findall(r"^### (r\d+)$", prompt, re.M)
lines = "\n".join(json.dumps({"id": i, "guess_min": 10 * (k % 3 + 1)}) for k, i in enumerate(ids))
print(json.dumps({"is_error": False, "result": lines, "total_cost_usd": 0.0123,
                  "usage": {"input_tokens": 3000, "output_tokens": 200},
                  "modelUsage": {"fake-model": {}}}))
'''


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Replay(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("RECKON_TEST_TMP")
        if base:
            os.makedirs(base, exist_ok=True)
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="reckon-replay-", dir=base))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = os.path.join(self.tmp, "work")
        self.config = os.path.join(self.tmp, "agent-config")
        self.store = os.path.join(self.tmp, "store")
        os.makedirs(self.work)
        self.fake = os.path.join(self.tmp, "fake-claude")
        with open(self.fake, "w") as fh:
            fh.write(FAKE_CLAUDE.replace("/usr/bin/env python3", sys.executable))
        os.chmod(self.fake, os.stat(self.fake).st_mode | stat.S_IEXEC)
        self.env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "PYTHONPATH")}
        self.env.update(RECKON_HOME=self.store, HOME=os.path.join(self.tmp, "home"), CLAUDE_CONFIG_DIR=self.config,
                        GIT_CEILING_DIRECTORIES=self.tmp, PYTHONDONTWRITEBYTECODE="1")

    def session(self, sid, requests):
        """requests: list of (prompt_text, start, minutes of work, rounds)."""
        folder = os.path.join(self.config, "projects", re.sub(r"[^A-Za-z0-9]", "-", self.work))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, sid + ".jsonl")
        n = 0
        with open(path, "w") as fh:
            put = lambda rec: fh.write(json.dumps(dict(rec, cwd=self.work)) + "\n")
            for text, start, minutes, rounds in requests:
                put({"type": "user", "timestamp": iso(start), "message": {"role": "user", "content": text}})
                for k in range(rounds):
                    n += 1
                    at = start + (k + 1) * minutes * 60 / (rounds + 1)
                    put({"type": "assistant", "timestamp": iso(at),
                         "message": {"id": f"m{n}", "model": "model-a", "role": "assistant",
                                     "usage": {"input_tokens": 100, "output_tokens": 50},
                                     "content": [{"type": "tool_use", "id": f"t{n}", "name": "Read", "input": {}}]}})
                    put({"type": "user", "timestamp": iso(at + 1), "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": f"t{n}", "content": "ok"}]}})
                put({"type": "assistant", "timestamp": iso(start + minutes * 60),
                     "message": {"id": f"e{n}", "model": "model-a", "role": "assistant",
                                 "content": [{"type": "text", "text": "done"}]}})
        os.utime(path, (T0, T0))
        return path

    def many(self, count=12):
        reqs = [(f"Please change the behaviour of feature number {k} in the settings page", T0 + k * 3600,
                 1 + k % 5, 4) for k in range(count)]
        for k in range(0, count, 3):        # at most 3 requests are taken per session
            self.session(f"s{k:03d}aaaa", reqs[k:k + 3])

    def run_replay(self, *args, ok=True):
        proc = subprocess.run([sys.executable, REPLAY, "--project", self.work, "--claude-bin", self.fake,
                               *[str(a) for a in args]], cwd=self.work, env=self.env,
                              capture_output=True, text=True, timeout=120)
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        return proc

    def saved(self):
        folder = os.path.join(self.store, "replay")
        name = [f for f in os.listdir(folder) if f.endswith(".json")][0]
        with open(os.path.join(folder, name)) as fh:
            return json.load(fh)

    def test_report_compares_raw_cold_and_calibrated_with_actuals(self):
        self.many(12)
        proc = self.run_replay("-n", 12)
        self.assertIn("reckon replay: 12 past requests", proc.stdout)
        data = self.saved()
        self.assertEqual(len(data["requests"]), 12)
        for r in data["requests"]:
            self.assertEqual(r["cold"], [round(r["guess"] * 0.09, 1), round(r["guess"] * 0.45, 1)])
            self.assertEqual(r["cal_used"], "guess calibrated")
            self.assertEqual(r["cal_n"], 11)
            self.assertGreater(r["agent_min"], 0)
        self.assertIsNotNone(data["calibrated"])
        self.assertEqual(data["cost"]["usd"], 0.0123)
        self.assertEqual(data["cost"]["tokens"], 3200)
        self.assertIn("Share of the work it sized", proc.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.store, "history.jsonl")))

    def test_small_sample_says_calibration_needs_more(self):
        self.many(6)
        proc = self.run_replay("-n", 6)
        self.assertIn("n/a: needs at least 9 requests", proc.stdout)
        self.assertIsNone(self.saved()["calibrated"])

    def test_skips_short_prompts_slash_commands_and_duplicate_sessions(self):
        self.many(6)
        self.session("cccc0003", [("ok", T0 + 90000, 3, 4), ("/compact", T0 + 91000, 3, 4),
                                  ("Please change the behaviour of feature number 5 in the settings page",
                                   T0 + 5 * 3600, 1 + 5 % 5, 4)])
        proc = self.run_replay("--list")
        lines = [line for line in proc.stdout.splitlines() if line.startswith("r")]
        self.assertEqual(len(lines), 6)
        self.assertNotIn(" ok", proc.stdout)

    def test_actual_is_the_request_not_the_session(self):
        self.session("dddd0004", [("First a long unrelated piece of work on the billing module", T0, 30, 10),
                                  ("Then fix the typo in the footer of the landing page please", T0 + 3600, 1, 3),
                                  ("And finally rename the helper used by the export job", T0 + 7200, 2, 3)])
        self.run_replay("-n", 3)
        mins = sorted(r["agent_min"] for r in self.saved()["requests"])
        self.assertEqual(mins, [1.0, 2.0, 30.0])

    def test_guesses_from_another_agent(self):
        self.many(10)
        guesses = os.path.join(self.tmp, "g.jsonl")
        with open(guesses, "w") as fh:
            for k in range(1, 11):
                fh.write(json.dumps({"id": f"r{k:02d}", "guess_min": 15}) + "\n")
        proc = self.run_replay("-n", 10, "--guesses", guesses, "--claude-bin", "/nonexistent")
        self.assertIn("guesses from g.jsonl", proc.stdout)
        self.assertTrue(all(r["guess"] == 15 for r in self.saved()["requests"]))

    def test_prompt_only_contains_skill_text_and_requests(self):
        self.many(4)
        proc = self.run_replay("-n", 4, "--prompt-only")
        self.assertIn("# SKILL.md", proc.stdout)
        self.assertIn("### r04", proc.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.store, "replay")))

    def test_too_few_requests_is_a_clear_error(self):
        self.session("eeee0005", [("Please change the behaviour of the one feature we have", T0, 2, 4)])
        proc = self.run_replay(ok=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("need at least 3", proc.stderr)


if __name__ == "__main__":
    unittest.main()
