#!/usr/bin/env python3
"""Fake GitHub CLI for Belay evals. Stdlib only, never touches the network.

State lives in $BELAY_FAKE_GH_STATE (default ./.fake-gh/state.json); every call is
appended to calls.log beside it. Scenario (empty, has-duplicate, no-labels, default)
comes from $BELAY_FAKE_GH_SCENARIO when the state file does not exist yet.
"""
import json
import os
import shlex
import sys
import time
from pathlib import Path

URL = "https://github.com/example/fake/issues/{}"
LABELS = ["bug", "enhancement", "documentation"]
BELAY = ["belay:ready", "belay:working", "belay:waiting", "belay:review", "belay:failed", "belay:stop"]
TS = "2026-01-05T09:00:00Z"
QUALIFIERS = {"in", "sort", "state", "is", "label"}


def _issue(n, title, body, state="OPEN", labels=()):
    return {"number": n, "title": title, "body": body, "state": state, "labels": list(labels),
            "author": "octocat", "createdAt": TS, "updatedAt": TS}


def seed(scenario):
    issues = [_issue(3, "Dark mode toggle", "Add a dark theme switch to settings.", labels=["enhancement"]),
              _issue(4, "Export fails when session token expired",
                     "Exporting CSV returns 401 after the session token has expired.", "CLOSED", ["bug"])]
    labels = LABELS + BELAY
    if scenario == "empty":
        issues = []
    elif scenario == "has-duplicate":
        issues.append(_issue(7, "Login page returns 500 on empty password",
                             "Submitting the login form with an empty password gives HTTP 500.", labels=["bug"]))
    elif scenario == "no-labels":
        labels = LABELS
    return {"scenario": scenario, "login": "octocat", "issues": issues,
            "labels": [{"name": n, "description": ""} for n in labels], "comments": [], "edits": []}


def state_path():
    return Path(os.environ.get("BELAY_FAKE_GH_STATE") or ".fake-gh/state.json")


def load(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return seed(os.environ.get("BELAY_FAKE_GH_SCENARIO", "default"))


def save(path, st):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(st, indent=2), encoding="utf-8")


def fail(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def parse(args):
    """Return (positionals, flags); flags map name -> list of values."""
    short = {"-S": "--search", "-R": "--repo", "-s": "--state", "-l": "--label", "-L": "--limit",
             "-t": "--title", "-F": "--body-file", "-q": "--jq"}
    pos, flags, i = [], {}, 0
    while i < len(args):
        a = short.get(args[i], args[i])
        if a.startswith("--"):
            if "=" in a:
                k, v = a.split("=", 1)
            else:
                k, v = a, args[i + 1] if i + 1 < len(args) else ""
                i += 1
            flags.setdefault(k, []).append(v)
        else:
            pos.append(args[i])
        i += 1
    return pos, flags


def shape(issue, fields):
    out = {}
    for f in fields:
        if f == "labels":
            out[f] = [{"name": n} for n in issue["labels"]]
        elif f == "author":
            out[f] = {"login": issue["author"]}
        elif f == "url":
            out[f] = URL.format(issue["number"])
        elif f in issue:
            out[f] = issue[f]
        else:
            fail(f'Unknown JSON field: "{f}"')
    return out


def fields_of(flags):
    return [f for f in flags.get("--json", [""])[0].split(",") if f]


def emit(rows, flags):
    if not fields_of(flags):
        fail("fake gh: pass --json <fields> (human output is not implemented)")
    print(json.dumps(rows))


def search_terms(q):
    terms = []
    for t in shlex.split(q):
        if t.split(":", 1)[0] in QUALIFIERS and ":" in t:
            continue
        terms.append(t.lower())
    return terms


def find(st, n):
    for i in st["issues"]:
        if str(i["number"]) == str(n):
            return i
    fail(f"GraphQL: Could not resolve to an issue or pull request with the number of {n}. (repository.issue)")


def read_body(flags):
    if "--body-file" not in flags:
        return None
    try:
        return Path(flags["--body-file"][0]).read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"open {flags['--body-file'][0]}: {exc.strerror}")


def issue_cmd(st, sub, pos, flags):
    if sub == "list":
        state = flags.get("--state", ["open"])[0].upper()
        want = [w.lower() for w in flags.get("--label", [])]
        terms = search_terms(flags.get("--search", [""])[0])
        rows = []
        for i in st["issues"]:
            hay = (i["title"] + "\n" + i["body"]).lower()
            if state != "ALL" and i["state"] != state:
                continue
            if any(w not in [n.lower() for n in i["labels"]] for w in want):
                continue
            if all(t in hay for t in terms):
                rows.append(shape(i, fields_of(flags)))
        emit(rows[:int(flags.get("--limit", ["30"])[0])], flags)
    elif sub == "view" and pos:
        emit(shape(find(st, pos[0]), fields_of(flags)), flags)
    elif sub == "create":
        title = flags.get("--title", [""])[0]
        body = read_body(flags)
        if not title or body is None:
            fail("fake gh: --title and --body-file are required (prompts are disabled)")
        have = {x["name"].lower(): x["name"] for x in st["labels"]}
        labels = []
        for lab in flags.get("--label", []):
            if lab.lower() not in have:
                fail(f"could not add label: '{lab}' not found")
            labels.append(have[lab.lower()])
        n = max([i["number"] for i in st["issues"]] + [0]) + 1
        st["issues"].append(_issue(n, title, body, labels=labels))
        print(URL.format(n))
    elif sub == "comment" and pos:
        issue = find(st, pos[0])
        st["comments"].append({"issue": issue["number"], "body": read_body(flags) or ""})
        print(URL.format(issue["number"]) + "#issuecomment-1")
    elif sub == "edit" and pos:
        issue = find(st, pos[0])
        st["edits"].append({"issue": issue["number"], "flags": flags})
        for lab in ",".join(flags.get("--add-label", [])).split(","):
            if lab and lab not in issue["labels"]:
                issue["labels"].append(lab)
        for lab in ",".join(flags.get("--remove-label", [])).split(","):
            if lab in issue["labels"]:
                issue["labels"].remove(lab)
        print(URL.format(issue["number"]))
    else:
        return False
    return True


def main(argv):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / "calls.log", "a", encoding="utf-8") as log:
        log.write(json.dumps({"argv": argv, "cwd": os.getcwd(),
                              "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z")}) + "\n")
    st = load(path)
    cmd, sub = (argv + ["", ""])[:2]
    pos, flags = parse(argv[2:])
    ok = False
    if cmd == "issue":
        ok = issue_cmd(st, sub, pos, flags)
    elif cmd == "label" and sub == "list":
        want = fields_of(flags)
        rows = [{k: x.get(k) for k in want} for x in st["labels"]]
        emit(rows[:int(flags.get("--limit", ["30"])[0])], flags)
        ok = True
    elif cmd == "api" and (pos[:1] == ["user"] or argv[1:2] == ["user"]):
        print(st["login"] if flags.get("--jq") == [".login"] else json.dumps({"login": st["login"]}))
        ok = True
    elif cmd == "auth" and sub == "status":
        print("github.com\n  Logged in to github.com account octocat (fake)")
        ok = True
    elif cmd == "pr":
        fail("fake gh: not supported")
    if not ok:
        fail("fake gh: unsupported command: " + " ".join(argv))
    save(path, st)


if __name__ == "__main__":
    main(sys.argv[1:])
