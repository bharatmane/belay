# Manifest notes

Verified 2026-11-07 against the official Claude Code docs:
`code.claude.com/docs/en/plugins-reference` (manifest reference),
`.../plugin-marketplaces` (marketplace guide) and `.../skills` (skill frontmatter).
The docs win over any other source. Limitation: the skills page was read through a
summarising fetch that covered the first 100,000 of ~119,000 characters; the
`disable-model-invocation` entry was quoted from it directly.

## plugin.json (`.claude-plugin/plugin.json`)

- The manifest is optional; when present, `name` is the only required key.
- `name`: kebab-case, no spaces, `@`, `:` or path separators. Names starting with
  `claude-`, `anthropic-` etc. are reserved (belay is fine).
- Recommended (validator warns if missing): `version`, `description`, `author`.
- `author` is an object: `name` (required), optional `email`, `url`.
- Other metadata: `homepage` (must parse as a URL), `repository` (string URL, not
  validated), `license` (SPDX id such as `MIT`), `keywords` (array of strings).
- `version` is not checked against semver; setting it pins users to that version
  until it changes, so every release needs a new value.
- Unknown top-level keys are stripped with a warning.
- Component paths must start with `./`. `skills/` is scanned by default, so no
  `skills` key is needed. Put nothing but the manifest inside `.claude-plugin/`.
- Validate locally with `claude plugin validate .` (`--strict` makes warnings fail).

## marketplace.json (`.claude-plugin/marketplace.json`)

Required top-level keys: `name`, `owner` (object with `name`), `plugins` (array).
Optional: `description`. Each plugin entry needs `name` and `source`; `description`
is shown in `/plugin`. A relative `source` is written from the marketplace root (the
directory holding `.claude-plugin/`), starts with `./`, and must not contain `..`.
For belay the plugin lives at the repo root, so `source` is `"./"`.
The entry `name` must equal the `name` in plugin.json. The manifest `version`
overrides an entry `version`, so keep both equal (the validator enforces it).

```json
{
  "name": "belay",
  "description": "...",
  "owner": { "name": "Bharat Mane" },
  "plugins": [
    { "name": "belay", "source": "./", "description": "...", "version": "0.1.0-dev" }
  ]
}
```

Install flow: `claude plugin marketplace add <owner>/<repo>` then
`claude plugin install belay@belay`.

## Skill frontmatter: blocking model-initiated invocation

The exact field is `disable-model-invocation: true` (default `false`). Docs: "Set to
`true` to prevent Claude from automatically loading this skill. Use for workflows you
want to trigger manually with `/name`." The description is then not placed in context
and the skill runs only when the user types the slash command. It also stops
preloading into subagents.

`user-invocable: false` is the opposite (hides it from the `/` menu) and does NOT stop
Claude invoking it. Do not use it for this purpose.

Other rules: field names must match exactly (unknown fields are silently ignored);
booleans accept true/false/yes/no/on/off/1/0; frontmatter is only read when `---` is
the first line. The skills page gives no length limit for `name`; the Agent Skills
spec limits (name 64 chars lowercase-hyphen, description 1024) are enforced by the
repo validator as a portability bar. Skills uploaded to claude.ai or the Skills API
accept only `name`, `description`, `license`, `compatibility`, `metadata`,
`allowed-tools`; Claude Code accepts more, including `disable-model-invocation`.

Consequence for Task 10: `dispatch/SKILL.md` must carry
`disable-model-invocation: true`.

## Hooks (`hooks/hooks.json`)

Verified 2026-11-07 against `code.claude.com/docs/en/hooks` (hooks reference) and
`code.claude.com/docs/en/plugins-reference` (standard layout, environment variables).
Limitation: the hooks page (~290,000 characters) was read through a summarising fetch in
three windows; the facts below were quoted from it directly.

- Location: `hooks/hooks.json` at the plugin root is loaded by default (no manifest key
  needed; a `hooks` manifest key would merge with it). The file wraps the event map in a
  top-level `"hooks"` key (a file without the wrapper fails to load) and may carry a
  top-level `description`.
- Shape: `{"hooks": {"PreToolUse": [{"matcher": "...", "hooks": [{"type": "command",
  "command": "...", "args": [...], "timeout": <seconds>, "shell": "bash"|"powershell",
  "onFailure": "continue"|"block"}]}]}}`. Default command timeout 600 s.
- Matcher: a string of only letters, digits, `_`, `-`, spaces, `,` and `|` is an exact
  name or a `|`/`,` list (`Edit|Write`); any other character makes it an unanchored
  JavaScript regex. Belay uses the exact list
  `Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell` (PowerShell is the Windows shell tool).
- PreToolUse stdin: common fields (`session_id`, `transcript_path`, `cwd`,
  `permission_mode`, `hook_event_name`, ...) plus `tool_name`, `tool_input`, `tool_use_id`.
  `tool_input`: Bash `command`; Write `file_path`, `content`; Edit `file_path`,
  `old_string`, `new_string`, `replace_all`. MultiEdit uses `file_path` and NotebookEdit
  `notebook_path` (not shown in the fetched text; the hook accepts either key).
- Deny: exit 0 and print
  `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
  "permissionDecisionReason": "..."}}`. Exit 2 with stderr also blocks, but JSON gives finer
  control and the docs say to pick one approach per hook; belay uses JSON and always exits 0.
  Top-level `decision`/`reason` are deprecated for PreToolUse. Printing nothing means no
  decision (normal permission flow); the hook never prints `"allow"`, which would skip the
  user's permission prompts.
- Exit codes: 0 success (JSON applied), 2 blocking error (stderr is the reason), anything
  else non-blocking error (action goes ahead, transcript notice).
- Failure (cannot start, other exit code, timeout, unparsable JSON): the action goes ahead
  unless `"onFailure": "block"` (v2.1.295+). Exception: on Windows WITHOUT Git Bash a
  shell-form PreToolUse hook that fails with `onFailure` unset can block the tool call.
  Belay sets `"onFailure": "continue"` so a broken or missing interpreter never blocks.
- `${CLAUDE_PLUGIN_ROOT}`: absolute path of the installed plugin version, substituted
  anywhere in `command` and `args` and exported to the hook process. Windows paths use
  forward slashes, except in exec form and PowerShell-run hooks.
- Exec form (`args` present: no shell, recommended for path placeholders) vs shell form
  (`args` absent: `sh -c` on macOS/Linux, Git Bash on Windows, PowerShell when Git Bash is
  missing - `pwsh.exe` if present, else `powershell.exe` 5.1). In shell form each
  placeholder must sit in double quotes.

### Interpreter choice (`python3` vs `python`)

No single name works everywhere: macOS and many Linux distributions ship only `python3`;
the python.org Windows installer provides `python` (and `py`) but no `python3`, and
Windows' `python3` is often the Microsoft Store stub that exits 9009. Exec form can name
only one executable, so belay uses shell form with a fallback:

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/belay_hook.py" || python "${CLAUDE_PLUGIN_ROOT}/scripts/belay_hook.py"

This is safe because the hook always exits 0 (deny travels as JSON), so the fallback runs
only when `python3` could not start, before it read stdin. Tradeoffs: a missing `python3`
prints a "command not found" line on stderr (non-blocking); if `python3` is older than 3.11
the hook fails open with one stderr line instead of trying `python`; on Windows without Git
Bash under Windows PowerShell 5.1 the `||` operator does not parse, so the hook fails and
`onFailure: continue` lets the call through (no enforcement there; PowerShell 7 parses `||`).

### What the hook enforces (Task 6b)

Only when `<root>/.belay/` exists, root = first ancestor of `cwd` (and of an edited file)
holding `.belay/`, never above a directory holding `.git`:
always deny `gh pr merge`, `gh pr review --approve`/`-a`, `gh api` merge or APPROVE calls and
force pushes (`--force`, `-f` in a short-flag cluster, `--force-with-lease`, `+refspec`);
while `.belay/STOP` exists or a `working` run has spent its budget, deny Edit/Write/
MultiEdit/NotebookEdit outside `.belay/` and shell commands running git
commit/push/merge/rebase/pull/cherry-pick/revert/am, gh pr
create/merge/review/edit/comment/close/reopen/ready, gh issue
create/edit/comment/close/reopen/delete/transfer/lock/unlock/pin/unpin/develop, or a
mutating `gh api` call. Any internal error fails open with one stderr line.

Known matcher limits (guardrail, not a sandbox): git/gh aliases; commands assembled from
variables (`$G push`); scripts, Makefiles or interpreters (`python -c`) that run git; other
clients (hub, curl to the API, MCP tools such as a GitHub MCP server); shell file writes
(`echo > f`, `sed -i`) are not file-tool calls; edits and writes under `.belay/` are always
allowed (so the owner can clear STOP), which also means run records there are writable; a
corrupt run record or bad config fails open, disabling the budget check (STOP still works).
Over-matching is deliberate: `echo git push` or text mentioning `gh pr merge` inside
`bash -c '...'` is denied.

Fix round (review of 6b): heredoc bodies are stripped before matching, so the standard
`git commit -m "$(cat <<'EOF' ... EOF)"` and `gh pr create --body "$(cat <<'EOF' ...)"`
flows are not denied for message text that mentions a denied command; an unquoted
delimiter's body keeps its `$(...)`/backtick substitutions (they execute). `<<` inside
quotes, `<<<` and arithmetic `((...))` are not heredocs. Limit: the quote/context tracking
is approximate, so an exotic mis-detected `<<` could hide the lines after it. Over-deep
nesting counts as mutating, not human-only. `.belay/runs/` edits are denied under STOP or
spent budget (run records change only through `scripts/belay.py`); other `.belay/` files
stay editable. Human-only also covers `gh api` `mergePullRequest`,
`enablePullRequestAutoMerge`, `APPROVE` and `event=approve`, and `git push --mirror`;
force/approve flags are read from option tokens only (an option's value is skipped).

Fix round 2: a heredoc body is executed when a shell consumes it, so bodies are kept and
examined whenever a shell or evaluator (bash, sh, zsh, dash, ksh, fish, pwsh, powershell,
cmd, eval, source, `.`, xargs, in any path/.exe form, including behind env/sudo/timeout)
appears among the line's words outside the bodies (`bash <<'EOF'`, `cat <<EOF | sh`,
`source /dev/stdin <<EOF`, `bash -c "$(cat <<EOF ...)"`); the decision carries into nested
levels. Consequence: a line that both runs a shell and commits with a heredoc message is
examined in full (over-match). Out of scope: non-shell interpreters (`python3 - <<EOF`,
`python -c "os.system('git push')"`, node, perl, ruby).

Task 7c: a linked worktree (`.git` is a file) without its own `.belay/` uses its main
checkout's footprint (hook: `gitdir:` -> `<gitdir>/commondir` -> parent of the main `.git`,
no subprocess, fails open to "no footprint"; helper `_default_root`: parent of
`git rev-parse --path-format=absolute --git-common-dir` when it is named `.git`, else
`--show-toplevel`, else cwd; `--root` wins).
