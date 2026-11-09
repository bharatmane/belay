#!/usr/bin/env python3
"""Locate text in a pull request that addresses reviewers, agents or AI. Stdlib only.

A locator, not a verdict: it prints candidate lines for the reviewing agent to judge and
report. It reads files (or stdin for one `-`), never executes anything, and prints exactly
one JSON object on stdout.

Usage:
  scan_instructions.py [--diff FILE|-] [--pr-json FILE] [--threads-json FILE] [--text FILE]...
  scan_instructions.py --list-patterns

--diff          unified diff (`gh pr diff`, `git diff base...head`); only ADDED lines are
                scanned, each reported as diff:<path>:<line at the head>
--pr-json       output of `gh pr view --json title,body,comments,reviews,commits`
--threads-json  output of the review-thread GraphQL query (`get_pr_threads`)
--text          any plain text file; reported as text:<file name>:<line>

Exit 0 with {"ok": true, "hits": [...], "count": N}; exit 2 with {"ok": false, "reason": ...}
on a usage error. The pattern names mirror skills/pr-review/references/instruction-scan.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MAX_EXCERPT = 160
_AI = r"(?:ai|a\.i\.|llm|llms|gpt|chatgpt|claude|copilot|gemini|assistant|language\s+model|bot)"
PATTERNS: list[tuple[str, str]] = [
    ("addressed-to-ai", r"(?<![\w.])" + _AI + r"s?(?:\s+(?:code\s+)?(?:reviewers?|agents?|assistants?|tools?|models?))?\s*[:,]"),
    ("addressed-to-reviewer", r"(?:^|[#/*\-\s])(?:dear|attention|note\s+to|hey|to)\s+(?:the\s+)?"
                              r"(?:automated\s+|ai\s+)?(?:reviewers?|agents?|assistants?)\b"),
    ("approve-or-merge", r"\b(?:approve|merge|accept)\s+(?:this|the)\s+(?:pr|pull\s+request|mr|merge\s+request)\b"
                         r"|\bapprove\s+(?:this|the)\s+(?:change|changes|diff|patch|code)\b"),
    ("lgtm-ship-it", r"\b(?:lgtm|ship\s+it)\b"),
    ("ignore-instructions", r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+|your\s+)?"
                            r"(?:previous|prior|above|earlier|preceding|other)\b"),
    ("skip-review", r"\b(?:skip|no|without)\s+(?:further\s+|the\s+|a\s+|more\s+)?(?:code\s+)?review\b"
                    r"|\breview\s+(?:is\s+)?(?:not\s+(?:needed|required|necessary)|unnecessary)\b"),
    ("safe-claim", r"\b(?:this|the)\s+(?:pr|change|code|patch|diff)\s+is\s+(?:safe|approved|verified|pre-?approved)\b"
                   r"|\bno\s+need\s+to\s+(?:check|review|test|verify|look)\b"),
    ("label-or-close", r"\b(?:add|apply|remove)\s+(?:the\s+)?(?:[\w:.-]+\s+)?label\b|\bclose\s+(?:issue\s+)?#\d+\b"
                       r"|\bbelay:(?:ready|approved|stop)\b"),
    ("run-command", r"\b(?:run|execute)\s+(?:this|the\s+following)\s+(?:command|script)\b"
                    r"|\bcurl\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b"),
]
COMPILED = [(name, re.compile(rx, re.I)) for name, rx in PATTERNS]
TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{20,}")
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class UsageError(Exception):
    pass


def _mask(s: str) -> str:
    """Replace token-looking runs (20+ word chars with a letter and a digit) by <redacted>."""
    def sub(m: re.Match) -> str:
        t = m.group(0)
        return "<redacted>" if re.search(r"[A-Za-z]", t) and re.search(r"\d", t) else t
    return TOKEN_RE.sub(sub, s)


def _excerpt(line: str, start: int) -> str:
    s = line.strip()
    if len(s) > MAX_EXCERPT:
        off = max(0, min(start - (len(line) - len(line.lstrip())) - 40, len(s) - MAX_EXCERPT))
        s = s[off:off + MAX_EXCERPT]
    return _mask(s)


def scan_line(line: str) -> tuple[str, int] | None:
    for name, rx in COMPILED:
        m = rx.search(line)
        if m:
            return name, m.start()
    return None


def _hit(source: str, line: str, found: tuple[str, int]) -> dict:
    return {"source": source, "text": _excerpt(line, found[1]), "pattern": found[0]}


def scan_diff(text: str) -> list[dict]:
    hits, path, new_line, prev = [], None, None, ""
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("diff --git "):
            path, new_line = None, None
        elif line.startswith("+++ ") and prev.startswith("--- "):
            target = line[4:].split("\t", 1)[0].strip()
            path = None if target == "/dev/null" else (target[2:] if target.startswith("b/") else target)
            new_line = None
        elif HUNK_RE.match(line):
            new_line = int(HUNK_RE.match(line).group(1))
        elif new_line is not None:
            if line.startswith("+"):
                found = scan_line(line[1:])
                if found and path:
                    hits.append(_hit(f"diff:{path}:{new_line}", line[1:], found))
                new_line += 1
            elif line.startswith(" ") or line == "":
                new_line += 1
            # "-" lines are deleted (not at the head) and "\ No newline" is metadata
        prev = line
    return hits


def scan_text(label: str, text: str) -> list[dict]:
    hits = []
    for i, raw in enumerate(text.splitlines(), start=1):
        found = scan_line(raw.rstrip("\r"))
        if found:
            hits.append(_hit(f"{label}:{i}" if label.startswith("text:") else label, raw, found))
    return hits


def _login(node) -> str:
    a = node.get("author") if isinstance(node, dict) else None
    return a.get("login", "?") if isinstance(a, dict) else "?"


def scan_pr_json(text: str) -> list[dict]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise UsageError("--pr-json must hold one JSON object from gh pr view")
    hits = scan_text("pr:title", data.get("title") or "")
    hits += _multi("pr:body", data.get("body") or "")
    for i, c in enumerate(data.get("comments") or [], start=1):
        hits += _multi(f"pr:comment{i}(by {_login(c)})", c.get("body") or "")
    for i, r in enumerate(data.get("reviews") or [], start=1):
        hits += _multi(f"pr:review{i}(by {_login(r)})", r.get("body") or "")
    for c in data.get("commits") or []:
        msg = "\n".join(x for x in (c.get("messageHeadline"), c.get("messageBody")) if x)
        hits += _multi(f"pr:commit {str(c.get('oid', '?'))[:12]}", msg)
    return hits


def scan_threads_json(text: str) -> list[dict]:
    data = json.loads(text)
    try:
        nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    except (KeyError, TypeError):
        raise UsageError("--threads-json is not a reviewThreads GraphQL reply") from None
    hits = []
    for t in nodes or []:
        for c in (t.get("comments") or {}).get("nodes") or []:
            hits += _multi(f"thread:{t.get('path')}:{t.get('line')}(by {_login(c)})", c.get("body") or "")
    return hits


def _multi(label: str, text: str) -> list[dict]:
    lines = text.splitlines()
    out = []
    for i, raw in enumerate(lines, start=1):
        found = scan_line(raw.rstrip("\r"))
        if found:
            out.append(_hit(label if len(lines) == 1 else f"{label}:{i}", raw, found))
    return out


def _read(path: str, stdin_used: list[bool]) -> str:
    if path == "-":
        if stdin_used[0]:
            raise UsageError("only one input may be '-' (stdin)")
        stdin_used[0] = True
        return sys.stdin.buffer.read().decode("utf-8", errors="replace")
    p = Path(path)
    if not p.is_file():
        raise UsageError(f"file not found: {path}")
    return p.read_bytes().decode("utf-8-sig", errors="replace")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def run(argv: list[str]) -> tuple[int, dict]:
    ap = _Parser(add_help=False)
    ap.add_argument("--diff")
    ap.add_argument("--pr-json")
    ap.add_argument("--threads-json")
    ap.add_argument("--text", action="append", default=[])
    ap.add_argument("--list-patterns", action="store_true")
    try:
        a = ap.parse_args(argv)
        if a.list_patterns:
            return 0, {"ok": True, "patterns": [{"name": n, "regex": rx} for n, rx in PATTERNS]}
        if not (a.diff or a.pr_json or a.threads_json or a.text):
            raise UsageError("give at least one of --diff, --pr-json, --threads-json, --text")
        stdin_used = [False]
        hits: list[dict] = []
        if a.diff:
            hits += scan_diff(_read(a.diff, stdin_used))
        if a.pr_json:
            hits += scan_pr_json(_read(a.pr_json, stdin_used))
        if a.threads_json:
            hits += scan_threads_json(_read(a.threads_json, stdin_used))
        for t in a.text:
            hits += scan_text(f"text:{'stdin' if t == '-' else Path(t).name}", _read(t, stdin_used))
    except UsageError as exc:
        return 2, {"ok": False, "reason": str(exc)}
    except json.JSONDecodeError as exc:
        return 2, {"ok": False, "reason": f"invalid JSON input: {exc}"}
    return 0, {"ok": True, "hits": hits, "count": len(hits)}


def main() -> int:
    code, out = run(sys.argv[1:])
    sys.stdout.write(json.dumps(out) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
