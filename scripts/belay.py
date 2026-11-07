#!/usr/bin/env python3
"""belay helper: deterministic checks for the belay Claude Code plugin.

Python 3.11+, standard library only. Sections are appended by later tasks.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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
    labels: dict[str, str] = None  # type: ignore[assignment]
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
            data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
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
    for n, v in _section(data, "labels").items():
        if n in labels:
            labels[n] = _typed(_section(data, "labels"), n, str, labels[n], "labels.")

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
    """`YYYYMMDDTHHMMSSZ-<issue>` with the issue sanitised to [A-Za-z0-9_-]."""
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(issue))
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{safe}"
