#!/usr/bin/env python3
"""Create a scratch directory seeded for one fake-gh scenario.

Usage: python new_scratch.py [scenario]   (empty, has-duplicate, no-labels, default)
Prints the scratch path, the PATH prefix and the env var to use.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fake_gh  # noqa: E402


def main(argv):
    scenario = argv[0] if argv else "default"
    if scenario not in ("empty", "has-duplicate", "no-labels", "default"):
        print(f"unknown scenario: {scenario}", file=sys.stderr)
        return 2
    scratch = Path(tempfile.mkdtemp(prefix="belay-scratch-")).resolve()
    state = scratch / ".fake-gh" / "state.json"
    fake_gh.save(state, fake_gh.seed(scenario))
    subprocess.run(["git", "init", "-q"], cwd=scratch, check=False)
    print(f"scratch:  {scratch}")
    print(f"scenario: {scenario}")
    print(f"state:    {state}")
    print(f"PATH prefix: {HERE}{os.pathsep}")
    print(f"env: BELAY_FAKE_GH_STATE={state}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
