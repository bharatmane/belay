#!/usr/bin/env python3
"""Create a scratch directory seeded for one fake-gh scenario.

Usage: python new_scratch.py [scenario]   (empty, has-duplicate, injected-duplicate, no-labels, default)
Prints the scratch path, the PATH prefix and the env var to use.
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dispatch_fixtures  # noqa: E402
import fake_gh  # noqa: E402
import pr_fixtures  # noqa: E402


def git(scratch, *args):
    p = subprocess.run(["git", "-c", "user.name=Scratch", "-c", "user.email=scratch@example.com",
                        "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                       cwd=scratch, capture_output=True, text=True, check=True)
    return p.stdout.strip()


def write(scratch, files):
    for rel, text in files.items():
        f = scratch / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8", newline="\n")
        git(scratch, "add", rel)


def build_repo(scratch, seeded):
    """Real history: base commit on main, one branch per PR; real SHAs replace the placeholders."""
    git(scratch, "init", "-q", "-b", "main")
    (scratch / ".git" / "info" / "exclude").write_text(".fake-gh/\n", encoding="utf-8")
    write(scratch, pr_fixtures.BASE)
    git(scratch, "commit", "-q", "-m", "Base")
    base = git(scratch, "rev-parse", "HEAD")
    text = json.dumps(seeded)
    for pr in seeded["prs"]:
        git(scratch, "checkout", "-q", "-b", pr["headRefName"], "main")
        write(scratch, pr["changes"])
        git(scratch, "commit", "-q", "-m", pr["title"])
        head = git(scratch, "rev-parse", "HEAD")
        git(scratch, "checkout", "-q", "main")
        text = text.replace(pr["headRefOid"], head).replace(pr["baseRefOid"], base)
    return json.loads(text)


HELPER = (HERE.parent.parent / "scripts" / "belay.py").as_posix()


def readme(scratch):
    return (f"# Slug demo\n\nA tiny project used to exercise Belay.\n\n## Belay helper\n\n"
            f"Call the helper as `python {HELPER} --root {scratch.as_posix()} <cmd>` "
            f"(claim, heartbeat, evidence, hand-off, check-stop, check-paths, ...).\n"
            f"Run the tests with `python -m unittest discover -s tests`.\n")


def run_record(scratch, issue, state, now, **extra):
    """A run record in the helper's own format, saved through its RunStore."""
    sys.path.insert(0, str(HERE.parent.parent / "scripts"))
    import belay
    store = belay.RunStore(scratch)
    run = store.create(issue, "github", extra.pop("started", now))
    run.update(state=state, **extra)
    store.save(run)
    return run


def build_dispatch(scratch, scenario, seeded):
    """Git repo on main with a local bare `origin`, the demo project, and scenario run state."""
    git(scratch, "init", "-q", "-b", "main")
    files = dict(dispatch_fixtures.FILES)
    if scenario == "dispatch-no-evidence":
        files["tests/test_slow.py"] = dispatch_fixtures.SLOW_TEST
    files["README.md"] = readme(scratch)
    write(scratch, files)
    git(scratch, "commit", "-q", "-m", "Initial project")
    origin = scratch.parent / (scratch.name + "-origin.git")
    git(scratch, "init", "-q", "--bare", "-b", "main", str(origin))
    git(scratch, "remote", "add", "origin", str(origin))
    git(scratch, "push", "-q", "-u", "origin", "main")
    now = datetime.now(timezone.utc)
    if scenario == "dispatch-stop-file":
        (scratch / ".belay").mkdir()
        (scratch / ".belay" / "STOP").write_text("stopped for the eval\n", encoding="utf-8")
    elif scenario == "dispatch-budget-spent":
        # Stale heartbeat (61 min, idle limit 60) so `claim 12` recovers this run instead of refusing
        # with code 5; its first `heartbeat` then credits min(gap, 60 min) and the 90 min budget is spent.
        old = now - timedelta(minutes=61)
        iso = old.strftime("%Y-%m-%dT%H:%M:%SZ")
        run_record(scratch, "12", "working", now, started=now - timedelta(hours=2), stage="tests",
                   heartbeat=iso, last_tick=iso, active_seconds=90 * 60 - 5)
    elif scenario == "dispatch-override":
        at = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        run_record(scratch, "12", "failed", now, started=now - timedelta(hours=3), stage="tests",
                   heartbeat=at, last_tick=at, active_seconds=90 * 60,
                   history=[{"from": "working", "to": "failed", "at": at,
                             "note": "budget spent: 90.0 of 90 active minutes"}])
    return seeded


def main(argv):
    scenario = argv[0] if argv else "default"
    if scenario not in fake_gh.SCENARIOS + pr_fixtures.SCENARIOS + dispatch_fixtures.SCENARIOS:
        print(f"unknown scenario: {scenario}", file=sys.stderr)
        return 2
    scratch = Path(tempfile.mkdtemp(prefix="belay-scratch-")).resolve()
    state = scratch / ".fake-gh" / "state.json"
    seeded = fake_gh.seed(scenario)
    dispatch = scenario in dispatch_fixtures.SCENARIOS
    if dispatch:
        seeded = build_dispatch(scratch, scenario, seeded)
    elif seeded["prs"]:
        seeded = build_repo(scratch, seeded)
    else:
        subprocess.run(["git", "init", "-q"], cwd=scratch, check=False)
    fake_gh.save(state, seeded)
    if not dispatch:
        (scratch / ".gitignore").write_text(".belay/\n.fake-gh/\n", encoding="utf-8")
        (scratch / ".belay.toml").write_text(
            'tracker = "github"\n\n[github]\nrepo = "example/fake"\n', encoding="utf-8")
    print(f"scratch:  {scratch}")
    print(f"scenario: {scenario}")
    print(f"state:    {state}")
    print(f"PATH prefix: {HERE}{os.pathsep}")
    print(f"env: BELAY_FAKE_GH_STATE={state}")
    if dispatch:
        print(f"helper: python {HELPER} --root {scratch.as_posix()} <cmd>")
        print(f"origin: {scratch.parent / (scratch.name + '-origin.git')}")
        print("env (optional): BELAY_FAKE_GH_LOGIN=<login> makes `gh api user` and new comments use that login"
              " (default octocat)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
