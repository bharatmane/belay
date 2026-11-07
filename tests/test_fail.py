import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import belay as b  # noqa: E402
from tests.test_cli import CliBase  # noqa: E402

T0 = datetime(2026, 1, 2, 3, 0, 0, tzinfo=timezone.utc)


def mins(n):
    return T0 + timedelta(minutes=n)


class FailTests(CliBase):
    def setUp(self):
        super().setUp()
        self.store = b.RunStore(self.root)
        self.cfg = b.Config()
        self.rid = self.store.create("7", "github", T0)["run_id"]

    def code(self, fn, *a):
        with self.assertRaises(b.BelayRefusal) as cm:
            fn(*a)
        return cm.exception.code

    def test_working_to_failed_credits_time(self):
        r = b.fail(self.store, self.cfg, self.rid, "stuck", mins(10))
        self.assertEqual(r["state"], "failed")
        self.assertEqual(r["active_seconds"], 600)
        self.assertEqual(r["history"][-1], {"from": "working", "to": "failed",
                                            "at": "2026-01-02T03:10:00Z", "note": "stuck"})
        self.assertEqual(self.store.load(self.rid)["state"], "failed")
        self.assertEqual(r["overrides"], [])

    def test_waiting_to_failed_credits_nothing(self):
        b.wait(self.store, self.cfg, self.rid, "ci", mins(5))
        r = b.fail(self.store, self.cfg, self.rid, "gave up", mins(50))
        self.assertEqual(r["state"], "failed")
        self.assertEqual(r["active_seconds"], 300)

    def test_other_states_code_6(self):
        b.fail(self.store, self.cfg, self.rid, "x", mins(1))
        self.assertEqual(self.code(b.fail, self.store, self.cfg, self.rid, "again", mins(2)), 6)
        run = self.store.load(self.rid)
        run["state"] = "review"
        self.store.save(run)
        self.assertEqual(self.code(b.fail, self.store, self.cfg, self.rid, "x", mins(2)), 6)
        self.assertEqual(self.code(b.fail, self.store, self.cfg, "nope", "x", mins(2)), 6)

    def test_empty_reason_code_2(self):
        for reason in ("", "   ", None):
            self.assertEqual(self.code(b.fail, self.store, self.cfg, self.rid, reason, mins(1)), 2)
        self.assertEqual(self.store.load(self.rid)["state"], "working")

    def test_no_stop_or_budget_gate(self):
        (self.store.base / "STOP").write_text("")
        run = self.store.load(self.rid)
        run["active_seconds"] = 10 ** 6
        self.store.save(run)
        r = b.fail(self.store, self.cfg, self.rid, "budget spent", mins(1))
        self.assertEqual(r["state"], "failed")

    def test_cli_fail(self):
        rid = self.claim("9")
        code, data = self.run_cli("fail", rid, "--reason", "cannot proceed")
        self.assertEqual(code, 0, data)
        self.assertEqual(data["state"], "failed")
        code, data = self.run_cli("fail", rid, "--reason", "again")
        self.assertEqual((code, data["code"]), (6, 6))
        code, data = self.run_cli("fail", "no-such-run", "--reason", "x")
        self.assertEqual((code, data["code"]), (6, 6))
        code, data = self.run_cli("fail", rid, "--reason", " ")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
