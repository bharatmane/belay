#!/usr/bin/env python3
"""belay helper: deterministic checks for the belay Claude Code plugin.

Python 3.11+, standard library only. Sections are appended by later tasks.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Sequence

# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class BelayRefusal(Exception):
    """A deliberate refusal; the CLI prints `reason` and exits with `code`."""

    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LABEL_NAMES = ("ready", "working", "waiting", "review", "failed", "stop", "approved")
_TOP_KEYS = {"tracker", "base_branch", "idle_minutes", "high_risk_paths", "test_command",
             "owner", "pr_draft", "github", "jira", "labels", "budget"}
_SECTION_KEYS = {"github": {"repo"}, "jira": {"site", "project"},
                 "labels": set(LABEL_NAMES), "budget": {"active_minutes", "attempts_per_stage"}}


@dataclass(frozen=True)
class Config:
    tracker: str = "github"
    base_branch: str = "main"
    idle_minutes: int = 60
    high_risk_paths: tuple[str, ...] = ()
    test_command: str = ""
    owner: str = ""
    pr_draft: bool = True
    labels: dict[str, str] = field(default_factory=lambda: {n: f"belay:{n}" for n in LABEL_NAMES})
    active_minutes: int = 90
    attempts_per_stage: int = 3
    github_repo: str = ""
    jira_site: str = ""
    jira_project: str = ""


def _bad(msg: str) -> BelayRefusal:
    return BelayRefusal(2, f"invalid config: {msg}")


def _typed(data: dict, key: str, typ: type, default, where: str = ""):
    if key not in data:
        return default
    v = data[key]
    if typ is int and isinstance(v, bool) or not isinstance(v, typ):
        raise _bad(f"{where}{key} must be {typ.__name__}")
    return v


def _section(data: dict, name: str) -> dict:
    v = data.get(name, {})
    if not isinstance(v, dict):
        raise _bad(f"[{name}] must be a table")
    return v


def load_config(path: Path | None) -> tuple[Config, list[str]]:
    """Load .belay.toml. None or a missing file yields defaults. Returns (config, warnings)."""
    data: dict = {}
    if path is not None and Path(path).is_file():
        try:
            data = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as e:
            raise BelayRefusal(2, f"cannot read config {path}: {e}") from None

    warnings: list[str] = []
    for k in sorted(set(data) - _TOP_KEYS):
        warnings.append(f"unknown config key: {k}")
    for sec, allowed in _SECTION_KEYS.items():
        for k in sorted(set(_section(data, sec)) - allowed):
            warnings.append(f"unknown config key: {sec}.{k}")

    tracker = _typed(data, "tracker", str, "github")
    if tracker not in ("github", "jira"):
        raise _bad('tracker must be "github" or "jira"')
    idle = _typed(data, "idle_minutes", int, 60)
    if idle < 0:
        raise _bad("idle_minutes must be >= 0")
    paths = _typed(data, "high_risk_paths", list, [])
    if not all(isinstance(p, str) for p in paths):
        raise _bad("high_risk_paths must be a list of strings")

    budget = _section(data, "budget")
    active = _typed(budget, "active_minutes", int, 90, "budget.")
    attempts = _typed(budget, "attempts_per_stage", int, 3, "budget.")
    if active <= 0:
        raise _bad("budget.active_minutes must be > 0")
    if attempts <= 0:
        raise _bad("budget.attempts_per_stage must be > 0")

    labels = {n: f"belay:{n}" for n in LABEL_NAMES}
    label_sec = _section(data, "labels")
    for n in labels:
        labels[n] = _typed(label_sec, n, str, labels[n], "labels.")
        if not labels[n].strip():
            raise _bad(f"labels.{n} must not be empty")

    gh, jira = _section(data, "github"), _section(data, "jira")
    cfg = Config(
        tracker=tracker,
        base_branch=_typed(data, "base_branch", str, "main"),
        idle_minutes=idle,
        high_risk_paths=tuple(paths),
        test_command=_typed(data, "test_command", str, ""),
        owner=_typed(data, "owner", str, ""),
        pr_draft=_typed(data, "pr_draft", bool, True),
        labels=labels,
        active_minutes=active,
        attempts_per_stage=attempts,
        github_repo=_typed(gh, "repo", str, "", "github."),
        jira_site=_typed(jira, "site", str, "", "jira."),
        jira_project=_typed(jira, "project", str, "", "jira."),
    )
    return cfg, warnings


# ---------------------------------------------------------------------------
# Run id
# ---------------------------------------------------------------------------


def make_run_id(issue: str, now: datetime) -> str:
    """`YYYYMMDDTHHMMSSZ-<issue>` with the issue sanitised to [A-Za-z0-9_-].

    A naive `now` is treated as UTC.
    """
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(issue))
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{safe}"


# ---------------------------------------------------------------------------
# Run store, claim, stop switch, stale recovery
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    """UTC ISO-8601 with a trailing Z. A naive datetime is treated as UTC."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _replace_with_retry(src: Path, dst: Path) -> None:
    """os.replace, retrying briefly: Windows raises PermissionError while another handle is open."""
    for attempt in range(50):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


_RUN_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


class RunStore:
    """Run records as `<root>/.belay/runs/<run_id>.json`.

    Corrupt records: `load` raises BelayRefusal(2) naming the file. `list_runs` does NOT skip
    corrupt files (a skipped record could hide a working run and allow a double claim); it
    raises the same refusal, so the user must repair or delete the named file. The file is
    never modified or deleted by belay.
    """

    LOCK_TIMEOUT = 5.0

    def __init__(self, root: Path):
        self.root = Path(root)
        self.base = self.root / ".belay"
        self.runs_dir = self.base / "runs"
        self.lock_path = self.base / "lock"

    def _path(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            raise BelayRefusal(2, f"invalid run id {run_id!r}: only letters, digits, _ and - allowed")
        return self.runs_dir / f"{run_id}.json"

    def create(self, issue: str, tracker: str, now: datetime) -> dict:
        ts = _iso(now)
        run = {
            "run_id": make_run_id(issue, now), "issue": str(issue), "tracker": tracker,
            "state": "working", "stage": "start", "started_at": ts, "heartbeat": ts,
            "last_tick": ts, "active_seconds": 0, "attempts": {}, "evidence": [],
            "history": [], "overrides": [], "extension_minutes": 0,
        }
        self.save(run)
        return run

    def load(self, run_id: str) -> dict:
        return self._read(self._path(run_id))

    def _read(self, path: Path) -> dict:
        text = None
        for attempt in range(50):
            try:
                text = path.read_text(encoding="utf-8")
                break
            except FileNotFoundError:
                raise BelayRefusal(2, f"run record not found: {path}") from None
            except UnicodeDecodeError as e:
                raise BelayRefusal(2, f"corrupt run record {path}: {e}; repair or delete this file") from None
            except OSError as e:
                if attempt == 49:
                    raise BelayRefusal(2, f"could not read {path}: {e}") from None
                time.sleep(0.02)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise BelayRefusal(2, f"corrupt run record {path}: {e}; repair or delete this file") from None
        if not isinstance(data, dict) or not isinstance(data.get("run_id"), str):
            raise BelayRefusal(2, f"corrupt run record {path}: not a run object; repair or delete this file")
        return data

    def save(self, run: dict) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        dst = self._path(run["run_id"])
        tmp = self.runs_dir / f".{dst.name}.{uuid.uuid4().hex}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(run, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            _replace_with_retry(tmp, dst)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()

    def list_runs(self) -> list[dict]:
        """All run records sorted by run_id. Raises BelayRefusal(2) on any corrupt record."""
        if not self.runs_dir.is_dir():
            return []
        return [self._read(p) for p in sorted(self.runs_dir.glob("*.json"))]

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        """Exclusive-create lock file; retries up to LOCK_TIMEOUT seconds, then refuses (code 2).

        A lock left by a crashed process is never stolen automatically; the refusal names it.
        Not reentrant: never call a function that takes this lock (claim, heartbeat, attempt,
        wait, resume) while already holding it, or it will time out.
        """
        self.base.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.LOCK_TIMEOUT
        while True:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except (FileExistsError, PermissionError):
                if time.monotonic() >= deadline:
                    raise BelayRefusal(
                        2, f"could not acquire lock {self.lock_path} within {self.LOCK_TIMEOUT:g}s; "
                           "if no belay process is running, delete that file") from None
                time.sleep(0.01)
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            fd = -1
            yield
        finally:
            if fd >= 0:
                os.close(fd)
            with contextlib.suppress(OSError):
                self.lock_path.unlink()


def check_stop(root: Path, cfg: Config, labels: Sequence[str] = ()) -> None:
    """Raise BelayRefusal(3) if `<root>/.belay/STOP` exists or the stop label is present."""
    if (Path(root) / ".belay" / "STOP").exists():
        raise BelayRefusal(3, "stop switch: .belay/STOP exists; delete it to resume")
    if cfg.labels["stop"] in labels:
        raise BelayRefusal(3, f"stop switch: issue has label {cfg.labels['stop']!r}")


def claim(store: RunStore, cfg: Config, issue: str, now: datetime,
          labels: Sequence[str] = ()) -> dict:
    """Claim `issue` for a run. Only a `working` run blocks (code 5); a stale one is recovered.

    Stale = heartbeat older than cfg.idle_minutes. Same issue: resumed unchanged. Other issue:
    marked `failed` with history note `stale`, then a new run is created.
    """
    check_stop(store.root, cfg, labels)
    now = _parse_iso(_iso(now))  # normalise: naive -> UTC, aware -> converted to UTC
    issue = str(issue)
    with store.lock():
        stale: list[dict] = []
        for run in store.list_runs():
            if run.get("state") != "working":
                continue
            try:
                age = now - _parse_iso(run["heartbeat"])
            except (KeyError, TypeError, ValueError):
                raise BelayRefusal(
                    2, f"corrupt run record {store._path(run['run_id'])}: bad heartbeat") from None
            if age <= timedelta(minutes=cfg.idle_minutes):
                raise BelayRefusal(
                    5, f"run {run['run_id']} (issue {run.get('issue')}) is already working")
            stale.append(run)
        resumed = None
        for run in stale:
            if resumed is None and run.get("issue") == issue:
                resumed = run
                continue
            _transition(run, "failed", now, "stale")
            store.save(run)
        return resumed if resumed is not None else store.create(issue, cfg.tracker, now)


# ---------------------------------------------------------------------------
# Active time, budget, attempts, wait and resume
# ---------------------------------------------------------------------------


def _load_for_update(store: RunStore, run_id: str) -> dict:
    """Load a run inside store.lock(); an absent record is code 6 (corrupt stays code 2)."""
    if not store._path(run_id).exists():
        raise BelayRefusal(6, f"run not found: {run_id}")
    return store.load(run_id)


def _num(run: dict, field: str, default=None) -> float:
    v = run.get(field, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise BelayRefusal(2, f"corrupt run record {run.get('run_id')}: {field} is not a number")
    return v


def _credit(cfg: Config, run: dict, now: datetime) -> None:
    """Credit working time since last_tick (capped at idle_minutes, never negative); move ticks."""
    if run.get("state") == "working":
        try:
            last = _parse_iso(run["last_tick"])
        except (KeyError, TypeError, ValueError):
            raise BelayRefusal(2, f"corrupt run record {run.get('run_id')}: bad last_tick") from None
        gap = (now - last).total_seconds()
        run["active_seconds"] = _num(run, "active_seconds", 0) + min(max(gap, 0), cfg.idle_minutes * 60)
    run["last_tick"] = run["heartbeat"] = _iso(now)


def _transition(run: dict, to: str, now: datetime, note: str) -> None:
    run.setdefault("history", []).append(
        {"from": run["state"], "to": to, "at": _iso(now), "note": note})
    run["state"] = to


def budget(cfg: Config, run: dict, now: datetime) -> dict:
    limit = cfg.active_minutes + _num(run, "extension_minutes", 0)
    active = _num(run, "active_seconds", 0) / 60
    return {"active_minutes": active, "limit_minutes": limit,
            "remaining_minutes": max(limit - active, 0),
            "attempts": dict(run.get("attempts", {})), "spent": active >= limit}


def heartbeat(store: RunStore, cfg: Config, run_id: str, now: datetime) -> dict:
    now = _parse_iso(_iso(now))
    with store.lock():
        run = _load_for_update(store, run_id)
        _credit(cfg, run, now)
        store.save(run)
    bd = budget(cfg, run, now)
    if bd["spent"]:
        raise BelayRefusal(4, f"budget spent: {bd['active_minutes']:.1f} of {bd['limit_minutes']} "
                              f"active minutes used for run {run_id}")
    return run


def attempt(store: RunStore, cfg: Config, run_id: str, stage: str, now: datetime) -> dict:
    if not isinstance(stage, str) or not stage.strip():
        raise BelayRefusal(2, "stage must be a non-empty string")
    with store.lock():
        run = _load_for_update(store, run_id)
        if run.get("state") != "working":
            raise BelayRefusal(6, f"cannot attempt: run {run_id} is {run.get('state')}, not working")
        attempts = run.setdefault("attempts", {})
        if attempts.get(stage, 0) >= cfg.attempts_per_stage:
            raise BelayRefusal(4, f"attempts spent for stage {stage!r}: "
                                  f"{cfg.attempts_per_stage} allowed")
        attempts[stage] = attempts.get(stage, 0) + 1
        store.save(run)
        return run


def wait(store: RunStore, cfg: Config, run_id: str, reason: str, now: datetime) -> dict:
    now = _parse_iso(_iso(now))
    with store.lock():
        run = _load_for_update(store, run_id)
        if run.get("state") != "working":
            raise BelayRefusal(6, f"cannot wait: run {run_id} is {run.get('state')}, not working")
        _credit(cfg, run, now)
        _transition(run, "waiting", now, reason)
        store.save(run)
        return run


def resume(store: RunStore, cfg: Config, run_id: str, now: datetime) -> dict:
    check_stop(store.root, cfg)
    now = _parse_iso(_iso(now))
    with store.lock():
        run = _load_for_update(store, run_id)
        if run.get("state") != "waiting":
            raise BelayRefusal(6, f"cannot resume: run {run_id} is {run.get('state')}, not waiting")
        for other in store.list_runs():
            if other["run_id"] == run_id or other.get("state") != "working":
                continue
            try:
                age = now - _parse_iso(other["heartbeat"])
            except (KeyError, TypeError, ValueError):
                raise BelayRefusal(
                    2, f"corrupt run record {store._path(other['run_id'])}: bad heartbeat") from None
            if age <= timedelta(minutes=cfg.idle_minutes):
                raise BelayRefusal(
                    5, f"run {other['run_id']} (issue {other.get('issue')}) is already working")
        _credit(cfg, run, now)  # waiting: moves ticks only, credits nothing
        _transition(run, "working", now, "resumed")
        store.save(run)
        return run


# ---------------------------------------------------------------------------
# Evidence, hand-off, high-risk paths, owner override
# ---------------------------------------------------------------------------


def record_evidence(store: RunStore, run_id: str, *, command: str, exit_code: int, head: str,
                    stage: str, now: datetime) -> dict:
    """Append a test-run record (command, exit code, commit) to the run's evidence list."""
    if not isinstance(command, str) or not command.strip():
        raise BelayRefusal(2, "evidence command must be a non-empty string")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise BelayRefusal(2, "evidence exit_code must be an integer")
    if not isinstance(head, str) or not head.strip():
        raise BelayRefusal(2, "evidence head (commit) must be a non-empty string")
    now = _parse_iso(_iso(now))
    with store.lock():
        run = _load_for_update(store, run_id)
        ev = run.setdefault("evidence", [])
        if not isinstance(ev, list):
            raise BelayRefusal(2, f"corrupt run record {run_id}: evidence is not a list")
        ev.append({"command": command, "exit_code": exit_code, "head": head,
                   "stage": str(stage), "at": _iso(now)})
        store.save(run)
        return run


def hand_off(store: RunStore, cfg: Config, run_id: str, head: str, now: datetime) -> dict:
    """Move a working run to `review`. Needs passing evidence recorded for exactly `head`.

    Evidence counts only if its head equals `head`, exit_code == 0 and, when cfg.test_command is
    set, its command equals cfg.test_command. So a new commit, or a different command, needs
    fresh evidence (code 7). A run that is not `working` is code 6.
    """
    now = _parse_iso(_iso(now))
    with store.lock():
        run = _load_for_update(store, run_id)
        if run.get("state") != "working":
            raise BelayRefusal(6, f"cannot hand off: run {run_id} is {run.get('state')}, not working")
        ev = run.get("evidence", [])
        if not isinstance(ev, list):
            raise BelayRefusal(2, f"corrupt run record {run_id}: evidence is not a list")
        ok = any(isinstance(e, dict) and e.get("head") == head and e.get("exit_code") == 0
                 and not isinstance(e.get("exit_code"), bool)
                 and (not cfg.test_command or e.get("command") == cfg.test_command)
                 for e in ev)
        if not ok:
            want = f" with command {cfg.test_command!r}" if cfg.test_command else ""
            raise BelayRefusal(7, f"no passing evidence{want} for HEAD {head}; run the tests on "
                                  "this commit and record the result before handing off")
        _credit(cfg, run, now)
        _transition(run, "review", now, f"handed off at {head}")
        store.save(run)
        return run


def _norm_path(p: str) -> str:
    p = str(p).replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


_GLOB_CACHE: dict[str, re.Pattern] = {}


def _glob_regex(pattern: str) -> re.Pattern:
    pat = _norm_path(pattern)
    if pat.endswith("/"):
        pat += "**"
    rx = _GLOB_CACHE.get(pat)
    if rx is not None:
        return rx
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**", i):
            j = i + 2
            while j < len(pat) and pat[j] == "*":
                j += 1
            if pat.startswith("/", j) and (i == 0 or pat[i - 1] == "/"):
                out.append("(?:.*/)?")  # `**/` spans zero or more directories
                i = j + 1
            else:
                out.append(".*")
                i = j
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    rx = _GLOB_CACHE[pat] = re.compile("".join(out))
    return rx


def matches_glob(path: str, pattern: str) -> bool:
    """Glob match on `/`-normalised paths (backslash becomes `/`, leading `./` dropped).

    `**` spans directories (`**/x` also matches a top-level `x`); `*` and `?` never cross `/`;
    a trailing `/` means the whole directory. Own implementation, not fnmatch (its `*` crosses
    `/`). Matching is case-SENSITIVE on every platform, so a risky pattern is never silently
    skipped by case; write patterns in the repo's own case.
    """
    if not pattern:
        return False
    return _glob_regex(pattern).fullmatch(_norm_path(path)) is not None


def check_paths(cfg: Config, changed: Sequence[str], labels: Sequence[str]) -> None:
    """Refuse (code 8) when a changed path matches cfg.high_risk_paths and the issue lacks the approved label."""
    if cfg.labels["approved"] in labels:
        return
    hits = [_norm_path(p) for p in changed
            if any(matches_glob(p, pat) for pat in cfg.high_risk_paths)]
    if hits:
        raise BelayRefusal(8, f"high-risk paths changed without label {cfg.labels['approved']!r}: "
                              + ", ".join(hits))


def override(store: RunStore, cfg: Config, run_id: str, *, who: str, kind: str, reason: str,
             minutes: int = 0, now: datetime) -> dict:
    """Owner-only: `extend` adds budget minutes, `reopen` moves a `failed` run back to `working`."""
    if not cfg.owner:
        raise BelayRefusal(9, "no owner configured: set `owner` in .belay.toml to allow overrides")
    if who != cfg.owner:
        raise BelayRefusal(9, f"{who!r} is not the owner ({cfg.owner!r}); only the owner may override")
    if kind not in ("extend", "reopen"):
        raise BelayRefusal(2, f"unknown override kind {kind!r}: use extend or reopen")
    if kind == "extend" and (isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0):
        raise BelayRefusal(2, "extend requires minutes > 0")
    now = _parse_iso(_iso(now))
    with store.lock():
        run = _load_for_update(store, run_id)
        if kind == "reopen":
            if run.get("state") != "failed":
                raise BelayRefusal(6, f"cannot reopen: run {run_id} is {run.get('state')}, not failed")
            _credit(cfg, run, now)  # failed: moves ticks only, so the downtime is not credited
            _transition(run, "working", now, f"reopened by {who}: {reason}")
        else:
            run["extension_minutes"] = _num(run, "extension_minutes", 0) + minutes
        ovs = run.setdefault("overrides", [])
        if not isinstance(ovs, list):
            raise BelayRefusal(2, f"corrupt run record {run_id}: overrides is not a list")
        ovs.append({"who": who, "kind": kind, "reason": reason, "at": _iso(now)})
        store.save(run)
        return run
