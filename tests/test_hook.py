import json
import os
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


def event(tool, cwd, **tool_input):
    return json.dumps({"session_id": "s", "hook_event_name": "PreToolUse", "cwd": str(cwd),
                       "tool_name": tool, "tool_input": tool_input})


class HookBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / ".git").mkdir()

    def footprint(self):
        (self.root / ".belay").mkdir(exist_ok=True)

    def stop(self):
        self.footprint()
        (self.root / ".belay" / "STOP").write_text("", encoding="utf-8")

    def run_hook(self, tool, **tool_input):
        return h.main(event(tool, self.root, **tool_input), now=NOW)

    def assertAllow(self, res):
        code, out, err = res
        self.assertEqual((code, out), (0, ""), err)

    def assertDeny(self, res, *needles):
        code, out, err = res
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        hso = data["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "deny")
        for n in needles:
            self.assertIn(n, hso["permissionDecisionReason"])
        return hso["permissionDecisionReason"]

    def bash(self, cmd):
        return self.run_hook("Bash", command=cmd)

    def make_run(self, active_seconds=0, state="working"):
        self.footprint()
        store = b.RunStore(self.root)
        run = store.create("7", "github", NOW)
        run["active_seconds"] = active_seconds
        run["state"] = state
        store.save(run)
        return run


class TestFootprint(HookBase):
    def test_no_footprint_allows_everything(self):
        self.assertAllow(self.run_hook("Edit", file_path=str(self.root / "a.py"),
                                       old_string="a", new_string="b"))
        self.assertAllow(self.bash("gh pr merge 5"))
        self.assertAllow(self.bash("git push --force"))

    def test_footprint_above_git_toplevel_is_ignored(self):
        outer = self.root / "outer"
        inner = outer / "repo"
        (outer / ".belay").mkdir(parents=True)
        (outer / ".belay" / "STOP").write_text("", encoding="utf-8")
        (inner / ".git").mkdir(parents=True)
        res = h.main(event("Bash", inner, command="gh pr merge 1"), now=NOW)
        self.assertAllow(res)

    def test_subdirectory_cwd_finds_footprint(self):
        self.stop()
        sub = self.root / "src" / "pkg"
        sub.mkdir(parents=True)
        res = h.main(event("Write", sub, file_path=str(sub / "x.py"), content="x"), now=NOW)
        self.assertDeny(res, "STOP")

    def test_file_in_other_footprint_repo_is_checked(self):
        other = Path(self._tmp.name) / "elsewhere"
        (other / ".git").mkdir(parents=True)
        self.stop()
        res = h.main(event("Write", other, file_path=str(self.root / "x.py"), content="x"), now=NOW)
        self.assertDeny(res, "STOP")


class TestStop(HookBase):
    def setUp(self):
        super().setUp()
        self.stop()

    def test_edit_denied_and_reason_says_how_to_clear(self):
        reason = self.assertDeny(self.run_hook("Edit", file_path=str(self.root / "a.py"),
                                               old_string="a", new_string="b"))
        self.assertIn(".belay/STOP", reason)
        self.assertIn("delete", reason.lower())

    def test_all_file_tools_denied(self):
        for tool, key in (("Write", "file_path"), ("MultiEdit", "file_path"),
                          ("NotebookEdit", "notebook_path")):
            with self.subTest(tool=tool):
                self.assertDeny(self.run_hook(tool, **{key: str(self.root / "n.ipynb")}))

    def test_relative_file_path_resolved_against_cwd(self):
        self.assertDeny(self.run_hook("Write", file_path="src/a.py", content=""))

    def test_edits_under_belay_dir_allowed(self):
        self.assertAllow(self.run_hook("Edit", file_path=str(self.root / ".belay" / "STOP"),
                                       old_string="", new_string=""))
        self.assertAllow(self.run_hook("Write", file_path=str(self.root / ".belay" / "notes.md"),
                                       content="x"))

    def test_belay_dir_escape_is_not_exempt(self):
        p = str(self.root / ".belay" / ".." / "src" / "a.py")
        self.assertDeny(self.run_hook("Write", file_path=p, content=""))

    def test_belay_lookalike_dir_is_not_exempt(self):
        self.assertDeny(self.run_hook("Write", file_path=str(self.root / ".belay2" / "x"),
                                      content=""))

    def test_read_only_tools_allowed(self):
        self.assertAllow(self.run_hook("Read", file_path=str(self.root / "a.py")))
        self.assertAllow(self.run_hook("Grep", pattern="x"))

    def test_git_commit_denied(self):
        self.assertDeny(self.bash("git commit -m x"), "STOP")

    def test_ls_allowed(self):
        self.assertAllow(self.bash("ls"))

    def test_rm_stop_file_allowed(self):
        self.assertAllow(self.bash("rm .belay/STOP"))
        self.assertAllow(self.run_hook("PowerShell", command="Remove-Item .belay/STOP"))

    def test_read_only_git_and_gh_allowed(self):
        for cmd in ("git status", "git log --oneline -5", "git diff HEAD~1", "git show HEAD",
                    "git log --grep commit", "git branch -a", "gh pr view 5", "gh pr list",
                    "gh issue view 3", "gh pr diff 5", "gh api repos/o/r/pulls/5",
                    "git -C sub status", "grep -rn 'git push' .", "echo done",
                    "gh pr checks 5", "git rev-parse --show-toplevel"):
            with self.subTest(cmd=cmd):
                self.assertAllow(self.bash(cmd))

    def test_mutating_commands_denied(self):
        for cmd in ("git commit -m x", "git  commit -m x", "git -C x commit", "git -c a=b commit",
                    "git --git-dir=.git commit", "git --git-dir .git --work-tree . commit",
                    "git --no-pager push", "ls && git push", "ls;git push", "ls || git push",
                    "ls | git push", "ls & git push", "ls\ngit push", "(git push)",
                    "{ git push; }", "g\"it\" p'u'sh", "/usr/bin/git push", "GIT.EXE push",
                    "C:\\Program Files\\Git\\cmd\\git.exe merge x", "FOO=1 git rebase main",
                    "sudo git push", "env GIT_X=1 git commit", "timeout 5 git push",
                    "xargs git push", "echo \"$(git push)\"", "echo `git commit`",
                    "bash -c 'git push'", "sh -c \"cd x && git commit -m y\"", "eval git push",
                    "git \\\npush", "git pull", "git cherry-pick abc", "git revert HEAD",
                    "git am x.patch", "gh pr create --fill", "gh pr review 5 --comment -b x",
                    "gh pr edit 5 --title t", "gh pr comment 5 -b x", "gh pr close 5",
                    "gh issue create -t x", "gh issue edit 3 --add-label y",
                    "gh issue comment 3 -b x", "gh issue close 3", "gh issue reopen 3",
                    "gh -R o/r issue close 3", "gh issue close 3 -R o/r",
                    "gh api -X POST repos/o/r/issues", "gh api repos/o/r/issues -f title=x",
                    "gh api --method PATCH repos/o/r/issues/1", "echo 'git push' | sh",
                    "git pu`sh"):
            with self.subTest(cmd=cmd):
                self.assertDeny(self.bash(cmd))

    def test_powershell_tool_matched_too(self):
        self.assertDeny(self.run_hook("PowerShell", command="Set-Location x; git push"))
        self.assertDeny(self.run_hook("PowerShell", command="& git commit -m x"))
        self.assertAllow(self.run_hook("PowerShell", command="Get-ChildItem"))


class TestHumanOnly(HookBase):
    def setUp(self):
        super().setUp()
        self.footprint()  # no STOP, no runs

    def test_gh_pr_merge_denied_without_stop(self):
        self.assertDeny(self.bash("gh pr merge 5"), HUMAN)

    def test_force_push_variants_denied(self):
        for cmd in ("git push --force", "git push -f origin x", "git push --force-with-lease",
                    "git push --force-with-lease=main:abc", "git push -uf origin x",
                    "git push origin +main", "git push --force-if-includes --force-with-lease",
                    "ls && git -C x push --force"):
            with self.subTest(cmd=cmd):
                self.assertDeny(self.bash(cmd), HUMAN)

    def test_approve_denied(self):
        for cmd in ("gh pr review 5 --approve", "gh pr review --approve=true 5",
                    "gh pr review 5 -a", "gh api repos/o/r/pulls/5/reviews -f event=APPROVE",
                    "gh api -X PUT repos/o/r/pulls/5/merge", "bash -c 'gh pr merge 5'"):
            with self.subTest(cmd=cmd):
                self.assertDeny(self.bash(cmd), HUMAN)

    def test_ordinary_work_allowed_when_healthy(self):
        for cmd in ("git push", "git push -u origin feat", "git commit -m x",
                    "gh pr create --draft --fill", "gh pr review 5 --comment -b ok",
                    "git push --follow-tags"):
            with self.subTest(cmd=cmd):
                self.assertAllow(self.bash(cmd))


class TestBudget(HookBase):
    def test_spent_working_run_denies_write(self):
        run = self.make_run(active_seconds=90 * 60)
        reason = self.assertDeny(self.run_hook("Write", file_path=str(self.root / "a.py"),
                                               content=""), run["run_id"])
        self.assertIn("owner", reason)
        self.assertIn("extend", reason)

    def test_spent_run_denies_mutating_bash_not_reads(self):
        self.make_run(active_seconds=10_000)
        self.assertDeny(self.bash("git commit -m x"))
        self.assertAllow(self.bash("git status"))

    def test_healthy_run_allows(self):
        self.make_run(active_seconds=60)
        self.assertAllow(self.run_hook("Write", file_path=str(self.root / "a.py"), content=""))

    def test_spent_but_not_working_run_ignored(self):
        self.make_run(active_seconds=10_000, state="review")
        self.assertAllow(self.run_hook("Write", file_path=str(self.root / "a.py"), content=""))

    def test_extension_and_config_respected(self):
        run = self.make_run(active_seconds=95 * 60)
        run["extension_minutes"] = 30
        b.RunStore(self.root).save(run)
        self.assertAllow(self.run_hook("Write", file_path=str(self.root / "a.py"), content=""))
        (self.root / ".belay.toml").write_text("[budget]\nactive_minutes = 10\n", encoding="utf-8")
        self.assertDeny(self.run_hook("Write", file_path=str(self.root / "a.py"), content=""))

    def test_belay_dir_edit_allowed_when_spent(self):
        self.make_run(active_seconds=10_000)
        self.assertAllow(self.run_hook("Write", file_path=str(self.root / ".belay" / "x"),
                                       content=""))


class TestFailOpen(HookBase):
    def assertFailOpen(self, res):
        code, out, err = res
        self.assertEqual((code, out), (0, ""))
        lines = err.splitlines()
        self.assertEqual(len(lines), 1, err)
        self.assertTrue(lines[0].startswith("belay hook: ignored internal error: "), err)
        self.assertNotIn("Traceback", err)

    def test_corrupt_run_record_fails_open(self):
        self.footprint()
        (self.root / ".belay" / "runs").mkdir()
        (self.root / ".belay" / "runs" / "bad.json").write_text("{not json", encoding="utf-8")
        self.assertFailOpen(self.run_hook("Write", file_path=str(self.root / "a.py"), content=""))

    def test_bad_config_fails_open(self):
        self.make_run()
        (self.root / ".belay.toml").write_text("tracker = 3\n", encoding="utf-8")
        self.assertFailOpen(self.run_hook("Write", file_path=str(self.root / "a.py"), content=""))

    def test_garbage_stdin_fails_open(self):
        for text in ("not json", "", "[]", '{"tool_name": 5}', '{"tool_name": "Bash"}'):
            with self.subTest(text=text):
                self.assertFailOpen(h.main(text, now=NOW))

    def test_stop_still_denies_even_with_corrupt_runs(self):
        self.stop()
        (self.root / ".belay" / "runs").mkdir()
        (self.root / ".belay" / "runs" / "bad.json").write_text("{", encoding="utf-8")
        self.assertDeny(self.bash("git push"), "STOP")


class TestScriptAndConfig(unittest.TestCase):
    def test_runs_as_script_and_denies_via_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / ".belay").mkdir()
            (root / ".belay" / "STOP").write_text("", encoding="utf-8")
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "belay_hook.py")],
                               input=event("Bash", root, command="git push").encode("utf-8"),
                               capture_output=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(r.stdout.decode("utf-8"))
            self.assertEqual(data["hookSpecificOutput"]["permissionDecision"], "deny")
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "belay_hook.py")],
                               input=b"garbage", capture_output=True, timeout=60)
            self.assertEqual((r.returncode, r.stdout), (0, b""))
            self.assertTrue(r.stderr.decode().startswith("belay hook: ignored internal error: "))

    def test_hooks_json_is_valid_and_references_script(self):
        data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        groups = data["hooks"]["PreToolUse"]
        self.assertEqual(len(groups), 1)
        tools = set(groups[0]["matcher"].split("|"))
        self.assertTrue({"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "PowerShell"} <= tools)
        handler = groups[0]["hooks"][0]
        self.assertEqual(handler["type"], "command")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/belay_hook.py", handler["command"])
        self.assertEqual(handler.get("onFailure"), "continue")


if __name__ == "__main__":
    unittest.main()
