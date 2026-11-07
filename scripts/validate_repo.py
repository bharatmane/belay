#!/usr/bin/env python3
"""Repository validator for belay: skills, manifests and Markdown links. Stdlib only."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE_RE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.S | re.M)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
SKIP_DIRS = {".git", ".superpowers", ".belay", "__pycache__", "node_modules"}
MAX_BODY_LINES = 500
MAX_DESC = 1024
MAX_NAME = 64


def parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing frontmatter")
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"bad frontmatter line: {line!r}")
        out[key.strip()] = value.strip()
    raise ValueError("unterminated frontmatter")


def _body(text: str) -> str:
    lines = text.lstrip("﻿").splitlines()
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1:])
    return ""


def validate_skill(skill_dir: Path) -> list[str]:
    skill_md = skill_dir / "SKILL.md"
    label = f"{skill_dir.name}/SKILL.md"
    if not skill_md.is_file():
        return [f"{label}: file not found"]
    text = skill_md.read_text(encoding="utf-8")
    try:
        fm = parse_frontmatter(text)
    except ValueError as exc:
        return [f"{label}: frontmatter error: {exc}"]
    errors: list[str] = []
    name = fm.get("name", "")
    if not name:
        errors.append(f"{label}: name is missing")
    elif not NAME_RE.match(name) or len(name) > MAX_NAME:
        errors.append(f"{label}: name {name!r} must be lowercase letters, digits and hyphens, at most {MAX_NAME} characters")
    elif name != skill_dir.name:
        errors.append(f"{label}: name {name!r} must match directory name {skill_dir.name!r}")
    desc = fm.get("description", "")
    if not desc:
        errors.append(f"{label}: description is missing")
    else:
        if len(desc) > MAX_DESC:
            errors.append(f"{label}: description is {len(desc)} characters, limit is {MAX_DESC}")
        if not (desc.startswith("Use when") or desc.startswith("Use only when")):
            errors.append(f"{label}: description must start with 'Use when' or 'Use only when'")
        low = desc.lower()
        if " then " in low or "step" in low or "workflow" in low:
            errors.append(f"{label}: description looks like a workflow summary (contains ' then ', 'step' or 'workflow'); say when to use the skill, not what it does")
    body_lines = len(_body(text).splitlines())
    if body_lines > MAX_BODY_LINES:
        errors.append(f"{label}: body is {body_lines} lines, limit is {MAX_BODY_LINES}")
    return errors


def _load_json(path: Path, errors: list[str]):
    if not path.is_file():
        errors.append(f"{path.name}: file not found")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{path.name}: invalid JSON: {exc}")
        return None


def validate_manifest(root: Path) -> list[str]:
    errors: list[str] = []
    cp = root / ".claude-plugin"
    plugin = _load_json(cp / "plugin.json", errors)
    market = _load_json(cp / "marketplace.json", errors)
    if plugin is None or market is None:
        return errors
    if plugin.get("name") != "belay":
        errors.append("plugin.json: name must be 'belay'")
    if not plugin.get("version"):
        errors.append("plugin.json: version is missing")
    if market.get("name") != "belay":
        errors.append("marketplace.json: name must be 'belay'")
    if not isinstance(market.get("owner"), dict) or not market["owner"].get("name"):
        errors.append("marketplace.json: owner.name is required")
    entries = market.get("plugins")
    if not isinstance(entries, list) or not entries:
        errors.append("marketplace.json: plugins must be a non-empty array")
        return errors
    entry = entries[0]
    if entry.get("name") != plugin.get("name"):
        errors.append("marketplace.json: plugin entry name must equal plugin.json name")
    if not entry.get("source"):
        errors.append("marketplace.json: plugin entry needs a source")
    if entry.get("version") != plugin.get("version"):
        errors.append(f"version mismatch: plugin.json {plugin.get('version')!r} vs marketplace.json {entry.get('version')!r}")
    return errors


def validate_hooks(root: Path) -> list[str]:
    """If hooks/hooks.json exists it must be JSON with the event map under a top-level "hooks"."""
    path = root / "hooks" / "hooks.json"
    if not path.is_file():
        return []
    errors: list[str] = []
    data = _load_json(path, errors)
    if data is not None and not (isinstance(data, dict) and isinstance(data.get("hooks"), dict)):
        errors.append("hooks.json: the event map must sit under a top-level \"hooks\" object")
    return errors


def validate_links(root: Path) -> list[str]:
    errors: list[str] = []
    for md in sorted(root.rglob("*.md")):
        rel_parts = md.relative_to(root).parts
        if any(p in SKIP_DIRS for p in rel_parts):
            continue
        text = INLINE_CODE_RE.sub("", FENCE_RE.sub("", md.read_text(encoding="utf-8")))
        for target in LINK_RE.findall(text):
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
                continue
            path_part = target.split("#", 1)[0].split("?", 1)[0]
            if not path_part:
                continue
            base = root if path_part.startswith("/") else md.parent
            if not (base / path_part.lstrip("/")).exists():
                errors.append(f"{md.relative_to(root).as_posix()}: broken link {target!r}")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path(__file__).resolve().parent.parent
    errors = validate_manifest(root)
    errors.extend(validate_hooks(root))
    skills = root / "skills"
    if skills.is_dir():
        for d in sorted(p for p in skills.iterdir() if p.is_dir()):
            errors.extend(validate_skill(d))
    errors.extend(validate_links(root))
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
