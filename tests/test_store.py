import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import belay as b  # noqa: E402

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = b.RunStore(self.root)
        self.cfg = b.Config()

    def test_claim_creates_record(self):
        run = b.claim(self.store, self.cfg, "42", NOW)
        self.assertEqual(run["issue"], "42")
        self.assertEqual(run["state"], "working")
        self.assertEqual(run["run_id"], b.make_run_id("42", NOW))
        for k in ("tracker", "stage", "started_at", "heartbeat", "last_tick", "active_seconds",
                  "attempts", "evidence", "history", "overrides", "extension_minutes"):
            self.assertIn(k, run)
        self.assertEqual(run["started_at"], "2026-01-02T03:04:05Z")
        self.assertEqual(self.store.load(run["run_id"]), run)
        self.assertTrue((self.root / ".belay" / "runs" / f"{run['run_id']}.json").is_file())

    def test_second_claim_is_refused_code_5(self):
        b.claim(self.store, self.cfg, "1", NOW)
        with self.assertRaises(b.BelayRefusal) as cm:
            b.claim(self.store, self.cfg, "2", NOW + timedelta(minutes=1))
        self.assertEqual(cm.exception.code, 5)

    def test_claim_refused_when_stop_file_present(self):
        (self.root / ".belay").mkdir()
        (self.root / ".belay" / "STOP").write_text("", encoding="utf-8")
        with self.assertRaises(b.BelayRefusal) as cm:
            b.claim(self.store, self.cfg, "1", NOW)
        self.assertEqual(cm.exception.code, 3)
        self.assertEqual(self.store.list_runs(), [])

    def test_claim_refused_when_stop_label_present(self):
        with self.assertRaises(b.BelayRefusal) as cm:
            b.claim(self.store, self.cfg, "1", NOW, labels=["bug", self.cfg.labels["stop"]])
        self.assertEqual(cm.exception.code, 3)
        b.check_stop(self.root, self.cfg, ["bug"])  # no raise

    def test_waiting_run_does_not_block_new_claim(self):
        run = b.claim(self.store, self.cfg, "1", NOW)
        run["state"] = "waiting"
        self.store.save(run)
        second = b.claim(self.store, self.cfg, "2", NOW + timedelta(minutes=1))
        self.assertEqual(second["issue"], "2")
        self.assertEqual(self.store.load(run["run_id"])["state"], "waiting")

    def test_stale_run_same_issue_is_resumed(self):
        run = b.claim(self.store, self.cfg, "1", NOW)
        later = NOW + timedelta(minutes=self.cfg.idle_minutes + 1)
        again = b.claim(self.store, self.cfg, "1", later)
        self.assertEqual(again, run)
        self.assertEqual(len(self.store.list_runs()), 1)

    def test_stale_run_other_issue_marked_failed(self):
        run = b.claim(self.store, self.cfg, "1", NOW)
        later = NOW + timedelta(minutes=self.cfg.idle_minutes + 1)
        new = b.claim(self.store, self.cfg, "2", later)
        self.assertEqual(new["issue"], "2")
        old = self.store.load(run["run_id"])
        self.assertEqual(old["state"], "failed")
        self.assertIn("stale", [h if isinstance(h, str) else h.get("note") for h in old["history"]])

    def test_fresh_run_same_issue_is_still_refused(self):
        b.claim(self.store, self.cfg, "1", NOW)
        with self.assertRaises(b.BelayRefusal) as cm:
            b.claim(self.store, self.cfg, "1", NOW + timedelta(minutes=1))
        self.assertEqual(cm.exception.code, 5)

    def test_save_is_atomic_leaves_no_temp_files(self):
        run = b.claim(self.store, self.cfg, "1", NOW)
        for i in range(5):
            run["active_seconds"] = i
            self.store.save(run)
        self.assertEqual(self.store.load(run["run_id"])["active_seconds"], 4)
        names = sorted(p.name for p in (self.root / ".belay" / "runs").iterdir())
        self.assertEqual(names, [f"{run['run_id']}.json"])
        self.assertFalse((self.root / ".belay" / "lock").exists())

    def test_corrupt_record_raises_refusal_code_2_not_crash(self):
        run = b.claim(self.store, self.cfg, "1", NOW)
        path = self.root / ".belay" / "runs" / f"{run['run_id']}.json"
        path.write_text('{"run_id": "x", "iss', encoding="utf-8")
        with self.assertRaises(b.BelayRefusal) as cm:
            self.store.load(run["run_id"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn(path.name, cm.exception.reason)
        # list_runs and claim surface it too (never silently skipped) and the file is untouched
        for call in (self.store.list_runs,
                     lambda: b.claim(self.store, self.cfg, "2", NOW)):
            with self.assertRaises(b.BelayRefusal) as cm:
                call()
            self.assertEqual(cm.exception.code, 2)
            self.assertIn(path.name, cm.exception.reason)
        self.assertEqual(path.read_text(encoding="utf-8"), '{"run_id": "x", "iss')

    def test_non_object_record_is_refused(self):
        run = b.claim(self.store, self.cfg, "1", NOW)
        path = self.root / ".belay" / "runs" / f"{run['run_id']}.json"
        path.write_text("[1, 2]", encoding="utf-8")
        with self.assertRaises(b.BelayRefusal) as cm:
            self.store.load(run["run_id"])
        self.assertEqual(cm.exception.code, 2)

    def test_load_missing_run_is_refusal_code_2(self):
        with self.assertRaises(b.BelayRefusal) as cm:
            self.store.load("nope")
        self.assertEqual(cm.exception.code, 2)

    def test_stale_lock_file_times_out_with_refusal(self):
        (self.root / ".belay").mkdir()
        lock = self.root / ".belay" / "lock"
        lock.write_text("999999", encoding="utf-8")
        store = b.RunStore(self.root)
        store.LOCK_TIMEOUT = 0.3
        with self.assertRaises(b.BelayRefusal) as cm:
            with store.lock():
                pass
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("lock", cm.exception.reason)
        self.assertTrue(lock.exists())  # never stolen

    def test_concurrent_claims_only_one_wins(self):
        results: list = []
        barrier = threading.Barrier(8)

        def worker(i: int):
            barrier.wait()
            try:
                results.append(("ok", b.claim(self.store, self.cfg, f"issue{i}", NOW)))
            except b.BelayRefusal as e:
                results.append(("refused", e.code))
            except BaseException as e:  # pragma: no cover
                results.append(("error", repr(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual([r for r in results if r[0] == "error"], [])
        self.assertEqual(sum(1 for r in results if r[0] == "ok"), 1)
        self.assertEqual([r[1] for r in results if r[0] == "refused"], [5] * 7)
        self.assertEqual(len(self.store.list_runs()), 1)


if __name__ == "__main__":
    unittest.main()
