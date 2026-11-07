import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import belay as b  # noqa: E402

T0 = datetime(2026, 1, 2, 3, 0, 0, tzinfo=timezone.utc)


def mins(n):
    return T0 + timedelta(minutes=n)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = b.RunStore(Path(self._tmp.name))
        self.cfg = b.Config(test_command="pytest -q", owner="alice",
                            high_risk_paths=("src/auth/**", "**/migrations/**", ".github/workflows/*"))
        self.rid = self.store.create("7", "github", T0)["run_id"]

    def code(self, fn, *a, **k):
        with self.assertRaises(b.BelayRefusal) as cm:
            fn(*a, **k)
        return cm.exception.code

    def ev(self, command="pytest -q", exit_code=0, head="abc", stage="impl", n=1):
        return b.record_evidence(self.store, self.rid, command=command, exit_code=exit_code,
                                 head=head, stage=stage, now=mins(n))


class EvidenceTests(Base):
    def test_hand_off_requires_passing_evidence_for_head(self):
        self.assertEqual(self.code(b.hand_off, self.store, self.cfg, self.rid, "abc", mins(2)), 7)
        self.ev()
        r = b.hand_off(self.store, self.cfg, self.rid, "abc", mins(3))
        self.assertEqual(r["state"], "review")
        self.assertEqual(self.store.load(self.rid)["state"], "review")
        self.assertEqual(r["history"][-1]["to"], "review")

    def test_hand_off_refused_after_new_commit_without_new_evidence(self):
        self.ev(head="abc")
        self.assertEqual(self.code(b.hand_off, self.store, self.cfg, self.rid, "def", mins(3)), 7)
        self.assertEqual(self.store.load(self.rid)["state"], "working")

    def test_hand_off_refused_when_command_differs_from_configured_test_command(self):
        self.ev(command="echo ok")
        self.assertEqual(self.code(b.hand_off, self.store, self.cfg, self.rid, "abc", mins(3)), 7)

    def test_any_command_accepted_when_no_test_command_configured(self):
        self.ev(command="echo ok")
        cfg = replace(self.cfg, test_command="")
        self.assertEqual(b.hand_off(self.store, cfg, self.rid, "abc", mins(3))["state"], "review")

    def test_failing_evidence_does_not_count(self):
        self.ev(exit_code=1)
        self.assertEqual(self.code(b.hand_off, self.store, self.cfg, self.rid, "abc", mins(3)), 7)

    def test_hand_off_not_found_and_wrong_state(self):
        self.assertEqual(self.code(b.hand_off, self.store, self.cfg, "nope", "abc", mins(3)), 6)
        self.ev()
        b.wait(self.store, self.cfg, self.rid, "ci", mins(2))
        self.assertEqual(self.code(b.hand_off, self.store, self.cfg, self.rid, "abc", mins(3)), 6)

    def test_record_evidence_validates_and_missing_run(self):
        self.assertEqual(self.code(b.record_evidence, self.store, "nope", command="c", exit_code=0,
                                   head="a", stage="s", now=mins(1)), 6)
        self.assertEqual(self.code(b.record_evidence, self.store, self.rid, command="c", exit_code=True,
                                   head="a", stage="s", now=mins(1)), 2)
        self.assertEqual(self.code(b.record_evidence, self.store, self.rid, command="c", exit_code=0,
                                   head="", stage="s", now=mins(1)), 2)

    def test_corrupt_evidence_is_code_2(self):
        run = self.store.load(self.rid)
        run["evidence"] = "oops"
        self.store.save(run)
        self.assertEqual(self.code(b.hand_off, self.store, self.cfg, self.rid, "abc", mins(3)), 2)


class GlobTests(unittest.TestCase):
    def test_glob_matches_windows_separators(self):
        self.assertTrue(b.matches_glob("src\\auth\\login.py", "src/auth/**"))
        self.assertTrue(b.matches_glob("src/auth/login.py", "src\\auth\\**"))

    def test_glob_double_star_spans_nested_dirs(self):
        self.assertTrue(b.matches_glob("src/auth/x.py", "src/auth/**"))
        self.assertTrue(b.matches_glob("src/auth/a/b.py", "src/auth/**"))
        self.assertTrue(b.matches_glob("db/migrations/001.sql", "**/migrations/**"))
        self.assertTrue(b.matches_glob("a/b/migrations/c/d.sql", "**/migrations/**"))
        self.assertTrue(b.matches_glob("migrations/001.sql", "**/migrations/**"))
        self.assertFalse(b.matches_glob("src/authx/x.py", "src/auth/**"))
        self.assertFalse(b.matches_glob("src/other/x.py", "src/auth/**"))

    def test_single_star_does_not_cross_slash(self):
        self.assertTrue(b.matches_glob(".github/workflows/ci.yml", ".github/workflows/*"))
        self.assertFalse(b.matches_glob(".github/workflows/sub/ci.yml", ".github/workflows/*"))
        self.assertTrue(b.matches_glob("a.py", "*.py"))
        self.assertFalse(b.matches_glob("src/a.py", "*.py"))

    def test_leading_dot_slash_and_case(self):
        self.assertTrue(b.matches_glob("./src/auth/x.py", "src/auth/**"))
        self.assertTrue(b.matches_glob("src/auth/x.py", "./src/auth/**"))
        self.assertTrue(b.matches_glob("SRC/Auth/x.py", "src/auth/**"))

    def test_special_chars_are_literal(self):
        self.assertTrue(b.matches_glob("a(b)+c.py", "a(b)+c.py"))
        self.assertFalse(b.matches_glob("aXc.py", "a.c.py"))
        self.assertTrue(b.matches_glob("ab.py", "a?.py"))
        self.assertFalse(b.matches_glob("a/.py", "a?.py"))


class NormaliseTests(Base):
    def test_path_spellings_all_match(self):
        for p in ("src/../src/auth/x.py", "src/./auth/x.py", "src//auth/x.py", "/src/auth/x.py",
                  "C:\\src\\auth\\x.py", "c:/src/auth/x.py", ".\\src\\auth\\x.py"):
            self.assertTrue(b.matches_glob(p, "src/auth/**"), p)
            with self.assertRaises(b.BelayRefusal) as cm:
                b.check_paths(self.cfg, [p], [])
            self.assertEqual(cm.exception.code, 8, p)

    def test_pattern_spellings_normalised(self):
        for pat in ("./src//auth/**", "src/./auth/**", "C:/src/auth/**", "/src/auth/**"):
            self.assertTrue(b.matches_glob("src/auth/x.py", pat), pat)

    def test_escaping_path_refused(self):
        for p in ("../x", "src/../../x", "..\\x"):
            with self.assertRaises(b.BelayRefusal) as cm:
                b.check_paths(self.cfg, [p], [])
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("escapes the repo", cm.exception.reason)

    def test_bare_str_rejected(self):
        self.assertEqual(self.code(b.check_paths, self.cfg, "src/auth/x.py", []), 2)
        # substring-label bypass: a bare string would make `in` a substring test
        self.assertEqual(self.code(b.check_paths, self.cfg, ["src/auth/x.py"], "belay:approved"), 2)
        self.assertEqual(self.code(b.check_paths, self.cfg, ["src/auth/x.py"], ["belay:approved-not"]), 8)


class PathTests(Base):
    def test_high_risk_path_blocked_without_approved_label(self):
        with self.assertRaises(b.BelayRefusal) as cm:
            b.check_paths(self.cfg, ["README.md", ".\\src\\auth\\login.py", "db/migrations/1.sql"],
                          ["belay:working"])
        self.assertEqual(cm.exception.code, 8)
        self.assertIn("src/auth/login.py", cm.exception.reason)
        self.assertIn("db/migrations/1.sql", cm.exception.reason)
        self.assertNotIn("README.md", cm.exception.reason)

    def test_high_risk_path_allowed_with_approved_label(self):
        b.check_paths(self.cfg, ["src/auth/login.py"], ["belay:approved"])

    def test_safe_paths_pass(self):
        b.check_paths(self.cfg, ["README.md", "src/app.py"], [])


class HardeningTests(Base):
    def test_strip_and_validate_inputs(self):
        self.assertEqual(self.code(b.record_evidence, self.store, self.rid, command="c", exit_code=0,
                                   head="a", stage="", now=mins(1)), 2)
        self.assertEqual(self.code(b.record_evidence, self.store, self.rid, command="c", exit_code=0,
                                   head="a", stage=None, now=mins(1)), 2)
        self.assertEqual(self.code(b.record_evidence, self.store, self.rid, command="c", exit_code=0,
                                   head="  ", stage="s", now=mins(1)), 2)
        self.ev(head="  abc  ")
        self.assertEqual(self.store.load(self.rid)["evidence"][0]["head"], "abc")
        self.assertEqual(b.hand_off(self.store, self.cfg, self.rid, " abc ", mins(2))["state"], "review")

    def test_owner_casefold_and_reason(self):
        r = b.override(self.store, self.cfg, self.rid, who=" ALICE ", kind="extend",
                       reason="r", minutes=5, now=mins(1))
        self.assertEqual(r["extension_minutes"], 5)
        for reason in ("", "   ", None):
            self.assertEqual(self.code(b.override, self.store, self.cfg, self.rid, who="alice",
                                       kind="extend", reason=reason, minutes=5, now=mins(2)), 2)
        cfg = replace(self.cfg, owner="  ")
        self.assertEqual(self.code(b.override, self.store, cfg, self.rid, who="  ", kind="extend",
                                   reason="r", minutes=5, now=mins(2)), 9)


class ReopenGuardTests(Base):
    def fail(self):
        run = self.store.load(self.rid)
        b._transition(run, "failed", mins(2), "x")
        self.store.save(run)

    def reopen(self, n=10):
        return b.override(self.store, self.cfg, self.rid, who="alice", kind="reopen",
                          reason="retry", now=mins(n))

    def test_reopen_refused_with_stop_file(self):
        self.fail()
        (self.store.base / "STOP").write_text("x")
        self.assertEqual(self.code(self.reopen), 3)
        self.assertEqual(self.store.load(self.rid)["state"], "failed")

    def test_reopen_refused_with_other_fresh_working_run(self):
        self.fail()
        b.claim(self.store, self.cfg, "8", mins(9))
        self.assertEqual(self.code(self.reopen), 5)
        self.assertEqual(self.store.load(self.rid)["state"], "failed")


class OverrideTests(Base):
    def ov(self, who="alice", kind="extend", minutes=30, n=5, cfg=None):
        return b.override(self.store, cfg or self.cfg, self.rid, who=who, kind=kind,
                          reason="needs more time", minutes=minutes, now=mins(n))

    def test_override_requires_owner(self):
        self.assertEqual(self.code(self.ov, who="mallory"), 9)
        self.assertEqual(self.store.load(self.rid)["overrides"], [])

    def test_override_with_no_owner_configured_refused(self):
        cfg = replace(self.cfg, owner="")
        with self.assertRaises(b.BelayRefusal) as cm:
            self.ov(who="", cfg=cfg)
        self.assertEqual(cm.exception.code, 9)
        self.assertIn("owner", cm.exception.reason)
        self.assertEqual(self.code(self.ov, who="alice", cfg=cfg), 9)

    def test_extend_adds_minutes(self):
        r = self.ov(minutes=30)
        self.assertEqual(r["extension_minutes"], 30)
        r = self.ov(minutes=15)
        self.assertEqual(r["extension_minutes"], 45)
        o = self.store.load(self.rid)["overrides"][0]
        self.assertEqual((o["who"], o["kind"], o["reason"], o["at"]),
                         ("alice", "extend", "needs more time", "2026-01-02T03:05:00Z"))

    def test_extend_needs_positive_minutes_and_kind_valid(self):
        self.assertEqual(self.code(self.ov, minutes=0), 2)
        self.assertEqual(self.code(self.ov, minutes=-5), 2)
        self.assertEqual(self.code(self.ov, kind="bogus"), 2)
        self.assertEqual(self.code(self.ov, who="mallory", kind="bogus"), 9)

    def test_reopen_only_from_failed(self):
        self.assertEqual(self.code(self.ov, kind="reopen", minutes=0), 6)
        run = self.store.load(self.rid)
        b._transition(run, "failed", mins(2), "x")
        self.store.save(run)
        r = self.ov(kind="reopen", minutes=0, n=10)
        self.assertEqual(r["state"], "working")
        self.assertEqual(r["last_tick"], "2026-01-02T03:10:00Z")
        self.assertEqual(r["history"][-1]["to"], "working")
        self.assertEqual(r["active_seconds"], 0)

    def test_override_missing_run(self):
        self.assertEqual(self.code(b.override, self.store, self.cfg, "nope", who="alice",
                                   kind="extend", reason="r", minutes=5, now=mins(1)), 6)


if __name__ == "__main__":
    unittest.main()
