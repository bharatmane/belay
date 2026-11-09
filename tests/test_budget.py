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


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = b.RunStore(Path(self._tmp.name))
        self.cfg = b.Config()  # idle 60, budget 90 min, 3 attempts
        self.run = self.store.create("7", "github", T0)
        self.rid = self.run["run_id"]

    def code(self, fn, *a):
        with self.assertRaises(b.BelayRefusal) as cm:
            fn(*a)
        return cm.exception.code

    def test_working_time_is_credited(self):
        r = b.heartbeat(self.store, self.cfg, self.rid, mins(10))
        self.assertEqual(r["active_seconds"], 600)
        self.assertEqual(r["last_tick"], "2026-01-02T03:10:00Z")
        self.assertEqual(r["heartbeat"], "2026-01-02T03:10:00Z")
        r = b.heartbeat(self.store, self.cfg, self.rid, mins(15))
        self.assertEqual(self.store.load(self.rid)["active_seconds"], 900)

    def test_waiting_time_not_credited(self):
        b.heartbeat(self.store, self.cfg, self.rid, mins(10))
        r = b.wait(self.store, self.cfg, self.rid, "ci", mins(20))
        self.assertEqual(r["state"], "waiting")
        self.assertEqual(r["active_seconds"], 1200)
        r = b.resume(self.store, self.cfg, self.rid, mins(50))
        self.assertEqual(r["state"], "working")
        self.assertEqual(r["active_seconds"], 1200)
        r = b.heartbeat(self.store, self.cfg, self.rid, mins(55))
        self.assertEqual(r["active_seconds"], 1500)
        notes = r["history"]
        self.assertEqual([(h["from"], h["to"]) for h in notes],
                         [("working", "waiting"), ("waiting", "working")])
        self.assertEqual(notes[0]["note"], "ci")

    def test_gap_longer_than_idle_limit_is_capped(self):
        r = b.heartbeat(self.store, self.cfg, self.rid, T0 + timedelta(hours=5))
        self.assertEqual(r["active_seconds"], 60 * 60)

    def test_clock_going_backwards_credits_nothing(self):
        b.heartbeat(self.store, self.cfg, self.rid, mins(10))
        r = b.heartbeat(self.store, self.cfg, self.rid, mins(5))
        self.assertEqual(r["active_seconds"], 600)

    def test_budget_spent_raises_code_4(self):
        cfg = replace(self.cfg, active_minutes=30)
        b.heartbeat(self.store, cfg, self.rid, mins(20))
        self.assertEqual(self.code(b.heartbeat, self.store, cfg, self.rid, mins(40)), 4)
        run = self.store.load(self.rid)
        self.assertEqual(run["active_seconds"], 2400)
        bd = b.budget(cfg, run, mins(40))
        self.assertTrue(bd["spent"])
        self.assertEqual(bd["remaining_minutes"], 0)

    def test_budget_includes_extension(self):
        run = dict(self.run, active_seconds=90 * 60, extension_minutes=30)
        bd = b.budget(self.cfg, run, mins(0))
        self.assertEqual(bd["limit_minutes"], 120)
        self.assertEqual(bd["active_minutes"], 90)
        self.assertEqual(bd["remaining_minutes"], 30)
        self.assertFalse(bd["spent"])

    def test_attempt_over_limit_raises_code_4(self):
        for i in range(3):
            r = b.attempt(self.store, self.cfg, self.rid, "build", mins(i))
        self.assertEqual(r["attempts"]["build"], 3)
        self.assertEqual(self.code(b.attempt, self.store, self.cfg, self.rid, "build", mins(5)), 4)
        self.assertEqual(self.store.load(self.rid)["attempts"]["build"], 3)

    def test_attempts_are_per_stage(self):
        for _ in range(3):
            b.attempt(self.store, self.cfg, self.rid, "build", mins(1))
        r = b.attempt(self.store, self.cfg, self.rid, "test", mins(2))
        self.assertEqual(r["attempts"], {"build": 3, "test": 1})

    def test_resume_requires_waiting_state(self):
        self.assertEqual(self.code(b.resume, self.store, self.cfg, self.rid, mins(1)), 6)

    def test_wait_requires_working_state_and_unknown_run(self):
        b.wait(self.store, self.cfg, self.rid, "x", mins(1))
        self.assertEqual(self.code(b.wait, self.store, self.cfg, self.rid, "y", mins(2)), 6)
        self.assertEqual(self.code(b.resume, self.store, self.cfg, "nope", mins(2)), 6)
        self.assertEqual(self.code(b.heartbeat, self.store, self.cfg, "nope", mins(2)), 6)

    def test_heartbeat_on_non_working_run_refused_code_6(self):
        b.wait(self.store, self.cfg, self.rid, "x", mins(1))
        self.assertEqual(self.code(b.heartbeat, self.store, self.cfg, self.rid, mins(30)), 6)
        r = self.store.load(self.rid)
        self.assertEqual((r["active_seconds"], r["state"]), (60, "waiting"))
        self.assertEqual(r["heartbeat"], "2026-01-02T03:01:00Z")  # unchanged
        b.resume(self.store, self.cfg, self.rid, mins(31))
        b.fail(self.store, self.cfg, self.rid, "gave up", mins(32))
        self.assertEqual(self.code(b.heartbeat, self.store, self.cfg, self.rid, mins(33)), 6)

    def test_heartbeat_on_review_run_refused_code_6(self):
        b.record_evidence(self.store, self.rid, command="t", exit_code=0, head="abc", stage="test",
                          now=mins(1))
        b.hand_off(self.store, self.cfg, self.rid, "abc", mins(2))
        self.assertEqual(self.code(b.heartbeat, self.store, self.cfg, self.rid, mins(3)), 6)

    def test_recovered_run_is_not_charged_for_downtime(self):
        b.heartbeat(self.store, self.cfg, self.rid, mins(40))  # 40 active minutes
        later = T0 + timedelta(hours=8)
        run = b.claim(self.store, self.cfg, "7", later)
        self.assertEqual(run["run_id"], self.rid)
        self.assertEqual(run["heartbeat"], "2026-01-02T11:00:00Z")
        self.assertEqual(run["last_tick"], "2026-01-02T11:00:00Z")
        self.assertEqual(run["active_seconds"], 40 * 60)
        self.assertEqual(run["history"][-1]["note"], "recovered")
        self.assertEqual(self.store.load(self.rid), run)  # saved under the lock
        r = b.heartbeat(self.store, self.cfg, self.rid, later + timedelta(minutes=5))
        self.assertEqual(r["active_seconds"], 45 * 60)

    def test_recovered_run_cannot_be_failed_by_another_claim(self):
        later = T0 + timedelta(hours=8)
        b.claim(self.store, self.cfg, "7", later)
        self.assertEqual(self.code(b.claim, self.store, self.cfg, "8", later + timedelta(minutes=1)), 5)
        self.assertEqual(self.store.load(self.rid)["state"], "working")

    def test_resume_blocked_by_other_fresh_working_run(self):
        b.wait(self.store, self.cfg, self.rid, "x", mins(1))
        other = self.store.create("8", "github", mins(5))
        self.assertEqual(self.code(b.resume, self.store, self.cfg, self.rid, mins(10)), 5)
        # stale other run does not block
        self.assertEqual(b.resume(self.store, self.cfg, self.rid, mins(200))["state"], "working")
        self.assertTrue(other["run_id"])

    def test_resume_honors_stop_switch(self):
        b.wait(self.store, self.cfg, self.rid, "x", mins(1))
        (self.store.base).mkdir(parents=True, exist_ok=True)
        (self.store.base / "STOP").write_text("")
        self.assertEqual(self.code(b.resume, self.store, self.cfg, self.rid, mins(2)), 3)

    def test_corrupt_fields_refuse_code_2(self):
        for field, bad in (("last_tick", "garbage"), ("active_seconds", "x")):
            run = self.store.load(self.rid)
            run[field] = bad
            self.store.save(run)
            self.assertEqual(self.code(b.heartbeat, self.store, self.cfg, self.rid, mins(1)), 2)
            run[field] = T0 and ("2026-01-02T03:00:00Z" if field == "last_tick" else 0)
            self.store.save(run)
        run = self.store.load(self.rid)
        run["extension_minutes"] = "x"
        self.assertEqual(self.code(b.budget, self.cfg, run, mins(1)), 2)

    def test_attempt_requires_working_and_stage(self):
        self.assertEqual(self.code(b.attempt, self.store, self.cfg, self.rid, "  ", mins(1)), 2)
        b.wait(self.store, self.cfg, self.rid, "x", mins(1))
        self.assertEqual(self.code(b.attempt, self.store, self.cfg, self.rid, "build", mins(2)), 6)


if __name__ == "__main__":
    unittest.main()
