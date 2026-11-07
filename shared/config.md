# Configuration reference

Belay reads one file, `.belay.toml`, in the repository root. Start from
[`.belay.toml.example`](../.belay.toml.example). Every key is optional: a missing file
means "all defaults", which is a safe but limited setup (no owner, so no overrides).

## Where the file is found

The helper uses `<root>/.belay.toml`, where `<root>` is `--root` if given, otherwise the
main checkout of the current repository (so a linked worktree shares the main checkout's
file), otherwise the current directory. `--config <path>` replaces the whole path. The hook
reads `<root>/.belay.toml` for the root that holds `.belay/`, resolving a linked worktree
the same way. Details and limits are in [safety](safety.md#hook-and-helper-limits).

Add `.belay/` to `.gitignore`. It holds run records, the stop file and scratch files that
must never be committed; skills check with `git check-ignore -q .belay/` and stop if it is
not ignored.

## How the file is checked

The file is TOML (Python's standard `tomllib`, UTF-8, a byte-order mark is tolerated).

| Problem | Result |
|---|---|
| File missing | Defaults; the helper adds the warning `config file not found: <path>; using defaults` |
| Unknown key, top level or in a known table | Warning `unknown config key: <key>` (or `<table>.<key>`); the key is ignored |
| Not valid TOML, unreadable, not UTF-8 | Refusal, exit code 2: `cannot read config <path>: ...` |
| Wrong type, out-of-range value, a known table that is not a table | Refusal, exit code 2: `invalid config: ...` |

Warnings never stop a run; refusals stop every helper command. Read warnings: a typo such
as `[budget] active_minute` is only a warning, and the default silently applies. Integers
must be TOML integers: `true`, `1.5` and `"90"` are rejected.

## Keys

"Read by" says which part acts on the key. **Helper** is `scripts/belay.py`, **hook** is the
PreToolUse hook, **skills** means the instructions in the skills and adapters.

### Top level

| Key | Type | Default | Rule | Read by | Meaning |
|---|---|---|---|---|---|
| `tracker` | string | `"github"` | `"github"` or `"jira"` | skills; stored in each run record | Where issues live. Only `"github"` works today; the [Jira adapter](adapters/jira.md) is planned. |
| `base_branch` | string | `"main"` | none | skills | Branch worktrees start from and PRs target. |
| `idle_minutes` | integer | `60` | `>= 0` | helper | A gap between heartbeats longer than this is credited as only this many minutes, and a `working` run whose last heartbeat is older is stale: the next `claim` recovers it. |
| `high_risk_paths` | list of strings | `[]` | each entry non-blank after trimming; no `[` | helper (`check-paths`) | Globs whose changes need the approved label (see below). |
| `test_command` | string | `""` | none | helper (`hand-off`); skills | What "tests pass" means. When set, `hand-off` accepts only evidence recorded with exactly this command string. When empty, any passing command counts. |
| `owner` | string | `""` | trimmed | helper (`override`) | GitHub login allowed to extend or reopen a run, compared case-insensitively. Empty means nobody can (exit code 9). |
| `pr_draft` | boolean | `true` | none | skills | Open new PRs as drafts. Never changes an existing PR. |

### `[github]`

| Key | Type | Default | Read by | Meaning |
|---|---|---|---|---|
| `github.repo` | string | `""` | skills | `owner/name` for every `gh` call. Empty means the repository of the current directory (see the [GitHub adapter](adapters/github.md)). |

### `[jira]`

Reserved for the planned Jira adapter. Accepted so a file can be prepared, but nothing reads
these keys yet.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `jira.site` | string | `""` | Site host, for example `example.atlassian.net`. |
| `jira.project` | string | `""` | Project key, for example `PROJ`. |

### `[labels]`

Tracker label names for each state. Each value must be a non-blank string. Like GitHub,
the helper matches the stop and approved labels ignoring case and surrounding spaces, so
`Belay:Stop` on the issue counts as `belay:stop`.

| Key | Default | Set by | Meaning |
|---|---|---|---|
| `labels.ready` | `"belay:ready"` | a human | The issue may be dispatched. Belay never adds it. |
| `labels.working` | `"belay:working"` | Belay | A run is working on the issue. |
| `labels.waiting` | `"belay:waiting"` | Belay | The run asked questions and waits for a human. |
| `labels.review` | `"belay:review"` | Belay | A PR is open and waits for human review. |
| `labels.failed` | `"belay:failed"` | Belay | The run hit a limit or failed; see its escape-hatch comment. |
| `labels.stop` | `"belay:stop"` | a human | Stop switch for this issue (`check-stop --label`, exit code 3). Belay never removes it. |
| `labels.approved` | `"belay:approved"` | a human | Allows changes to `high_risk_paths` (`check-paths`, exit code 8 without it). Belay never adds it. |

### `[budget]`

| Key | Type | Default | Rule | Read by | Meaning |
|---|---|---|---|---|---|
| `budget.active_minutes` | integer | `90` | `> 0` | helper, hook | Working minutes per run, plus any owner extensions. Time in `waiting` does not count. When spent, `heartbeat` exits 4 and the hook denies mutating tool calls (the hook sees the active time recorded at the last `heartbeat`). An owner `extend` adds minutes here only; it never adds attempts. |
| `budget.attempts_per_stage` | integer | `3` | `> 0` | helper (`attempt`) | Tries allowed per named stage; the next `attempt` exits 4. |

## `high_risk_paths` globs

Patterns are relative to the repository root. Feed `check-paths` repo-relative paths: run
`git diff --name-only <base_branch>...HEAD` in the worktree and pass its output with
`--changed -`. Absolute paths cannot be resolved reliably.

Paths and patterns are compared after normalising: `\` becomes `/`, a leading `./` or `/`
and a drive letter are dropped, `..` is resolved.

| Pattern | Matches |
|---|---|
| `*` | any characters within one path segment (never `/`) |
| `?` | one character within a segment |
| `**` | any number of segments; `**/x` also matches a top-level `x` |
| trailing `/` | the whole directory: `src/auth/` equals `src/auth/**` |

Matching is case-insensitive on every platform. This is deliberate: the check guards a
refusal, so it must over-match (`SRC/Auth/x.py` on a case-insensitive file system) rather
than let a change slip through. Character classes (`[abc]`) are refused at load time
rather than silently treated as literal text.

## Example

```toml
test_command = "python -m pytest -q"
owner = "octocat"
high_risk_paths = ["migrations/**", "src/auth/", "**/*.pem"]

[budget]
active_minutes = 60
```
