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
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
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


def main(argv):
    scenario = argv[0] if argv else "default"
    if scenario not in fake_gh.SCENARIOS + pr_fixtures.SCENARIOS:
        print(f"unknown scenario: {scenario}", file=sys.stderr)
        return 2
    scratch = Path(tempfile.mkdtemp(prefix="belay-scratch-")).resolve()
    state = scratch / ".fake-gh" / "state.json"
    seeded = fake_gh.seed(scenario)
    if seeded["prs"]:
        seeded = build_repo(scratch, seeded)
    else:
        subprocess.run(["git", "init", "-q"], cwd=scratch, check=False)
    fake_gh.save(state, seeded)
    (scratch / ".gitignore").write_text(".belay/\n.fake-gh/\n", encoding="utf-8")
    (scratch / ".belay.toml").write_text(
        'tracker = "github"\n\n[github]\nrepo = "example/fake"\n', encoding="utf-8")
    print(f"scratch:  {scratch}")
    print(f"scenario: {scenario}")
    print(f"state:    {state}")
    print(f"PATH prefix: {HERE}{os.pathsep}")
    print(f"env: BELAY_FAKE_GH_STATE={state}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
