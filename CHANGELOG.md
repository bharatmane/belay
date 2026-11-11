# Changelog

All notable changes to Belay are recorded here. Versions follow semantic versioning; the
release process is in [docs/RELEASING.md](docs/RELEASING.md).

## Unreleased

## 0.1.0 - 2026-11-11

First release.

### Added

- Helper `scripts/belay.py` (Python 3.11+ standard library, one file): `claim`, `heartbeat`,
  `budget`, `attempt`, `wait`, `resume`, `fail`, `evidence`, `hand-off`, `check-stop`,
  `check-paths`, `override`, `status`. One JSON object on stdout; exit codes 2 to 9 for
  refusals.
- PreToolUse hook `scripts/belay_hook.py`: denies merges, approving reviews and force
  pushes in any repository with `.belay/`; denies file edits, commits, pushes and tracker
  writes while `.belay/STOP` exists or a working run is over budget. Fails open.
- Skills `issue-create`, `pr-review` and `dispatch` (explicit invocation only).
- Instruction scanner `scripts/scan_instructions.py` for text aimed at reviewers or agents.
- Shared safety rules, configuration reference (`.belay.toml`), GitHub adapter and
  templates; Jira adapter documented as planned.
- Repository validator, CI on Windows, macOS and Linux, evals with a fake `gh` harness,
  smoke-test protocol and release process.
