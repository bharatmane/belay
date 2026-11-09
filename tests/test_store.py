import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
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
        self.assertEqual(again["run_id"], run["run_id"])
        self.assertEqual(again["state"], "working")
        self.assertEqual(again["heartbeat"], b._iso(later))
        self.assertEqual(len(self.store.list_runs()), 1)

    def test_same_second_run_ids_do_not_collide(self):
        first = self.store.create("1", "github", NOW)
        first["state"] = "failed"
        self.store.save(first)
        second = self.store.create("1", "github", NOW)
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(second["run_id"], first["run_id"] + "-2")
        self.assertEqual(self.store.load(first["run_id"])["state"], "failed")
        third = self.store.create("1", "github", NOW)
        self.assertEqual(third["run_id"], first["run_id"] + "-3")
        self.assertEqual(len(self.store.list_runs()), 3)

    def test_stale_run_other_issue_marked_failed(self):
        run = b.claim(self.store, self.cfg, "1", NOW)
        later = NOW + timedelta(minutes=self.cfg.idle_minutes + 1)
        new = b.claim(self.store, self.cfg, "2", later)
        self.assertEqual(new["issue"], "2")
        old = self.store.load(run["run_id"])
        self.assertEqual(old["state"], "failed")
        self.assertEqual([(h["from"], h["to"], h["note"]) for h in old["history"]], [("working", "failed", "stale")])

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

    def test_naive_now_does_not_look_corrupt(self):
        b.claim(self.store, self.cfg, "1", NOW)
        naive = datetime(2026, 1, 2, 3, 5, 5)
        with self.assertRaises(b.BelayRefusal) as cm:
            b.claim(self.store, self.cfg, "2", naive)
        self.assertEqual(cm.exception.code, 5)
        later = datetime(2026, 1, 2, 3, 4, 5) + timedelta(minutes=self.cfg.idle_minutes + 1)
        self.assertEqual(b.claim(self.store, self.cfg, "1", later)["issue"], "1")

    def test_unreadable_record_is_not_called_corrupt(self):
        run = b.claim(self.store, self.cfg, "1", NOW)
        path = self.store._path(run["run_id"])
        real = Path.read_text

        def deny(self_, *a, **k):
            if self_ == path:
                raise PermissionError("denied")
            return real(self_, *a, **k)

        with mock.patch.object(Path, "read_text", deny), mock.patch.object(b.time, "sleep"):
            with self.assertRaises(b.BelayRefusal) as cm:
                self.store.load(run["run_id"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("could not read", cm.exception.reason)
        self.assertIn(path.name, cm.exception.reason)
        self.assertNotIn("delete", cm.exception.reason)

    def test_unsafe_run_id_rejected(self):
        for bad in ("../x", "a/b", "", "a.b"):
            with self.assertRaises(b.BelayRefusal) as cm:
                self.store.load(bad)
            self.assertEqual(cm.exception.code, 2)
        with self.assertRaises(b.BelayRefusal) as cm:
            self.store.save({"run_id": "../x"})
        self.assertEqual(cm.exception.code, 2)
        self.assertFalse((self.root / ".belay" / "x.json").exists())

    def test_two_stale_working_runs_resume_same_issue_fail_other(self):
        r1 = self.store.create("A", "github", NOW)
        r2 = self.store.create("B", "github", NOW)
        later = NOW + timedelta(minutes=self.cfg.idle_minutes + 1)
        got = b.claim(self.store, self.cfg, "A", later)
        self.assertEqual(got["run_id"], r1["run_id"])
        self.assertEqual(self.store.load(r2["run_id"])["state"], "failed")
        self.assertEqual(self.store.load(r1["run_id"])["state"], "working")

    def test_fresh_working_run_blocks_even_with_stale_same_issue(self):
        self.store.create("A", "github", NOW)
        fresh = self.store.create("B", "github", NOW + timedelta(minutes=self.cfg.idle_minutes))
        later = NOW + timedelta(minutes=self.cfg.idle_minutes + 1)
        with self.assertRaises(b.BelayRefusal) as cm:
            b.claim(self.store, self.cfg, "A", later)
        self.assertEqual(cm.exception.code, 5)
        self.assertEqual(self.store.load(fresh["run_id"])["state"], "working")

    def test_cross_process_claims_only_one_wins(self):
        script = """
import sys, importlib.util
from datetime import datetime, timezone
from pathlib import Path
spec = importlib.util.spec_from_file_location('belay', sys.argv[1])
m = importlib.util.module_from_spec(spec); sys.modules['belay'] = m
spec.loader.exec_module(m)
try:
    m.claim(m.RunStore(Path(sys.argv[2])), m.Config(), sys.argv[3],
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
except m.BelayRefusal as e:
    sys.exit(e.code)
"""
        belay_path = str(Path(b.__file__).resolve())
        procs = [subprocess.Popen([sys.executable, "-I", "-c", script, belay_path, str(self.root), f"i{n}"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                 for n in range(4)]
        codes = sorted(p.wait(timeout=30) for p in procs)
        self.assertEqual(codes, [0, 5, 5, 5])
        self.assertEqual(len(self.store.list_runs()), 1)


if __name__ == "__main__":
    unittest.main()
