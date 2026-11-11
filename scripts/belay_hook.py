#!/usr/bin/env python3
"""belay PreToolUse hook: makes the stop switch, the budget and the human-merge rule mechanical.

Claude Code runs this before Edit, Write, MultiEdit, NotebookEdit, Bash and PowerShell calls
(see hooks/hooks.json). It reads the hook event JSON on stdin and either prints nothing (no
opinion: the normal permission flow applies) or prints a PreToolUse `permissionDecision: "deny"`
object. It ALWAYS exits 0, so a deny is carried by JSON, never by exit code 2.

Rules, only when a Belay footprint (`<root>/.belay/` directory) exists:
  * Owner's controls: file-tool edits of `.belay.toml` and `.belay/runs/`, and shell commands
    that look like they change `.belay/STOP`, `.belay/runs/` or `.belay.toml` (rm, mv, del,
    Remove-Item, redirects, sed -i, truncate, interpreters ...: see touches_controls, a
    best-effort pattern match) are always denied. The owner changes them outside the session.
  * Human-only, always denied: `gh pr merge`, approving reviews, force/mirror/all/prune pushes,
    pushes whose destination is the base branch (configured `base_branch`, plus main and
    master), remote branch deletions, adding the ready/approved/stop labels or removing the
    approved/stop labels (gh issue/pr edit, gh issue create, gh label edit/delete, gh api), and
    `gh api` merge/approve/ref/contents writes, including graphql or review/merge calls whose
    body comes from a file or stdin (`--input`, `key=@file`) and so cannot be inspected.
  * Stop switch: while `<root>/.belay/STOP` exists, mutating calls are denied.
  * Budget: while any `working` run has spent its budget, mutating calls are denied.
Mutating = Edit/Write/MultiEdit/NotebookEdit outside `<root>/.belay/`, or a shell command that
matches the git/gh patterns below. Read-only tools and commands are never denied. Other files
under `.belay/` (STOP itself, tmp/) stay editable by file tools, and the owner clears STOP
from their own terminal, which the hook never sees, so nobody is trapped.

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
shell (echo > f, sed -i) except onto the control files above, a refspec-less `git push` whose
upstream is the base branch, and paths outside the root via `cd` in the same command. Unterminated
quotes fall back to a cruder split that also over-matches. Heredoc detection tracks quotes,
$(...) and ((...)) approximately; a mis-detected `<<` could hide the lines after it.
"""
from __future__ import annotations

import fnmatch
import importlib.util
import json
import os
import posixpath
import re
import shlex
import sys
from dataclasses import dataclass, field
from urllib.parse import unquote

FILE_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path"}
SHELL_TOOLS = {"Bash", "PowerShell"}
HUMAN_REASON = ("Belay never merges, approves, force-pushes, pushes to the base branch, deletes "
                "remote branches or changes the owner's control labels (approved, stop, ready); "
                "a human does this.")
TAMPER_REASON = ("Belay denies changes to .belay/STOP, .belay/runs/ and .belay.toml: they are the "
                 "owner's controls. Do not delete, move or edit them, and do not offer to. If the "
                 "stop switch is on, pause the run (belay.py wait <run> --reason \"stop switch\") "
                 "and tell the user. Run records change only through scripts/belay.py.")
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
GH_API_OPTS_WITH_ARG = GH_OPTS_WITH_ARG | {"-X", "--method", "-f", "-F", "--field", "--raw-field",
                                           "-H", "--header", "--input", "--jq", "-q", "--template",
                                           "-t", "--cache", "-p", "--preview"}
GH_LABEL_MUTATING = {"create", "edit", "delete", "clone"}
# Over-matches on purpose: `gh api repos/o/r/pulls/1/merge` without a method is the read-only
# "is it merged?" GET, and it is denied too.
_API_HUMAN_RE = re.compile(r"/merges?(?:$|[/?#])|mergePullRequest|enablePullRequestAutoMerge"
                           r"|mergeBranch|updateRefs?\b|deleteRef\b|createCommitOnBranch"
                           r"|event\s*[=:]\s*[\"']?approve\b", re.I)
_PUSH_HUMAN_FLAGS = {"--force", "--force-if-includes", "--mirror", "--delete", "--prune", "--all",
                     "--branches"}


@dataclass(frozen=True)
class Policy:
    """Repository-specific names the classifier protects (all casefolded). The defaults apply
    when no `.belay.toml` can be read; configured names are added to them, never replace them."""
    base_branches: frozenset = frozenset({"main", "master"})
    deny_add_labels: frozenset = frozenset({"belay:ready", "belay:approved", "belay:stop"})
    deny_remove_labels: frozenset = frozenset({"belay:approved", "belay:stop"})
    control_labels: frozenset = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "control_labels", self.deny_add_labels | self.deny_remove_labels)


DEFAULT_POLICY = Policy()


def _push_human(rest: list[str], policy: Policy) -> bool:
    """Force pushes, mirror/all/prune pushes, remote branch deletions (`--delete`, `-d`, `:dst`)
    and any push whose destination is a protected base branch (`main`, `HEAD:main`,
    `x:refs/heads/main`, wildcard refspecs). A refspec-less `git push` is allowed: where it goes
    depends on the current branch and its upstream, which the hook does not read."""
    flags = _flags(rest, GIT_PUSH_OPTS_WITH_ARG)
    if any(a in _PUSH_HUMAN_FLAGS or a.startswith(("--force-with-lease", "--force="))
           or _short_has(a, "f") or _short_has(a, "d") for a in flags):
        return True
    if any(a.startswith("+") for a in rest):  # +refspec is a force push
        return True
    pos = [rest[i] for i in _positionals(rest, GIT_PUSH_OPTS_WITH_ARG)]
    repo_flag = any(a == "--repo" or a.startswith("--repo=") for a in flags)
    for ref in (pos if repo_flag else pos[1:]):
        src, sep, dst = ref.partition(":")
        if sep and not src.strip():
            return True  # `:dst` deletes the remote branch
        dst = (dst if sep else src).strip()
        if dst.lower().startswith("refs/heads/"):
            dst = dst[len("refs/heads/"):]
        if "*" in dst or dst.casefold() in policy.base_branches:
            return True
    return False


def _classify_git(args: list[str], policy: Policy = DEFAULT_POLICY) -> tuple[bool, bool]:
    pos = _positionals(args, GIT_OPTS_WITH_ARG)
    if not pos:
        return False, False
    sub = args[pos[0]].lower()
    rest = args[pos[0] + 1:]
    human = sub == "push" and _push_human(rest, policy)
    return human, sub in GIT_MUTATING


def _label_values(args: list[str]) -> list[tuple[str, str]]:
    """(kind, label) pairs from --add-label/--label/-l (kind "add") and --remove-label ("remove"),
    in both `--opt value` and `--opt=value` forms; comma lists are split; casefolded."""
    out = []
    for j, a in enumerate(args):
        name, eq, val = a.partition("=")
        if name not in ("--add-label", "--label", "-l", "--remove-label"):
            continue
        if not eq:
            if j + 1 >= len(args):
                continue
            val = args[j + 1]
        kind = "remove" if name == "--remove-label" else "add"
        out += [(kind, v.strip().casefold()) for v in val.split(",") if v.strip()]
    return out


def _labels_human(args: list[str], policy: Policy) -> bool:
    return any(lab in (policy.deny_add_labels if kind == "add" else policy.deny_remove_labels)
               for kind, lab in _label_values(args))


def _api_fields(args: list[str]) -> list[str]:
    """Values of -f/-F/--field/--raw-field (`key=value`), in spaced, `=` and attached forms."""
    out = []
    for j, a in enumerate(args):
        if a in ("-f", "-F", "--field", "--raw-field"):
            if j + 1 < len(args):
                out.append(args[j + 1])
        elif a.startswith(("--field=", "--raw-field=")):
            out.append(a.split("=", 1)[1])
        elif len(a) > 2 and a[:2] in ("-f", "-F") and a[2] != "-":
            out.append(a[2:])
    return out


def _classify_api(api_args: list[str], policy: Policy) -> tuple[bool, bool]:
    human = any(_API_HUMAN_RE.search(a) or re.search(r"\bAPPROVE\b", a) for a in api_args)
    method = "GET"
    for j, a in enumerate(api_args):
        if a in ("-X", "--method") and j + 1 < len(api_args):
            method = api_args[j + 1].upper()
        elif a.startswith("--method="):
            method = a.split("=", 1)[1].upper()
        elif a.startswith("-X") and len(a) > 2:
            method = a[2:].upper()
    fields = _api_fields(api_args)
    has_input = any(a == "--input" or a.startswith("--input=") for a in api_args)
    mutating = method != "GET" or bool(fields) or has_input
    pos = _positionals(api_args, GH_API_OPTS_WITH_ARG)
    path = unquote(api_args[pos[0]]).lower().split("?", 1)[0].strip("/") if pos else ""
    # A body read from a file or stdin (`--input`, `key=@file`) cannot be inspected here.
    opaque = has_input or any(f.partition("=")[2].startswith("@") for f in fields)
    if path == "graphql" and opaque:
        human = True
    if re.search(r"(?:^|/)pulls/\d+/(?:reviews|merge)(?:/|$)|(?:^|/)merges$", path) and opaque:
        human = True
    if mutating and re.search(r"(?:^|/)(?:git/refs|contents)(?:/|$)", path):
        human = True  # ref updates and file commits through the API bypass the push rules
    labels_path = re.search(r"(?:^|/)labels(?:/|$)", path)
    issue_path = re.search(r"(?:^|/)(?:issues|pulls)/\d+$", path)
    if mutating and (labels_path or issue_path):
        if opaque and (labels_path or method in ("PATCH", "POST", "PUT")):
            human = True
        if method in ("PUT", "DELETE") and re.search(r"(?:^|/)issues/\d+/labels$", path):
            human = True  # replaces or clears every label, the stop label included
        words = {w.strip("[]") for a in api_args
                 for w in re.split(r"[=,/\s]+", unquote(a).casefold()) if w}
        if (labels_path or any(f.lower().startswith("labels") for f in fields)) \
                and words & policy.control_labels:
            human = True
    return human, mutating


def _classify_gh(args: list[str], policy: Policy = DEFAULT_POLICY) -> tuple[bool, bool]:
    idx = _positionals(args, GH_OPTS_WITH_ARG)
    pos = [args[i].lower() for i in idx]
    if not pos:
        return False, False
    group = pos[0]
    action = pos[1] if len(pos) > 1 else ""
    if group == "pr":
        approve = action == "review" and any(
            a.startswith("--approve") or _short_has(a, "a")
            for a in _flags(args, GH_REVIEW_OPTS_WITH_ARG))
        labels = action in ("edit", "create") and _labels_human(args, policy)
        human = action == "merge" or approve or labels
        return human, action in GH_PR_MUTATING
    if group == "issue":
        human = action in ("edit", "create") and _labels_human(args, policy)
        return human, action in GH_ISSUE_MUTATING
    if group == "label":
        human = action in ("edit", "delete") and any(
            part.strip().casefold() in policy.control_labels
            for a in args[idx[1] + 1:] for part in a.split("="))
        return human, action in GH_LABEL_MUTATING
    if group == "api":
        return _classify_api(args[idx[0] + 1:], policy)
    return False, False


def _has_input_consumer(cmd: str) -> bool:
    return any(_prog(t) in HEREDOC_CONSUMERS for toks in _token_lists(cmd) for t in toks)


def classify_command(cmd: str, depth: int = 0, keep_heredocs: bool = False,
                     policy: Policy | None = None) -> tuple[bool, bool]:
    """(human_only, mutating) for a Bash/PowerShell command line.

    Heredoc bodies are dropped (they are data, e.g. a commit message) unless a shell or
    evaluator appears among the line's words outside the bodies (bash <<EOF, cat <<EOF | sh,
    source /dev/stdin <<EOF ...): then the bodies may be executed and are examined, at this
    level and in every nested $(...) / bash -c level below it.
    """
    policy = policy or DEFAULT_POLICY
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
                h, m = (_classify_git if p == "git" else _classify_gh)(seg[i + 1:], policy)
                human, mutating = human or h, mutating or m
        for t in seg:
            if "$(" in t or "`" in t or (shell_call and re.search(r"\s", t)):
                inner = t.replace("$(", ";").replace("`", ";").replace(")", ";")
                h, m = classify_command(inner, depth + 1, keep_heredocs, policy)
                human, mutating = human or h, mutating or m
        if any(p in SHELL_INVOKERS for p in progs):
            h, m = (classify_command(" ".join(seg[1:]), depth + 1, keep_heredocs, policy)
                    if len(seg) > 1 else (False, False))
            human, mutating = human or h, mutating or m
    return human, mutating


# ---------------------------------------------------------------------------
# Owner's control files (.belay/STOP, .belay/runs/, .belay.toml)
# ---------------------------------------------------------------------------

# Commands that only read: naming a control file with one of these is fine. Anything else that
# names one (rm, mv, cp, sed, tee, Set-Content, an interpreter, a wrapper such as sudo ...) is
# denied: unknown verbs fail closed.
_READ_ONLY = {"ls", "dir", "cat", "type", "head", "tail", "less", "more", "stat", "file", "wc",
              "test", "[", "[[", "echo", "printf", "grep", "egrep", "fgrep", "rg", "tree", "du",
              "realpath", "readlink", "basename", "dirname", "diff", "cmp", "md5sum", "sha1sum",
              "sha256sum", "find", "get-content", "gc", "get-item", "gi", "get-childitem", "gci",
              "test-path", "get-itemproperty", "gp", "select-string", "sls", "resolve-path",
              "rvpa", "write-output", "write-host", "get-filehash", "true", "false"}
_GIT_READ = {"status", "diff", "log", "show", "blame", "ls-files", "ls-tree", "check-ignore",
             "rev-parse", "grep", "cat-file", "add", "commit"}
_FIND_WRITES = ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fls")
_INTERPRETERS = {"python", "python3", "py", "pythonw", "node", "perl", "ruby", "php", "deno",
                 "bun", "lua", "awk", "gawk", "sed", "osascript"}
# Verbs that write or execute: if a control file is named anywhere in the command (even by a
# reader, as in `ls .belay | xargs rm`), one of these elsewhere in it is denied too.
_WRITERS = {"rm", "rmdir", "unlink", "del", "erase", "rd", "mv", "move", "ren", "rename", "cp",
            "copy", "dd", "tee", "truncate", "shred", "install", "ln", "xargs", "touch", "mkdir",
            "remove-item", "ri", "move-item", "mi", "rename-item", "rni", "copy-item", "cpi",
            "set-content", "sc", "add-content", "ac", "clear-content", "clc", "out-file",
            "new-item", "ni", "clear-item", "cli", "set-item", "si"} | _INTERPRETERS | SHELL_INVOKERS
_PIECE_SPLIT = re.compile(r"[\s'\"(){};,<>|&=`]+")
_GLOB_CHARS = "*?["


def _comp_is(comp: str, name: str) -> bool:
    """A path component equals `name`, or is a glob that matches it. A glob only matches a
    dot-name when it starts with a dot itself (as in bash: `*` skips dotfiles)."""
    if comp == name:
        return True
    return (any(c in comp for c in _GLOB_CHARS) and (comp.startswith(".") or not name.startswith("."))
            and fnmatch.fnmatchcase(name, comp))


def _control_piece(piece: str, prefix: str) -> bool:
    """Does this word name `.belay` itself, `.belay/STOP`, `.belay/runs[/...]` or `.belay.toml`?
    Case-insensitive, `\\` as `/`, `..` resolved; relative words are also tried against the
    command's cwd (`prefix`, relative to the footprint root) so `rm STOP` inside `.belay/` counts."""
    p = piece.replace("\\", "/").strip().lower()
    if not p:
        return False
    cands = [p]
    if prefix and not p.startswith(("/", "~", "$")) and not re.match(r"^[a-z]:", p):
        cands.append(prefix + "/" + p)
    for c in cands:
        comps = [x for x in posixpath.normpath(c).split("/") if x]
        for i, comp in enumerate(comps):
            if _comp_is(comp, ".belay.toml"):
                return True
            if _comp_is(comp, ".belay"):
                if i == len(comps) - 1:
                    return True
                if _comp_is(comps[i + 1], "stop") or _comp_is(comps[i + 1], "runs"):
                    return True
    return False


def _words(seg: list[str]) -> list[str]:
    """The segment without leading `VAR=x` assignments (and a PowerShell `$v =`)."""
    i = 0
    while i < len(seg) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", seg[i]):
        i += 1
    if i + 1 < len(seg) and seg[i].startswith("$") and seg[i + 1] == "=":
        i += 2
    return seg[i:]


def _is_belay_helper(words: list[str]) -> bool:
    """`python[3] [-opts] .../belay.py ...`: the helper changes run records under its own lock."""
    rest = [w for w in words[1:] if not w.startswith("-")]
    return bool(rest) and _prog(rest[0]) == "belay.py"


def _seg_read_only(words: list[str]) -> bool:
    if not words:
        return True
    w = _prog(words[0])
    if w == "git":
        pos = _positionals(words[1:], GIT_OPTS_WITH_ARG)
        return bool(pos) and words[1 + pos[0]].lower() in _GIT_READ
    if w == "gh":
        return True
    if w == "find":
        return not any(t.lower().startswith(_FIND_WRITES) for t in words[1:])
    if w in _INTERPRETERS:
        return _is_belay_helper(words)
    return w in _READ_ONLY


def _segments_with_punct(tokens: list[str]) -> list[tuple[str, list[str]]]:
    segs, cur, punct = [], [], ""
    for t in tokens:
        if t and all(c in _PUNCT for c in t):
            if cur:
                segs.append((punct, cur))
            cur, punct = [], t
        else:
            cur.append(t)
    if cur:
        segs.append((punct, cur))
    return segs


def touches_controls(cmd: str, prefix: str = "", depth: int = 0, keep_heredocs: bool = False) -> bool:
    """True if a shell command looks like it changes `.belay/STOP`, `.belay/runs/` or `.belay.toml`.

    BEST EFFORT, over-matching: a segment that names a control file (after quotes, `\\`, `..`,
    globs such as `.b*/S*`, and the cwd are taken into account) is denied unless its verb only
    reads (ls, cat, test, grep, Get-Content, Test-Path, read-only git, gh, the belay.py helper
    ...); output redirection onto one is denied; and a command that names one anywhere is denied
    if it also runs a writer, shell or interpreter (`ls .belay | xargs rm`). Quoted text with
    spaces is data for git and gh (commit messages, bodies) and is split into words for every
    other verb. Not detected: paths built from variables ($D/STOP), a `cd` into `.belay` in an
    earlier tool call, scripts that delete files, and `git clean`.
    """
    if depth > _MAX_DEPTH:
        return False
    stripped = strip_heredocs(cmd)
    keep_heredocs = keep_heredocs or _has_input_consumer(stripped)
    if not keep_heredocs:
        cmd = stripped
    for toks in _token_lists(cmd):
        named_any = writer_any = False
        for punct, seg in _segments_with_punct(toks):
            if punct in ("<", "<<<"):
                continue  # the file an input redirection reads
            words = _words(seg)
            w = _prog(words[0]) if words else ""
            pieces = [p for t in words if not (w in ("git", "gh") and re.search(r"\s", t))
                      for p in _PIECE_SPLIT.split(t) if p]
            named = any(_control_piece(p, prefix) for p in pieces)
            read_only = _seg_read_only(words)
            if named and not read_only:
                return True
            named_any = named_any or named
            writer_any = writer_any or (w in _WRITERS and not read_only)
            for t in seg:
                if "$(" in t or "`" in t:
                    inner = t.replace("$(", ";").replace("`", ";").replace(")", ";")
                    if touches_controls(inner, prefix, depth + 1, keep_heredocs):
                        return True
        if named_any and writer_any:
            return True
    return False


# ---------------------------------------------------------------------------
# Footprint and paths
# ---------------------------------------------------------------------------


def _main_checkout(worktree: str) -> str | None:
    """For a linked worktree (`.git` is a FILE `gitdir: <main>/.git/worktrees/<name>`), the
    main checkout root: <gitdir>/commondir names the main .git dir (relative to gitdir) and
    the root is its parent. None for anything else (submodule, corrupt file, bare repo)."""
    try:
        with open(os.path.join(worktree, ".git"), encoding="utf-8") as f:
            first = f.readline().strip()
        if not first.startswith("gitdir:"):
            return None
        gitdir = os.path.join(worktree, first[len("gitdir:"):].strip())
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as f:
            common = f.readline().strip()
        if not common:
            return None
        main_git = os.path.normpath(os.path.join(gitdir, common))
        if os.path.basename(main_git) != ".git" or not os.path.isdir(main_git):
            return None
        return os.path.dirname(main_git)
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def find_root(start: str) -> str | None:
    """Walk up from `start` to the first directory holding `.belay/`. Stop at a git top-level
    (a directory holding `.git`) so a footprint above the repository never applies. A linked
    worktree without its own `.belay/` uses its main checkout's footprint (no subprocess)."""
    d = os.path.abspath(start)
    while True:
        git = os.path.join(d, ".git")
        if os.path.isfile(git):
            # A linked worktree: the main checkout's footprint wins even when the worktree has a
            # .belay/ of its own (dispatch keeps body files in <worktree>/.belay/tmp), otherwise
            # that STOP and those run records would be ignored.
            main = _main_checkout(d)
            if main and os.path.isdir(os.path.join(main, ".belay")):
                return main
            return d if os.path.isdir(os.path.join(d, ".belay")) else None
        if os.path.isdir(os.path.join(d, ".belay")):
            return d
        if os.path.exists(git):
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
            "work. Only the owner clears the stop switch, outside this session. Do not delete, "
            "move or edit .belay/STOP and do not offer to: pause the run (belay.py wait <run> "
            "--reason \"stop switch\") and tell the user the switch is on.")


def _policy(roots: list[str]) -> Policy:
    """Protected names for these footprints: defaults plus each root's configured base branch
    and labels. A config that cannot be read keeps the defaults (the human-only rules must not
    fail open because of a typo in .belay.toml)."""
    base, add, rem = (set(DEFAULT_POLICY.base_branches), set(DEFAULT_POLICY.deny_add_labels),
                      set(DEFAULT_POLICY.deny_remove_labels))
    for rt in roots:
        try:
            cfg, _ = _load_helper().load_config(os.path.join(rt, ".belay.toml"))
        except Exception:
            continue
        base.add(cfg.base_branch.strip().casefold())
        add |= {cfg.labels[n].strip().casefold() for n in ("ready", "approved", "stop")}
        rem |= {cfg.labels[n].strip().casefold() for n in ("approved", "stop")}
    return Policy(frozenset(base), frozenset(add), frozenset(rem))


def _cwd_prefix(cwd: str, root: str) -> str:
    """cwd relative to root with `/` separators; "" at the root or outside it."""
    try:
        rel = os.path.relpath(_canon(cwd), _canon(root)).replace("\\", "/")
    except ValueError:
        return ""
    return "" if rel == "." or rel == ".." or rel.startswith("../") else rel


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
    if not roots:
        return None

    if target is None:
        # The owner's control files: denied in any footprint, STOP or not.
        if any(touches_controls(cmd, _cwd_prefix(cwd, rt)) for rt in roots):
            return TAMPER_REASON
        human, mutating = classify_command(cmd, policy=_policy(roots))
        if human:
            return HUMAN_REASON
        if not mutating:
            return None
    elif any(_under_belay(target, rt, "runs")
             or _canon(target) == _canon(os.path.join(rt, ".belay.toml")) for rt in roots):
        return TAMPER_REASON

    # Other files under .belay/ (STOP, tmp/) stay editable, so the owner can create STOP and
    # skills can write body files.
    mutating_roots = [rt for rt in roots if not (target is not None and _under_belay(target, rt))]
    for rt in mutating_roots:
        if os.path.exists(os.path.join(rt, ".belay", "STOP")):
            return _stop_reason(rt)
    if not mutating_roots:
        return None
    helper = _load_helper()
    if now is None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
    for rt in mutating_roots:
        reason = _budget_reason(helper, rt, now)
        if reason:
            return reason
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
