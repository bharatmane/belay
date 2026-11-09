"""Checks that the shared documents the skills link to stay complete and consistent."""
import contextlib
import io
import json
import re
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import belay as b  # noqa: E402
import validate_repo as v  # noqa: E402

SHARED = ROOT / "shared"
OPERATIONS = ("find_ready", "get_issue", "set_state", "comment", "read_new_comments",
              "create_issue", "link_pr")
EXIT_CODES = (0, 2, 3, 4, 5, 6, 7, 8, 9)
TEMPLATES = {
    "issue.md": {"goal", "context", "acceptance_criteria", "evidence", "out_of_scope",
                 "open_questions", "stop_and_ask", "risks"},
    "pr-body.md": {"issue_ref", "why", "what", "verification", "risks", "run_id"},
    "run-comment.md": {"run_id", "event", "state_label", "details"},
    "escape-hatch.md": {"run_id", "limit", "code", "head", "branch", "last_evidence",
                        "blocker", "failed_label"},
}
PLACEHOLDER_RE = re.compile(r"\{\{([a-z_]+)\}\}")
DOC_ROW_RE = re.compile(r"^\|\s*`\{\{([a-z_]+)\}\}`\s*\|", re.M)


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def template_body(text: str) -> str:
    """The first fenced block after the `## Template` heading."""
    m = re.search(r"^## Template\s*$.*?^```markdown\n(.*?)^```\s*$", text, re.S | re.M)
    if not m:
        raise AssertionError("no ```markdown block under '## Template'")
    return m.group(1)


def section(text: str, op: str) -> str:
    m = re.search(rf"^### `{op}`\s*$(.*?)(?=^##+ |\Z)", text, re.S | re.M)
    if not m:
        raise AssertionError(f"no '### `{op}`' section")
    return m.group(1)


class ConfigDocTests(unittest.TestCase):
    def test_example_config_loads_without_warnings(self):
        path = ROOT / ".belay.toml.example"
        self.assertTrue(path.is_file(), "a missing file would load as defaults with no warnings")
        cfg, warnings = b.load_config(path)
        self.assertEqual(warnings, [])
        self.assertEqual(cfg.tracker, "github")

    def test_example_sets_every_key(self):
        data = tomllib.loads(read(".belay.toml.example"))
        self.assertEqual(set(data), b._TOP_KEYS)
        for sec, keys in b._SECTION_KEYS.items():
            self.assertEqual(set(data[sec]), keys, sec)

    def test_config_doc_covers_every_key(self):
        doc = read("shared/config.md")
        keys = [k for k in b._TOP_KEYS if k not in b._SECTION_KEYS]
        keys += [f"{s}.{k}" for s, ks in b._SECTION_KEYS.items() for k in ks]
        missing = [k for k in keys if f"`{k}`" not in doc]
        self.assertEqual(missing, [])


class AdapterDocTests(unittest.TestCase):
    def test_adapter_defines_all_seven_operations(self):
        doc = read("shared/adapters/github.md")
        for op in OPERATIONS:
            body = section(doc, op)
            self.assertRegex(body, r"```\w*\n[^`]*\bgh ", f"{op} has no gh command block")

    def test_adapter_records_the_fixed_decisions(self):
        doc = read("shared/adapters/github.md")
        for needle in ("GH_PROMPT_DISABLED=1", "--body-file", "--paginate", "gh label list",
                       "--add-label", "--remove-label", "gh api user", "Belay run <run_id>",
                       "<!-- belay:run=<run_id> -->", "--json"):
            self.assertIn(needle, doc)
        self.assertRegex(section(doc, "create_issue"), r"gh issue list[^\n]*--search")
        self.assertNotIn("--body ", doc.replace("--body-file", ""))

    def test_jira_doc_is_marked_planned(self):
        doc = read("shared/adapters/jira.md")
        self.assertIn("planned, not yet supported", doc.lower())
        self.assertIn("transitions", doc)


class SafetyDocTests(unittest.TestCase):
    def test_safety_doc_mentions_every_exit_code(self):
        doc = read("shared/safety.md")
        for code in EXIT_CODES:
            self.assertRegex(doc, rf"(?m)^\|\s*`?{code}`?\s*\|", f"exit code {code} has no row")

    def test_safety_doc_is_honest_about_enforcement(self):
        doc = read("shared/safety.md")
        for needle in ("Hook", "Helper", "Advisory", "gh api user", "self-asserted",
                       "advisory only", "fails open"):
            self.assertIn(needle, doc)


class TemplateTests(unittest.TestCase):
    def test_templates_exist_and_have_documented_placeholders(self):
        for name, expected in TEMPLATES.items():
            with self.subTest(template=name):
                text = read(f"shared/templates/{name}")
                used = set(PLACEHOLDER_RE.findall(template_body(text)))
                documented = set(DOC_ROW_RE.findall(text))
                self.assertEqual(used, expected)
                self.assertEqual(documented, expected)

    def test_tracker_comments_start_with_run_line_and_marker(self):
        for name in ("run-comment.md", "escape-hatch.md"):
            with self.subTest(template=name):
                lines = template_body(read(f"shared/templates/{name}")).splitlines()
                self.assertEqual(lines[:2], ["Belay run {{run_id}}", "<!-- belay:run={{run_id}} -->"])


class LinkTests(unittest.TestCase):
    def test_validate_repo_links_pass(self):
        self.assertEqual(v.validate_links(ROOT), [])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(v.main([str(ROOT)]), 0, out.getvalue())


class ReadmeTests(unittest.TestCase):
    def test_readme_install_command_matches_manifests(self):
        plugin = json.loads(read(".claude-plugin/plugin.json"))
        market = json.loads(read(".claude-plugin/marketplace.json"))
        readme = read("README.md")
        self.assertIn(f"/plugin install {plugin['name']}@{market['name']}", readme)
        self.assertIn("/plugin marketplace add bharatmane/belay", readme)

    def test_readme_names_every_skill(self):
        readme = read("README.md")
        for skill in sorted(p.name for p in (ROOT / "skills").iterdir() if p.is_dir()):
            with self.subTest(skill):
                self.assertIn(f"`belay:{skill}`", readme)


if __name__ == "__main__":
    unittest.main()
