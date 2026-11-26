"""Contract tests: every adapter measures the same things the same way.

The OpenCode and Codex fixtures are real sessions with every piece of text
replaced by "x" (tests/fixtures/adapters/build.py); timings and record shapes
are real. The Claude Code fixture is the synthetic eval transcript.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
FIX = os.path.join(HERE, "fixtures", "adapters")
sys.path.insert(0, SCRIPTS)

from adapters import ADAPTERS, codex, opencode  # noqa: E402

REQUIRED = {"session_id": str, "source": str, "scope": str, "start": str, "rounds": int, "agent_min": float,
            "wait_min": float, "idle_min": float, "human_turns": int, "wall_min": float, "title": str,
            "prompts": int}

# (adapter, environment, expected whole-session numbers measured from the fixture)
CASES = {
    "claude-code": ({"CLAUDE_CONFIG_DIR": os.path.join(SKILL, "evals", "fixtures", "claude-config")}, None),
    "opencode": ({"RECKON_OPENCODE_DB": os.path.join(FIX, "opencode.db")},
                 {"rounds": 19, "agent_min": 9.3, "prompts": 1, "model": "glm-5.3", "provider": "example-gateway"}),
    "codex": ({"CODEX_HOME": os.path.join(FIX, "codex")},
              {"rounds": 3, "agent_min": 0.2, "prompts": 1, "model": "gpt-5.6-luna", "provider": "example-gateway"}),
}


class Env:
    def __init__(self, values):
        self.values, self.saved = values, {}

    def __enter__(self):
        for k, v in self.values.items():
            self.saved[k] = os.environ.get(k)
            os.environ[k] = v

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Contract(unittest.TestCase):
    def test_every_adapter_is_registered_and_complete(self):
        self.assertEqual(set(ADAPTERS), set(CASES))
        for name, ad in ADAPTERS.items():
            self.assertEqual(ad.NAME, name)
            for fn in ("list_sessions", "measure", "session_name", "requests"):
                self.assertTrue(callable(getattr(ad, fn, None)), f"{name}.{fn}")

    def test_measure_returns_the_same_fields_with_sane_values(self):
        for name, (env, expected) in CASES.items():
            ad = ADAPTERS[name]
            with self.subTest(adapter=name), Env(env):
                paths = ad.list_sessions(None)
                self.assertTrue(paths, "fixture session not found")
                m = ad.measure(paths[0])
                for key, kind in REQUIRED.items():
                    self.assertIsInstance(m.get(key), kind, key)
                self.assertEqual(m["source"], name)
                self.assertEqual(m["scope"], "session")
                self.assertGreaterEqual(m["rounds"], 1)
                self.assertGreater(m["agent_min"], 0)
                self.assertLessEqual(m["agent_min"] + m["wait_min"] + m["idle_min"], m["wall_min"] + 0.2)
                self.assertTrue(m.get("model"))
                if expected:
                    for key, value in expected.items():
                        self.assertEqual(m[key], value, key)
                    self.assertGreater(m["tokens"], 0)

    def test_request_scope_matches_the_session_for_a_single_request(self):
        for name, (env, _expected) in CASES.items():
            ad = ADAPTERS[name]
            with self.subTest(adapter=name), Env(env):
                path = ad.list_sessions(None)[0]
                reqs = ad.requests(path)
                self.assertTrue(reqs)
                first = reqs[0]
                self.assertGreaterEqual(first["rounds"], 1)
                m = ad.measure(path, since=first["ts"] - 1)
                self.assertEqual(m["scope"], "request")
                if len(reqs) == 1:
                    self.assertEqual(m["rounds"], ad.measure(path)["rounds"])

    def test_project_filter_uses_the_session_folder(self):
        for name in ("opencode", "codex"):
            ad = ADAPTERS[name]
            with self.subTest(adapter=name), Env(CASES[name][0]):
                self.assertEqual(len(ad.list_sessions("/tmp/fixture-project")), 1)
                self.assertEqual(ad.list_sessions("/tmp/fixture-project-2"), [])
                self.assertEqual(ad.list_sessions("/somewhere/else"), [])

    def test_short_ids_tell_sessions_apart(self):
        self.assertGreater(opencode.SHORT_ID, len("ses_"))
        self.assertGreater(codex.SHORT_ID, 8)

    def test_opencode_rejects_a_database_it_does_not_know(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        bad = os.path.join(tmp, "opencode.db")
        import sqlite3
        sqlite3.connect(bad).execute("create table other (x)").connection.commit()
        with Env({"RECKON_OPENCODE_DB": bad}):
            with self.assertRaises(RuntimeError):
                opencode.list_sessions(None)


class CommandLine(unittest.TestCase):
    def run_cli(self, env, *args, script="reckon.py"):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        full = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "PYTHONPATH")}
        full.update(env, RECKON_HOME=os.path.join(tmp, "store"), HOME=os.path.join(tmp, "home"),
                    GIT_CEILING_DIRECTORIES=tmp, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args], cwd=tmp, env=full,
                              capture_output=True, text=True, timeout=120)

    def test_sessions_and_actual_work_for_each_agent(self):
        for name in ("opencode", "codex"):
            env = CASES[name][0]
            with self.subTest(adapter=name):
                proc = self.run_cli(env, "sessions", "--agent", name, "--any-project")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                row = json.loads(proc.stdout.splitlines()[0])
                self.assertEqual(row["source"], name)
                self.assertEqual(row["model"], CASES[name][1]["model"])

    def test_actual_selects_the_session_by_its_own_id(self):
        for name in ("opencode", "codex"):
            env = CASES[name][0]
            ad = ADAPTERS[name]
            with self.subTest(adapter=name), Env(env):
                sid = ad.session_name(ad.list_sessions(None)[0])
            # record and actual must share a store: run them in one temp dir
            tmp = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, tmp, True)
            full = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "PYTHONPATH")}
            full.update(env, RECKON_HOME=os.path.join(tmp, "store"), HOME=os.path.join(tmp, "home"),
                        GIT_CEILING_DIRECTORIES=tmp)
            run = lambda *a: subprocess.run([sys.executable, os.path.join(SCRIPTS, "reckon.py"), *map(str, a)],
                                            cwd=tmp, env=full, capture_output=True, text=True, timeout=120)
            self.assertEqual(run("record", "--title", "x", "--type", "docs", "--guess-min", 5, "--id", "t").returncode, 0)
            proc = run("actual", "t", "--agent", name, "--any-project", "--whole-session", "--session", sid[:12])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(os.path.join(tmp, "store", "history.jsonl")) as fh:
                item = json.loads(fh.read().splitlines()[-1])
            self.assertEqual(item["actual"]["source"], name)
            self.assertEqual(item["actual"]["sessions"], [sid[:ad.SHORT_ID]])

    def test_replay_lists_requests_from_another_agent(self):
        env = CASES["opencode"][0]
        proc = self.run_cli(env, "--agent", "opencode", "--any-project", "-n", "3", "--list", script="replay.py")
        # one fixture session gives one request, fewer than the minimum of 3
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("found 1 usable past request", proc.stderr)


if __name__ == "__main__":
    unittest.main()
