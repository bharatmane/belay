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
HUMAN = "a human does this."
TAMPER = "owner's controls"


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

    def test_edit_denied_and_reason_tells_model_not_to_clear(self):
        reason = self.assertDeny(self.run_hook("Edit", file_path=str(self.root / "a.py"),
                                               old_string="a", new_string="b"))
        self.assertIn(".belay/STOP", reason)
        self.assertIn("Do not delete", reason)
        self.assertIn("owner", reason)
        self.assertNotIn("rm ", reason)
        self.assertNotIn("for example", reason)

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

    def test_rm_stop_file_denied(self):
        self.assertDeny(self.bash("rm .belay/STOP"), TAMPER)
        self.assertDeny(self.run_hook("PowerShell", command="Remove-Item .belay/STOP"), TAMPER)

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
                    "git pu`sh", "gh label create bug", "gh label delete old"):
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


COMMIT_MERGE = "git commit -m \"$(cat <<'EOF'\nfeat: deny gh pr merge\nEOF\n)\""
COMMIT_FORCE = ("git commit -m \"$(cat <<'EOF'\nfix: block git push -f and gh pr review "
                "--approve\n\nCo-Authored-By: x <x@y>\nEOF\n)\"")
PR_BODY = ("gh pr create --title t --body \"$(cat <<'EOF'\n## Summary\nA human runs "
           "gh pr merge 5; we never git push --force.\nEOF\n)\"")


class TestHeredocs(HookBase):
    def setUp(self):
        super().setUp()
        self.footprint()  # healthy: no STOP, no runs

    def test_message_text_in_quoted_heredocs_allowed(self):
        for cmd in (COMMIT_MERGE, COMMIT_FORCE, PR_BODY,
                    "git commit -F - <<'EOF'\nnever git push --force\nEOF",
                    "git commit -F - <<\"EOF\"\ngh pr merge 1\nEOF",
                    "git commit -F - <<\\EOF\ngh pr merge 1\nEOF",
                    "git commit -F - <<-EOF\n\tgh pr merge 1\n\tEOF",
                    "git commit -m \"$(cat <<'EOF'\nIt's \"gh pr merge\" time\nEOF\n)\"",
                    "cat <<EOF > notes.txt\ngh pr merge 1\nEOF\ngit status"):
            with self.subTest(cmd=cmd):
                self.assertAllow(self.bash(cmd))

    def test_real_commands_still_denied(self):
        for cmd in ("git commit -m \"$(gh pr merge 1)\"", "echo $(gh pr merge 1)",
                    "bash -c \"git push -f\"", "cat <<EOF\nx\nEOF\ngh pr merge 1",
                    "cat <<EOF\n$(gh pr merge 1)\nEOF", "cat <<EOF\n`gh pr merge 1`\nEOF",
                    "x=$(cat <<EOF\nx\nEOF)\ngh pr merge 1",
                    "echo \"<<EOF\"\ngh pr merge 1\nEOF", "echo '<<EOF'\ngh pr merge 1\nEOF",
                    "cat <<<EOF\ngh pr merge 1", "echo $((1<<2))\ngh pr merge 1",
                    "(( x = 1<<2 ))\ngh pr merge 1", "cat <<EOF\nEOFX\nEOF\ngh pr merge 1",
                    "cat <<EOF; gh pr merge 1\nbody\nEOF"):
            with self.subTest(cmd=cmd):
                self.assertDeny(self.bash(cmd), HUMAN)

    def test_heredoc_fed_to_a_shell_is_examined(self):
        for cmd in ("bash <<'EOF'\ngit push --force\nEOF\n", "sh <<EOF\ngh pr merge 5\nEOF",
                    "cat <<'EOF' | bash\ngh pr merge 5\nEOF", "bash -s <<'EOF'\ngh pr merge 5\nEOF",
                    "/bin/bash <<'EOF'\ngh pr merge 5\nEOF", "sudo zsh <<'EOF'\ngh pr merge 5\nEOF",
                    "pwsh.exe -Command - <<'EOF'\ngh pr merge 5\nEOF",
                    "source /dev/stdin <<'EOF'\ngh pr merge 5\nEOF",
                    ". /dev/stdin <<'EOF'\ngh pr merge 5\nEOF",
                    "xargs -I{} sh -c {} <<'EOF'\ngh pr merge 5\nEOF",
                    "cat <<'EOF' | env timeout 5 dash\ngit push -f\nEOF",
                    "bash -c \"$(cat <<'EOF'\ngh pr merge 5\nEOF\n)\"",
                    "C:\\Program Files\\Git\\bin\\bash.exe <<'EOF'\ngh pr merge 5\nEOF"):
            with self.subTest(cmd=cmd):
                self.assertDeny(self.bash(cmd), HUMAN)

    def test_heredoc_into_python_allowed(self):
        # python is not a shell: python -c "os.system('git push')" style bypasses are out of scope
        self.assertAllow(self.bash("python3 - <<EOF\nprint('gh pr merge 5')\nEOF"))
        self.assertAllow(self.bash("python3 - <<'EOF'\nimport os\n# git push --force\nEOF"))

    def test_shell_word_inside_message_body_does_not_keep_body(self):
        self.assertAllow(self.bash("git commit -m \"$(cat <<'EOF'\nrun bash, then gh pr merge\n"
                                   "EOF\n)\""))

    def test_heredoc_commit_denied_under_stop(self):
        self.stop()
        self.assertDeny(self.bash(COMMIT_MERGE), "STOP")


class TestNestingAndApi(HookBase):
    def test_too_deep_is_mutating_not_human(self):
        self.assertEqual(h.classify_command("ls", depth=h._MAX_DEPTH + 1), (False, True))
        self.footprint()
        deep = "eval " * 8 + "ls"
        self.assertAllow(self.bash(deep))
        self.stop()
        self.assertDeny(self.bash(deep), "STOP")

    def test_more_human_only_patterns(self):
        self.footprint()
        for cmd in ("gh api graphql -f query='mutation { mergePullRequest(input: {}) { x } }'",
                    "gh api graphql -f query='mutation { enablePullRequestAutoMerge(input: {}) }'",
                    "gh api repos/o/r/pulls/5/reviews -f event=approve",
                    "gh api repos/o/r/pulls/5/reviews --input r.json -f e=APPROVE",
                    "git push --mirror", "git push --mirror origin"):
            with self.subTest(cmd=cmd):
                self.assertDeny(self.bash(cmd), HUMAN)

    def test_option_values_are_not_flags(self):
        self.footprint()
        for cmd in ("gh pr review 5 --comment -b '-a thing'",
                    "gh pr review 5 --comment --body '-a'", "git push -o '-f' origin x",
                    "git push --push-option -f origin x"):
            with self.subTest(cmd=cmd):
                self.assertAllow(self.bash(cmd))


class TestRunRecords(HookBase):
    def runs_file(self):
        return str(self.root / ".belay" / "runs" / "x.json")

    def test_runs_dir_denied_under_stop(self):
        self.stop()
        self.assertDeny(self.run_hook("Write", file_path=self.runs_file(), content="{}"),
                        "scripts/belay.py")
        self.assertAllow(self.run_hook("Write", file_path=str(self.root / ".belay" / "STOP"),
                                       content=""))

    def test_runs_dir_denied_when_spent(self):
        self.make_run(active_seconds=10_000)
        self.assertDeny(self.run_hook("Edit", file_path=self.runs_file(), old_string="a",
                                      new_string="b"), "scripts/belay.py")

    def test_runs_dir_denied_even_when_healthy(self):
        self.make_run(active_seconds=60)
        self.assertDeny(self.run_hook("Write", file_path=self.runs_file(), content="{}"),
                        TAMPER, "scripts/belay.py")


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

    def test_failed_run_unblocks_escape_hatch(self):
        run = self.make_run(active_seconds=10_000)
        self.assertDeny(self.bash("gh issue comment 1 -b x"))
        b.fail(b.RunStore(self.root), b.Config(), run["run_id"], "budget spent", NOW)
        self.assertAllow(self.bash("gh issue comment 1 -b x"))
        self.assertAllow(self.bash("gh issue edit 1 --add-label belay:failed"))

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


class TestControlFiles(HookBase):
    """C1: shell commands that change .belay/STOP, .belay/runs/ or .belay.toml are denied in a
    footprint, with or without STOP (best-effort pattern match)."""

    def setUp(self):
        super().setUp()
        self.footprint()

    def test_tamper_commands_denied(self):
        root = str(self.root)
        for cmd in ("rm .belay/STOP", "rm -f ./.belay/STOP", "del .belay\\STOP", "erase .belay\\STOP",
                    "mv .belay/STOP /tmp/x", "rm -rf .belay", "rm -r .belay/runs/",
                    "rm .belay/runs/x.json", "echo '{}' > .belay/runs/x.json",
                    "echo x >> .belay.toml", "echo x>.belay.toml", "sed -i 's/60/0/' .belay.toml",
                    "truncate -s 0 .belay.toml", "python -c \"import os; os.remove('.belay/STOP')\"",
                    "bash -c 'rm .belay/STOP'", "echo \"$(rm .belay/STOP)\"", "rm .b*/S*",
                    "rm .belay/*", f"rm {root}/.belay/STOP", f"rm \"{root}\\.belay\\STOP\"",
                    "cd .belay && rm STOP", "ls .belay | xargs rm", "git checkout -- .belay.toml",
                    "git rm --cached .belay.toml", "cp x .belay.toml", "rm src/../.belay/STOP",
                    "echo rm .belay/STOP | sh", "unlink .belay/STOP", "sudo rm .BELAY/stop",
                    "tee .belay.toml < x", "find .belay -name STOP -delete",
                    "rm .belay/runs/../STOP", "python3 - <<'EOF'\nimport os\nEOF\nrm .belay/STOP"):
            with self.subTest(cmd=cmd):
                self.assertDeny(self.bash(cmd), TAMPER)

    def test_powershell_tamper_denied(self):
        for cmd in ("Remove-Item .belay/STOP", "Remove-Item -Force .belay\\STOP",
                    "ri .belay\\STOP", "Move-Item .belay/STOP x", "Rename-Item .belay\\STOP x",
                    "Set-Content .belay.toml 'idle_minutes = 0'", "Clear-Content .belay.toml",
                    "'x' | Out-File .belay.toml", "Remove-Item -Recurse .belay\\runs"):
            with self.subTest(cmd=cmd):
                self.assertDeny(self.run_hook("PowerShell", command=cmd), TAMPER)

    def test_reason_says_do_not_touch(self):
        reason = self.assertDeny(self.bash("rm .belay/STOP"), TAMPER)
        self.assertIn("Do not", reason)
        self.assertNotIn("rm ", reason)

    def test_cwd_inside_belay_dir_resolves_bare_names(self):
        (self.root / ".belay" / "runs").mkdir()
        for cmd in ("rm STOP", "rm runs/x.json", "rm -r *"):
            with self.subTest(cmd=cmd):
                code, out, err = h.main(event("Bash", self.root / ".belay", command=cmd), now=NOW)
                self.assertIn("deny", out, err)

    def test_reads_and_unrelated_paths_allowed(self):
        for cmd in ("ls .belay", "ls -la .belay/runs", "cat .belay/STOP", "cat .belay.toml",
                    "test -f .belay/STOP && echo stopped", "git -C . check-ignore -q .belay/",
                    "grep idle .belay.toml", "mkdir -p .belay/tmp", "rm .belay/tmp/x.md",
                    "git commit -m 'docs: explain .belay/STOP'", "cat < .belay/STOP",
                    "python scripts/belay.py --config .belay.toml status", "rm -rf build/*",
                    "rm notes.belay", "echo see .belay/STOP", "git status .belay.toml",
                    "head -n 5 .belay/runs/x.json", "rm .belayx/STOP", "git add .belay.toml"):
            with self.subTest(cmd=cmd):
                self.assertAllow(self.bash(cmd))
        for cmd in ("Test-Path .belay/STOP", "Get-Content .belay.toml",
                    "Get-ChildItem .belay\\runs"):
            with self.subTest(cmd=cmd):
                self.assertAllow(self.run_hook("PowerShell", command=cmd))

    def test_file_tools_on_config_and_runs_denied_stop_file_allowed(self):
        self.assertDeny(self.run_hook("Write", file_path=str(self.root / ".belay.toml"),
                                      content="idle_minutes = 0\n"), TAMPER)
        self.assertDeny(self.run_hook("Edit", file_path=str(self.root / ".belay" / "runs" / "a.json"),
                                      old_string="a", new_string="b"), TAMPER)
        self.assertAllow(self.run_hook("Write", file_path=str(self.root / ".belay" / "STOP"),
                                       content=""))
        self.assertAllow(self.run_hook("Write", file_path=str(self.root / ".belay" / "tmp" / "x.md"),
                                       content=""))

    def test_no_footprint_no_opinion(self):
        (self.root / ".belay").rmdir()
        self.assertAllow(self.bash("rm .belay/STOP"))
        self.assertAllow(self.bash("rm .belay.toml"))


class TestBaseBranchPush(HookBase):
    """I1: pushes to the base branch and remote branch deletions are human-only."""

    def setUp(self):
        super().setUp()
        self.footprint()

    def test_push_to_base_or_delete_denied(self):
        for cmd in ("git push origin HEAD:main", "git push origin main", "git push origin main:main",
                    "git push origin feat:refs/heads/main", "git push origin HEAD:refs/heads/main",
                    "git push origin :feature", "git push --delete origin feat",
                    "git push origin --delete feat", "git push -d origin feat",
                    "git push origin HEAD:master", "git push --prune origin",
                    "git push origin 'refs/heads/*:refs/heads/*'", "git push --all origin",
                    "git -C x push origin HEAD:Main", "git push --repo origin main",
                    "git push -u origin feat main"):
            with self.subTest(cmd=cmd):
                self.assertDeny(self.bash(cmd), HUMAN)
                self.assertEqual(h.classify_command(cmd)[0], True)

    def test_feature_pushes_allowed(self):
        for cmd in ("git push", "git push origin", "git push -u origin belay/20261107-7",
                    "git push origin HEAD:belay/x", "git push origin mainline",
                    "git push origin feature/main", "git push --set-upstream origin feat",
                    "git push origin HEAD"):
            with self.subTest(cmd=cmd):
                self.assertAllow(self.bash(cmd))

    def test_configured_base_branch(self):
        (self.root / ".belay.toml").write_text('base_branch = "develop"\n', encoding="utf-8")
        self.assertDeny(self.bash("git push origin HEAD:develop"), HUMAN)
        self.assertDeny(self.bash("git push origin main"), HUMAN)  # main/master always protected
        self.assertAllow(self.bash("git push origin HEAD:feat"))

    def test_bad_config_still_protects_main(self):
        (self.root / ".belay.toml").write_text("tracker = 3\n", encoding="utf-8")
        self.assertDeny(self.bash("git push origin HEAD:main"), HUMAN)


class TestControlLabels(HookBase):
    """I2: adding/removing the approved and stop labels (and adding ready) is human-only."""

    def setUp(self):
        super().setUp()
        self.footprint()

    def test_control_label_changes_denied(self):
        for cmd in ("gh issue edit 14 --add-label belay:approved",
                    "gh issue edit 14 --add-label 'bug,Belay:Approved'",
                    "gh issue edit 14 --add-label=belay:approved",
                    "gh issue edit 14 --remove-label belay:stop",
                    "gh issue edit 14 --remove-label belay:approved",
                    "gh issue edit 14 --add-label belay:stop",
                    "gh issue edit 14 --add-label belay:ready",
                    "gh -R o/r issue edit 14 --remove-label ' belay:stop '",
                    "gh issue create -t x --label belay:ready", "gh issue create -t x -l belay:approved",
                    "gh pr edit 14 --add-label belay:approved",
                    "gh label delete belay:stop", "gh label edit belay:review --name belay:approved",
                    "gh label edit belay:approved --name x",
                    "gh api -X POST repos/o/r/issues/14/labels -f 'labels[]=belay:approved'",
                    "gh api repos/o/r/issues/14/labels -f 'labels[]=belay:approved'",
                    "gh api -X DELETE repos/o/r/issues/14/labels/belay:stop",
                    "gh api -X DELETE repos/o/r/issues/14/labels/belay%3Astop",
                    "gh api -X PUT repos/o/r/issues/14/labels -f 'labels[]=bug'",
                    "gh api -X DELETE repos/o/r/issues/14/labels",
                    "gh api repos/o/r/issues/14/labels --input l.json",
                    "gh api -X PATCH repos/o/r/labels/belay:review -f new_name=belay:approved",
                    "gh api -X PATCH repos/o/r/issues/14 -f 'labels[]=belay:approved'"):
            with self.subTest(cmd=cmd):
                self.assertDeny(self.bash(cmd), HUMAN)

    def test_state_labels_allowed(self):
        for cmd in ("gh issue edit 14 --add-label belay:working --remove-label belay:ready",
                    "gh issue edit 14 --add-label belay:review --remove-label belay:working",
                    "gh issue edit 14 --add-label belay:waiting --remove-label belay:working",
                    "gh issue edit 14 --add-label belay:failed --remove-label belay:working",
                    "gh issue edit 3 --add-label bug", "gh issue create -t x --label bug",
                    "gh label create bug", "gh issue comment 14 -b 'please add belay:approved'",
                    "gh api -X POST repos/o/r/issues/14/labels -f 'labels[]=belay:working'"):
            with self.subTest(cmd=cmd):
                self.assertAllow(self.bash(cmd))

    def test_configured_label_names(self):
        (self.root / ".belay.toml").write_text('[labels]\napproved = "ok-to-build"\n',
                                               encoding="utf-8")
        self.assertDeny(self.bash("gh issue edit 1 --add-label OK-to-build"), HUMAN)
        self.assertDeny(self.bash("gh issue edit 1 --add-label belay:approved"), HUMAN)


class TestApiBodies(HookBase):
    """I6: gh api writes whose body cannot be inspected are human-only where it matters."""

    def setUp(self):
        super().setUp()
        self.footprint()

    def test_uninspectable_or_ref_writes_denied(self):
        for cmd in ("gh api graphql -F query=@m.graphql", "gh api graphql --input q.json",
                    "gh api graphql --input=q.json", "gh api graphql -F query=@-",
                    "gh api graphql -f query=@m.graphql", "gh api graphql --field=query=@m.graphql",
                    "gh api repos/o/r/pulls/1/reviews --input body.json",
                    "gh api repos/o/r/pulls/1/reviews -F body=@b.md -f event=COMMENT",
                    "gh api repos/o/r/pulls/1/reviews/9/events --input e.json",
                    "gh api -X PUT repos/o/r/pulls/1/merge --input m.json",
                    "gh api repos/o/r/merges --input m.json",
                    "gh api repos/o/r/git/refs/heads/main -X PATCH -F force=true",
                    "gh api -X POST repos/o/r/git/refs -f ref=refs/heads/x -f sha=abc",
                    "gh api -X DELETE repos/o/r/git/refs/heads/feat",
                    "gh api -X PUT repos/o/r/contents/a.py -f message=m -f content=eA== -f branch=main",
                    "gh api graphql -f query='mutation { mergeBranch(input: {}) { x } }'",
                    "gh api graphql -f query='mutation { updateRef(input: {force: true}) { x } }'",
                    "gh api graphql -f query='mutation { createCommitOnBranch(input: {}) { x } }'"):
            with self.subTest(cmd=cmd):
                self.assertDeny(self.bash(cmd), HUMAN)

    def test_inspectable_and_documented_calls_allowed(self):
        q = ("gh api graphql -f query='query($owner:String!,$name:String!,$number:Int!){repository"
             "(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100){nodes"
             "{isResolved}}}}}' -f owner=o -f name=r -F number=5")
        for cmd in (q, "gh api -X POST repos/o/r/pulls/5/comments -f commit_id=a -f path=x "
                       "-F line=3 -f side=RIGHT -F body=@.belay/tmp/review-5-1.md",
                    "gh api repos/o/r/git/refs/heads/main", "gh api user --jq .login",
                    "gh api repos/o/r/pulls/5/reviews -f event=COMMENT -f body=ok"):
            with self.subTest(cmd=cmd):
                self.assertAllow(self.bash(cmd))


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
