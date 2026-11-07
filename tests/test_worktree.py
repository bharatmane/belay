"""Linked git worktrees resolve to the MAIN checkout's .belay footprint (hook and helper)."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import belay as b  # noqa: E402
import belay_hook as h  # noqa: E402

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
HUMAN = "Belay never merges, approves or force-pushes; a human does this."


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=60, check=True)


@unittest.skipUnless(shutil.which("git"), "git is not installed; worktree tests need it")
class WorktreeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.main = base / "main"
        self.main.mkdir()
        _git(self.main, "init", "-q")
        _git(self.main, "commit", "-q", "--allow-empty", "-m", "init")
        self.wt = base / "wt"
        _git(self.main, "worktree", "add", "-q", "-b", "wtb", str(self.wt))
        self.assertTrue((self.wt / ".git").is_file())
        (self.main / ".belay").mkdir()
        (self.wt / "sub").mkdir()

    def hook(self, tool, cwd, **tool_input):
        return h.main(json.dumps({"cwd": str(cwd), "tool_name": tool, "tool_input": tool_input}),
                      now=NOW)

    def decision(self, res):
        code, out, err = res
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"] if out else None

    def test_find_root_resolves_worktree_to_main(self):
        self.assertEqual(Path(h.find_root(str(self.wt / "sub"))), self.main)
        self.assertEqual(Path(h.find_root(str(self.main))), self.main)

    def test_stop_in_main_denies_edit_in_worktree(self):
        (self.main / ".belay" / "STOP").write_text("", encoding="utf-8")
        reason = self.decision(self.hook("Edit", self.wt / "sub", file_path=str(self.wt / "a.py"),
                                         old_string="a", new_string="b"))
        self.assertIn("STOP", reason or "")
        self.assertIn("STOP", self.decision(self.hook("Bash", self.wt, command="git commit -m x")))

    def test_human_only_applies_inside_worktree(self):
        self.assertEqual(self.decision(self.hook("Bash", self.wt, command="gh pr merge 5")), HUMAN)
        self.assertIsNone(self.decision(self.hook("Bash", self.wt, command="git status")))

    def test_belay_directly_in_worktree_still_used(self):
        (self.main / ".belay").rmdir()
        (self.wt / ".belay").mkdir()
        self.assertEqual(Path(h.find_root(str(self.wt / "sub"))), self.wt)

    def test_no_footprint_anywhere_allows(self):
        (self.main / ".belay").rmdir()
        self.assertIsNone(h.find_root(str(self.wt)))
        self.assertIsNone(self.decision(self.hook("Bash", self.wt, command="gh pr merge 5")))

    def test_corrupt_git_file_falls_back(self):
        # r+ then truncate: Windows refuses "w" on the hidden .git file git creates
        with open(self.wt / ".git", "r+", encoding="utf-8") as f:
            f.write("garbage\n")
            f.truncate()
        self.assertIsNone(h.find_root(str(self.wt)))

    def test_helper_default_root_from_worktree_finds_main_run(self):
        run = b.RunStore(self.main).create("7", "github", NOW)
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "belay.py"), "status"],
                           cwd=self.wt / "sub", capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual([x["run_id"] for x in data["runs"]], [run["run_id"]])

    def test_helper_explicit_root_wins(self):
        b.RunStore(self.main).create("7", "github", NOW)
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "belay.py"), "--root",
                            str(self.wt), "status"], cwd=self.wt, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(json.loads(r.stdout)["runs"], [])

    def test_helper_default_root_in_main_and_subdir(self):
        run = b.RunStore(self.main).create("7", "github", NOW)
        (self.main / "d").mkdir()
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "belay.py"), "status"],
                           cwd=self.main / "d", capture_output=True, text=True, timeout=60)
        self.assertEqual([x["run_id"] for x in json.loads(r.stdout)["runs"]], [run["run_id"]])


if __name__ == "__main__":
    unittest.main()
