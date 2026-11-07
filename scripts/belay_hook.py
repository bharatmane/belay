#!/usr/bin/env python3
"""belay PreToolUse hook: makes the stop switch, the budget and the human-merge rule mechanical.

Claude Code runs this before Edit, Write, MultiEdit, NotebookEdit, Bash and PowerShell calls
(see hooks/hooks.json). It reads the hook event JSON on stdin and either prints nothing (no
opinion: the normal permission flow applies) or prints a PreToolUse `permissionDecision: "deny"`
object. It ALWAYS exits 0, so a deny is carried by JSON, never by exit code 2.

Rules, only when a Belay footprint (`<root>/.belay/` directory) exists:
  * Human-only: `gh pr merge`, approving reviews and force pushes are always denied.
  * Stop switch: while `<root>/.belay/STOP` exists, mutating calls are denied.
  * Budget: while any `working` run has spent its budget, mutating calls are denied.
Mutating = Edit/Write/MultiEdit/NotebookEdit outside `<root>/.belay/`, or a shell command that
matches the git/gh patterns below. Read-only tools and commands are never denied, and files
under `.belay/` (STOP included) stay editable, so the owner can never be trapped. Exception:
`.belay/runs/` is guarded like any other path (run records change only through belay.py).

FAIL OPEN: any internal error allows the call and prints one stderr line
`belay hook: ignored internal error: <msg>`. A broken hook must never block the user.

Command matching (a guardrail, not a sandbox) -- the command is tokenised with shlex (quotes
removed, so g"it" p'u'sh is git push) and split into segments on ; & | ( ) < > newline and
backtick. In each segment EVERY token whose basename is git/gh is examined, not just the first
word, so wrappers (sudo, env, timeout, xargs, VAR=x) are covered. Heredoc bodies are removed
first (they are data, e.g. a commit message in `$(cat <<'EOF' ... EOF)`); an unquoted-delimiter
body keeps its $(...)/backtick substitutions, which do execute. Bodies are KEPT and examined
when a shell or evaluator (bash, sh, zsh, dash, ksh, fish, pwsh, powershell, cmd, eval, source,
`.`, xargs, any path/.exe form, also behind env/sudo/timeout) appears among the line's words
outside the bodies, since the body may then run (`bash <<EOF`, `cat <<EOF | sh`). Interpreters
are not shells: `python3 - <<EOF` bodies and `python -c "os.system('git push')"` are out of
scope. Git global options (-C, -c,
--git-dir ...) are skipped to find the subcommand. Quoted text is re-examined when it holds a
command substitution ($( or backtick) or the segment calls a shell (bash -c, sh -c, eval, cmd,
pwsh, Invoke-Expression ...). It over-matches on purpose (`echo git push` is denied under STOP).
Known limits (not detected): git/gh aliases, commands built from variables ($G push), scripts or
Makefiles that run git, other binaries (hub, curl to the GitHub API, MCP tools), file writes by
shell (echo > f, sed -i), and paths outside the root via `cd` in the same command. Unterminated
quotes fall back to a cruder split that also over-matches. Heredoc detection tracks quotes,
$(...) and ((...)) approximately; a mis-detected `<<` could hide the lines after it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import sys

FILE_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path"}
SHELL_TOOLS = {"Bash", "PowerShell"}
HUMAN_REASON = "Belay never merges, approves or force-pushes; a human does this."
ERR_PREFIX = "belay hook: ignored internal error: "

GIT_MUTATING = {"commit", "push", "merge", "rebase", "pull", "cherry-pick", "revert", "am"}
GIT_OPTS_WITH_ARG = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--config-env",
                     "--super-prefix", "--exec-path", "--list-cmds"}
GH_PR_MUTATING = {"create", "merge", "review", "edit", "comment", "close", "reopen", "ready"}
GH_ISSUE_MUTATING = {"create", "edit", "comment", "close", "reopen", "delete", "transfer",
                     "lock", "unlock", "pin", "unpin", "develop"}
GH_OPTS_WITH_ARG = {"-R", "--repo", "--hostname"}
SHELL_INVOKERS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "eval", "cmd", "pwsh",
                  "powershell", "invoke-expression", "iex", "su", "busybox"}
# Words that make a heredoc body executable when they appear on the same command line.
HEREDOC_CONSUMERS = SHELL_INVOKERS | {"source", ".", "xargs"}
_PUNCT = ";&|()<>\n`"
_MAX_DEPTH = 4


# ---------------------------------------------------------------------------
# Shell command classification
# ---------------------------------------------------------------------------


_SUBST_RE = re.compile(r"\$\([^)]*\)?|`[^`]*`?")


def _read_delim(s: str, i: int) -> tuple[str, bool, bool, int]:
    """Parse a heredoc word at s[i:] (after `<<`). Returns (delimiter, quoted, dash, end)."""
    n = len(s)
    dash = i < n and s[i] == "-"
    if dash:
        i += 1
    while i < n and s[i] in " \t":
        i += 1
    word, quoted = [], False
    while i < n and s[i] not in " \t\n;&|()<>":
        c = s[i]
        if c in "'\"":
            quoted = True
            j = s.find(c, i + 1)
            j = n if j < 0 else j
            word.append(s[i + 1:j])
            i = j + 1
        elif c == "\\":
            quoted = True
            word.append(s[i + 1:i + 2])
            i += 2
        else:
            word.append(c)
            i += 1
    return "".join(word), quoted, dash, i


def strip_heredocs(s: str) -> str:
    """Remove heredoc bodies: they are data fed to stdin, not commands. A body whose delimiter
    is unquoted still expands $(...) and `...`, so those substitutions are kept. The operator
    is only recognised outside quotes (inside "$(...)" too); `<<<` is a here-string. A body
    ends at a line equal to the delimiter (surrounding whitespace ignored, `)` allowed after
    it); an unterminated body runs to the end, as in bash. Quote tracking is approximate."""
    out: list[str] = []
    # contexts: top, dq (double quotes), sub ($( ... ) inside double quotes), arith ((( ... )))
    stack = ["top"]
    pending: list[tuple[str, bool]] = []
    i, n = 0, len(s)
    while i < n:
        c, ctx = s[i], stack[-1]
        if c == "\n" and ctx != "dq" and pending:
            out.append("\n")
            i += 1
            for delim, quoted in pending:
                body = []
                while i < n:
                    j = s.find("\n", i)
                    line = s[i:] if j < 0 else s[i:j]
                    nxt = n if j < 0 else j + 1
                    stripped = line.strip()
                    if stripped == delim or (stripped.startswith(delim)
                                             and stripped[len(delim):].lstrip().startswith(")")):
                        i = i + line.find(delim) + len(delim)  # keep anything after it
                        break
                    body.append(line)
                    i = nxt
                if not quoted:
                    kept = _SUBST_RE.findall("\n".join(body))
                    if kept:
                        out.append(" ; ".join(kept) + "\n")
            pending = []
            continue
        if c == "\\" and i + 1 < n:
            out.append(s[i:i + 2])
            i += 2
            continue
        if ctx == "dq":
            if c == '"':
                stack.pop()
            elif s.startswith("$(", i):
                stack.append("sub")
                out.append("$(")
                i += 2
                continue
            out.append(c)
            i += 1
            continue
        # unquoted context (top level, or a command substitution inside double quotes)
        if c == "'":
            j = s.find("'", i + 1)
            j = n - 1 if j < 0 else j
            out.append(s[i:j + 1])
            i = j + 1
            continue
        if s.startswith("((", i):  # arithmetic: << is a shift there, not a heredoc
            stack.append("arith")
            out.append("((")
            i += 2
            continue
        if ctx == "arith":
            if s.startswith("))", i):
                stack.pop()
                out.append("))")
                i += 2
                continue
            out.append(c)
            i += 1
            continue
        if c == '"':
            stack.append("dq")
        elif c == ")" and ctx == "sub":
            stack.pop()
        elif s.startswith("<<", i) and not s.startswith("<<<", i) and (i == 0 or s[i - 1] != "<"):
            delim, quoted, _, end = _read_delim(s, i + 2)
            out.append(s[i:end])
            i = end
            if delim:
                pending.append((delim, quoted))
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _token_lists(cmd: str) -> list[list[str]]:
    """Tokenise several spellings and union the results (over-matching on purpose): as written
    (POSIX escapes: g\\it is git), with backslashes as `/` (Windows paths such as
    C:\\Git\\cmd\\git.exe) and without backticks (PowerShell escapes: pu`sh is push)."""
    cmd = re.sub(r"[\\`]\r?\n", "", cmd)  # bash and PowerShell line continuations
    variants = dict.fromkeys([cmd, cmd.replace("\\", "/"), cmd.replace("`", "")])
    return [_tokens(v) for v in variants]


def _tokens(cmd: str) -> list[str]:
    try:
        lx = shlex.shlex(cmd, posix=True, punctuation_chars=_PUNCT)
        lx.whitespace = " \t\r"
        lx.whitespace_split = True
        lx.commenters = ""  # never let `#` hide the rest of a line
        return list(lx)
    except ValueError:  # unterminated quote: crude split, over-matches
        return re.findall(r"[;&|()<>\n`]+|[^\s;&|()<>\n`]+", cmd.replace('"', "").replace("'", ""))


def _segments(tokens: list[str]) -> list[list[str]]:
    segs, cur = [], []
    for t in tokens:
        if t and all(c in _PUNCT for c in t):
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    return segs


def _prog(token: str) -> str:
    name = token.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _positionals(args: list[str], opts_with_arg: set[str]) -> list[int]:
    """Indexes of the non-option arguments (skipping the value of an option that takes one)."""
    out, skip = [], False
    for i, a in enumerate(args):
        if skip:
            skip = False
        elif a.startswith("-"):
            skip = a in opts_with_arg
        else:
            out.append(i)
    return out


def _short_has(a: str, letter: str) -> bool:
    return len(a) > 1 and a[0] == "-" and a[1] != "-" and letter in a[1:]


def _flags(args: list[str], opts_with_arg: set[str]) -> list[str]:
    """Option tokens only: the value after an option that takes one is skipped, and a token
    holding whitespace is a quoted value, never a flag."""
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a.startswith("-") and not re.search(r"\s", a):
            out.append(a)
            skip = a in opts_with_arg
    return out


GIT_PUSH_OPTS_WITH_ARG = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
GH_REVIEW_OPTS_WITH_ARG = {"-b", "--body", "-F", "--body-file", "-R", "--repo"}
_API_HUMAN_RE = re.compile(r"/merges?(?:$|[/?#])|mergePullRequest|enablePullRequestAutoMerge"
                           r"|event\s*[=:]\s*[\"']?approve\b", re.I)


def _classify_git(args: list[str]) -> tuple[bool, bool]:
    pos = _positionals(args, GIT_OPTS_WITH_ARG)
    if not pos:
        return False, False
    sub = args[pos[0]].lower()
    rest = args[pos[0] + 1:]
    human = sub == "push" and (
        any(a in ("--force", "--force-if-includes", "--mirror")
            or a.startswith(("--force-with-lease", "--force=")) or _short_has(a, "f")
            for a in _flags(rest, GIT_PUSH_OPTS_WITH_ARG))
        or any(a.startswith("+") for a in rest))  # +refspec is a force push
    return human, sub in GIT_MUTATING


def _classify_gh(args: list[str]) -> tuple[bool, bool]:
    pos = [args[i].lower() for i in _positionals(args, GH_OPTS_WITH_ARG)]
    if not pos:
        return False, False
    group = pos[0]
    action = pos[1] if len(pos) > 1 else ""
    if group == "pr":
        approve = action == "review" and any(
            a.startswith("--approve") or _short_has(a, "a")
            for a in _flags(args, GH_REVIEW_OPTS_WITH_ARG))
        human = action == "merge" or approve
        return human, action in GH_PR_MUTATING
    if group == "issue":
        return False, action in GH_ISSUE_MUTATING
    if group == "api":
        human = any(_API_HUMAN_RE.search(a) or re.search(r"\bAPPROVE\b", a) for a in args)
        method = "GET"
        for j, a in enumerate(args):
            if a in ("-X", "--method") and j + 1 < len(args):
                method = args[j + 1].upper()
            elif a.startswith("--method="):
                method = a.split("=", 1)[1].upper()
            elif a.startswith("-X") and len(a) > 2:
                method = a[2:].upper()
        has_fields = any(a in ("-f", "-F", "--field", "--raw-field", "--input")
                         or a.startswith(("--field=", "--raw-field=", "--input=")) for a in args)
        return human, method != "GET" or has_fields
    return False, False


def _has_input_consumer(cmd: str) -> bool:
    return any(_prog(t) in HEREDOC_CONSUMERS for toks in _token_lists(cmd) for t in toks)


def classify_command(cmd: str, depth: int = 0, keep_heredocs: bool = False) -> tuple[bool, bool]:
    """(human_only, mutating) for a Bash/PowerShell command line.

    Heredoc bodies are dropped (they are data, e.g. a commit message) unless a shell or
    evaluator appears among the line's words outside the bodies (bash <<EOF, cat <<EOF | sh,
    source /dev/stdin <<EOF ...): then the bodies may be executed and are examined, at this
    level and in every nested $(...) / bash -c level below it.
    """
    human = mutating = False
    if depth > _MAX_DEPTH:
        return False, True  # absurd nesting: mutating (denied under STOP/budget), not human-only
    stripped = strip_heredocs(cmd)
    keep_heredocs = keep_heredocs or _has_input_consumer(stripped)
    if not keep_heredocs:
        cmd = stripped
    segs = [seg for toks in _token_lists(cmd) for seg in _segments(toks)]
    # A shell anywhere in the line (bash -c '...', echo '...' | sh) makes quoted text suspect.
    shell_call = any(_prog(t) in SHELL_INVOKERS for seg in segs for t in seg)
    for seg in segs:
        progs = [_prog(t) for t in seg]
        for i, p in enumerate(progs):
            if p in ("git", "gh"):
                h, m = (_classify_git if p == "git" else _classify_gh)(seg[i + 1:])
                human, mutating = human or h, mutating or m
        for t in seg:
            if "$(" in t or "`" in t or (shell_call and re.search(r"\s", t)):
                inner = t.replace("$(", ";").replace("`", ";").replace(")", ";")
                h, m = classify_command(inner, depth + 1, keep_heredocs)
                human, mutating = human or h, mutating or m
        if any(p in SHELL_INVOKERS for p in progs):
            h, m = (classify_command(" ".join(seg[1:]), depth + 1, keep_heredocs)
                    if len(seg) > 1 else (False, False))
            human, mutating = human or h, mutating or m
    return human, mutating


# ---------------------------------------------------------------------------
# Footprint and paths
# ---------------------------------------------------------------------------


def find_root(start: str) -> str | None:
    """Walk up from `start` to the first directory holding `.belay/`. Stop at a git top-level
    (a directory holding `.git`) so a footprint above the repository never applies."""
    d = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(d, ".belay")):
            return d
        if os.path.exists(os.path.join(d, ".git")):
            return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _canon(p: str) -> str:
    return os.path.normcase(os.path.realpath(p))


def _under_belay(path: str, root: str, sub: str = "") -> bool:
    base = _canon(os.path.join(root, ".belay", sub) if sub else os.path.join(root, ".belay"))
    target = _canon(path)
    try:
        return os.path.commonpath([base, target]) == base
    except ValueError:  # different drives
        return False


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def _load_helper():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "belay.py")
    if "belay_hook_helper" in sys.modules:
        return sys.modules["belay_hook_helper"]
    spec = importlib.util.spec_from_file_location("belay_hook_helper", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look their module up in sys.modules
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return mod


def _deny(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": reason}}, ensure_ascii=True)


def _stop_reason(root: str) -> str:
    return (f"Belay stop switch is on ({os.path.join(root, '.belay', 'STOP')} exists): file edits, "
            "commits, pushes, merges and tracker changes are paused. Read-only commands still "
            "work. To resume, the owner deletes .belay/STOP (for example: rm .belay/STOP).")


def _budget_reason(helper, root: str, now) -> str | None:
    cfg, _ = helper.load_config(os.path.join(root, ".belay.toml"))
    for run in helper.RunStore(root).list_runs():
        if run.get("state") != "working":
            continue
        bd = helper.budget(cfg, run, now)
        if bd["spent"]:
            return (f"belay code 4: budget spent for run {run['run_id']} "
                    f"({bd['active_minutes']:.1f} of {bd['limit_minutes']} active minutes). Stop "
                    "and ask the owner to extend it (belay.py override <run> --as <owner> "
                    "--kind extend --minutes N --reason ...).")
    return None


def decide(data: dict, now) -> str | None:
    """Return a deny reason, or None to stay silent."""
    if not isinstance(data, dict):
        raise ValueError("event is not a JSON object")
    tool = data.get("tool_name")
    if not isinstance(tool, str):
        raise ValueError("tool_name missing")
    if tool not in FILE_TOOLS and tool not in SHELL_TOOLS:
        return None
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        raise ValueError("tool_input missing")
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()

    roots = []
    r = find_root(cwd)
    if r:
        roots.append(r)
    human = False
    target = None
    if tool in FILE_TOOLS:
        target = tool_input.get(FILE_TOOLS[tool]) or tool_input.get("file_path")
        if not isinstance(target, str) or not target:
            raise ValueError(f"{tool} without a file path")
        target = os.path.join(cwd, target)  # an absolute target replaces cwd
        r = find_root(os.path.dirname(os.path.abspath(target)))
        if r and r not in roots:
            roots.append(r)
    else:
        cmd = tool_input.get("command")
        if not isinstance(cmd, str):
            raise ValueError(f"{tool} without a command string")
        human, mutating = classify_command(cmd)
        if not mutating and not human:
            return None
    if not roots:
        return None
    if human:
        return HUMAN_REASON

    # Files under .belay/ stay editable so the owner can clear STOP, except run records, which
    # change only through scripts/belay.py (guarded like any other edit).
    def exempt(rt: str) -> bool:
        return (target is not None and _under_belay(target, rt)
                and not _under_belay(target, rt, "runs"))

    runs_note = ""
    if target is not None and any(_under_belay(target, rt, "runs") for rt in roots):
        runs_note = " Run records under .belay/runs/ change only through scripts/belay.py."
    mutating_roots = [rt for rt in roots if not exempt(rt)]
    for rt in mutating_roots:
        if os.path.exists(os.path.join(rt, ".belay", "STOP")):
            return _stop_reason(rt) + runs_note
    if not mutating_roots:
        return None
    helper = _load_helper()
    if now is None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
    for rt in mutating_roots:
        reason = _budget_reason(helper, rt, now)
        if reason:
            return reason + runs_note
    return None


def _one_line(e: BaseException) -> str:
    msg = getattr(e, "reason", None) or str(e) or type(e).__name__
    return f"{type(e).__name__}: {msg}".replace("\r", " ").replace("\n", " ")


def main(stdin_text: str, now=None) -> tuple[int, str, str]:
    """Pure seam: (exit_code, stdout, stderr). Exit code is always 0."""
    try:
        if sys.version_info < (3, 11):
            raise RuntimeError("Python 3.11+ is required")
        reason = decide(json.loads(stdin_text.lstrip("﻿")), now)
        return 0, (_deny(reason) + "\n") if reason else "", ""
    except Exception as e:  # FAIL OPEN, never a traceback
        return 0, "", ERR_PREFIX + _one_line(e) + "\n"


def _cli() -> int:
    try:
        text = sys.stdin.buffer.read().decode("utf-8", "replace")
    except Exception as e:
        sys.stderr.write(ERR_PREFIX + _one_line(e) + "\n")
        return 0
    code, out, err = main(text)
    try:
        if out:
            sys.stdout.write(out)
            sys.stdout.flush()
        if err:
            sys.stderr.write(err)
            sys.stderr.flush()
    except Exception:
        pass
    return code


if __name__ == "__main__":
    try:
        sys.exit(_cli())
    except SystemExit:
        raise
    except BaseException:
        sys.exit(0)
