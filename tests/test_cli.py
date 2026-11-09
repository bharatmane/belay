import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import belay as b  # noqa: E402


class CliBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write_cfg(self, text):
        (self.root / ".belay.toml").write_text(text, encoding="utf-8")

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = b.main(["--root", str(self.root), *args])
        data = json.loads(out.getvalue())  # exactly one JSON object
        return code, data

    def claim(self, issue="7"):
        code, data = self.run_cli("claim", issue)
        self.assertEqual(code, 0, data)
        return data["run_id"]


class TestCli(CliBase):
    def test_claim_prints_json_and_exit_0(self):
        code, data = self.run_cli("claim", "42", "--label", "belay:ready")
        self.assertEqual(code, 0)
        self.assertTrue(data["ok"])
        self.assertTrue(data["run_id"].endswith("-42"))
        self.assertIn("warnings", data)

    def test_refusal_prints_ok_false_code_and_exits_with_code(self):
        self.claim("1")
        code, data = self.run_cli("claim", "2")
        self.assertEqual(code, 5)
        self.assertEqual(data["ok"], False)
        self.assertEqual(data["code"], 5)
        self.assertIn("already working", data["reason"])

    def test_exit_code_table_matches_spec(self):
        # 2 invalid config
        self.write_cfg('tracker = "svn"\n')
        code, data = self.run_cli("status")
        self.assertEqual((code, data["code"]), (2, 2))
        (self.root / ".belay.toml").unlink()
        # 3 stop file
        (self.root / ".belay").mkdir()
        (self.root / ".belay" / "STOP").write_text("")
        self.assertEqual(self.run_cli("claim", "1")[0], 3)
        (self.root / ".belay" / "STOP").unlink()
        # 4 budget spent
        self.write_cfg('[budget]\nattempts_per_stage = 1\n')
        rid = self.claim("1")
        self.assertEqual(self.run_cli("attempt", rid, "build")[0], 0)
        self.assertEqual(self.run_cli("attempt", rid, "build")[0], 4)
        # 5 second claim
        self.assertEqual(self.run_cli("claim", "2")[0], 5)
        # 6 unknown run
        self.assertEqual(self.run_cli("heartbeat", "nope")[0], 6)
        # 7 hand-off without evidence
        self.assertEqual(self.run_cli("hand-off", rid, "--head", "abc")[0], 7)
        # 8 high-risk path
        self.write_cfg('owner = "alice"\nhigh_risk_paths = ["src/auth/**"]\n')
        self.assertEqual(self.run_cli("check-paths", "--changed", "src/auth/x.py")[0], 8)
        # a repeated --changed must accumulate, not keep only the last group
        self.assertEqual(self.run_cli("check-paths", "--changed", "src/auth/x.py",
                                      "--changed", "README.md")[0], 8)
        # 9 non-owner override
        code, data = self.run_cli("override", rid, "--as", "mallory", "--kind", "extend",
                                  "--reason", "r", "--minutes", "5")
        self.assertEqual((code, data["code"]), (9, 9))
        # owner override works and maps --as to who
        code, data = self.run_cli("override", rid, "--as", "alice", "--kind", "extend",
                                  "--reason", "r", "--minutes", "5")
        self.assertEqual(code, 0, data)

    def test_status_lists_runs(self):
        rid = self.claim("9")
        code, data = self.run_cli("status")
        self.assertEqual(code, 0)
        (run,) = data["runs"]
        self.assertEqual(run["run_id"], rid)
        self.assertEqual(run["issue"], "9")
        self.assertEqual(run["state"], "working")
        self.assertFalse(run["stale"])
        for k in ("active_minutes", "remaining_minutes"):
            self.assertIn(k, run)

    def test_status_function_marks_stale(self):
        from datetime import datetime, timedelta, timezone
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        store = b.RunStore(self.root)
        store.create("1", "github", t0)
        cfg = b.Config()
        fresh = b.status(store, cfg, t0 + timedelta(minutes=5))
        stale = b.status(store, cfg, t0 + timedelta(minutes=cfg.idle_minutes + 1))
        self.assertFalse(fresh[0]["stale"])
        self.assertTrue(stale[0]["stale"])

    def test_missing_config_uses_defaults_and_warns(self):
        code, data = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertTrue(any("defaults" in w for w in data["warnings"]))

    def test_usage_error_is_json_exit_2(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = b.main(["--root", str(self.root), "bogus"])
        data = json.loads(out.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual((data["ok"], data["code"]), (False, 2))

    def test_check_paths_stdin_dash(self):
        self.write_cfg('high_risk_paths = ["src/auth/**"]\n')
        old = sys.stdin
        sys.stdin = io.StringIO("README.md\nsrc/auth/x.py\n")
        try:
            code, data = self.run_cli("check-paths", "--changed", "-")
        finally:
            sys.stdin = old
        self.assertEqual(code, 8)
        self.assertIn("src/auth/x.py", data["reason"])

    def test_check_paths_absolute_paths(self):
        self.write_cfg('high_risk_paths = ["migrations/**", "auth/"]\n')
        root = self.root.resolve()
        for p in (str(root / "migrations" / "1.sql"), str(root / "auth" / "login.py")):
            code, data = self.run_cli("check-paths", "--changed", p)
            self.assertEqual(code, 8, (p, data))
        code, data = self.run_cli("check-paths", "--changed", str(root / "README.md"))
        self.assertEqual(code, 0, data)
        code, data = self.run_cli("check-paths", "--changed", str(root.parent / "x" / "auth" / "a.py"))
        self.assertEqual(code, 2, data)

    def test_lifecycle_commands(self):
        rid = self.claim("3")
        self.assertEqual(self.run_cli("heartbeat", rid)[0], 0)
        code, data = self.run_cli("budget", rid)
        self.assertEqual(code, 0)
        self.assertIn("remaining_minutes", data)
        self.assertEqual(self.run_cli("wait", rid, "--reason", "ci")[0], 0)
        self.assertEqual(self.run_cli("resume", rid)[0], 0)
        self.assertEqual(self.run_cli("evidence", rid, "--cmd", "pytest", "--exit-code", "0",
                                      "--head", "abc", "--stage", "test")[0], 0)
        code, data = self.run_cli("hand-off", rid, "--head", "abc")
        self.assertEqual(code, 0, data)
        self.assertEqual(data["state"], "review")
        self.assertEqual(self.run_cli("check-stop")[0], 0)


class TestRobustness(CliBase):
    def test_broken_pipe_does_not_escape_and_keeps_exit_code(self):
        class Dead:
            def write(self, _):
                raise BrokenPipeError()

            def flush(self):
                raise BrokenPipeError()

        old = sys.stdout
        try:
            sys.stdout = Dead()
            code = b.main(["--root", str(self.root), "heartbeat", "nope"])
            sys.stdout.close()
            sys.stdout = Dead()
            code_ok = b.main(["--root", str(self.root), "status"])
        finally:
            if hasattr(sys.stdout, "close"):
                sys.stdout.close()
            sys.stdout = old
        self.assertEqual(code, 6)
        self.assertEqual(code_ok, 0)

    def test_oserror_on_write_is_swallowed(self):
        class Full:
            def write(self, _):
                raise OSError("disk full")

            def flush(self):
                pass

        old = sys.stdout
        sys.stdout = Full()
        try:
            code = b.main(["--root", str(self.root), "status"])
        finally:
            if hasattr(sys.stdout, "close"):
                sys.stdout.close()
            sys.stdout = old
        self.assertEqual(code, 0)

    def test_empty_stdin_warns(self):
        old = sys.stdin
        sys.stdin = io.StringIO("")
        try:
            code, data = self.run_cli("check-paths", "--changed", "-")
        finally:
            sys.stdin = old
        self.assertEqual(code, 0)
        self.assertTrue(any("checked nothing" in w for w in data["warnings"]))

    def test_nonempty_check_paths_has_no_empty_warning(self):
        code, data = self.run_cli("check-paths", "--changed", "a.py")
        self.assertFalse(any("checked nothing" in w for w in data["warnings"]))

    def test_read_paths_preserves_spaces_and_skips_blank(self):
        self.assertEqual(b._read_paths(" a b.py \r\n\n  \nc.py\n"), [" a b.py ", "c.py"])


class TestScript(unittest.TestCase):
    def test_dead_stdout_pipe_exits_cleanly(self):
        # Reader end closed before the child starts: deterministic, no race.
        r, w = os.pipe()
        os.close(r)
        try:
            with tempfile.TemporaryDirectory() as d:
                (Path(d) / ".belay").mkdir()
                (Path(d) / ".belay" / "STOP").write_text("")
                for args, want in ((["status"], 0), (["check-stop"], 3)):
                    p = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "belay.py"),
                                        "--root", d, *args], stdout=w, stderr=subprocess.PIPE,
                                       text=True)
                    self.assertEqual(p.returncode, want, p.stderr)
                    self.assertEqual(p.stderr, "")
        finally:
            os.close(w)

    def test_runs_as_script_via_subprocess(self):
        with tempfile.TemporaryDirectory() as d:
            p = subprocess.run([sys.executable, str(ROOT / "scripts" / "belay.py"), "status"], capture_output=True, text=True, cwd=d)
            self.assertEqual(p.returncode, 0, p.stderr)
            data = json.loads(p.stdout)
            self.assertTrue(data["ok"])
            self.assertEqual(data["runs"], [])


if __name__ == "__main__":
    unittest.main()
