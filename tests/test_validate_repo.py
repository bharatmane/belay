import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import validate_repo as v  # noqa: E402

GOOD_DESC = "Use when a developer wants to file a well-formed issue."


def make_skill(root: Path, name="issue-create", desc=GOOD_DESC, body="Body.\n", raw=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    text = raw if raw is not None else f"---\nname: {name}\ndescription: {desc}\n---\n{body}"
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return d


def make_manifests(root: Path, plugin_version="0.1.0-dev", market_version="0.1.0-dev", name="belay"):
    cp = root / ".claude-plugin"
    cp.mkdir(parents=True, exist_ok=True)
    (cp / "plugin.json").write_text(json.dumps({"name": name, "version": plugin_version}), encoding="utf-8")
    (cp / "marketplace.json").write_text(json.dumps({
        "name": "belay", "owner": {"name": "x"},
        "plugins": [{"name": "belay", "source": "./", "version": market_version}]}), encoding="utf-8")


class SkillTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_valid_skill_passes(self):
        self.assertEqual(v.validate_skill(make_skill(self.root)), [])

    def test_description_must_start_with_use_when(self):
        errs = v.validate_skill(make_skill(self.root, desc="Creates issues for you."))
        self.assertTrue(any("Use when" in e for e in errs))

    def test_use_only_when_is_accepted(self):
        self.assertEqual(v.validate_skill(make_skill(self.root, desc="Use only when the user types /dispatch.")), [])

    def test_description_over_1024_fails(self):
        errs = v.validate_skill(make_skill(self.root, desc="Use when " + "x" * 1030))
        self.assertTrue(any("1024" in e for e in errs))

    def test_name_must_be_lowercase_hyphen(self):
        errs = v.validate_skill(make_skill(self.root, name="Bad_Name"))
        self.assertTrue(any("name" in e for e in errs))

    def test_body_over_500_lines_fails(self):
        errs = v.validate_skill(make_skill(self.root, body="line\n" * 501))
        self.assertTrue(any("500" in e for e in errs))

    def test_missing_frontmatter_reports_error(self):
        errs = v.validate_skill(make_skill(self.root, raw="no frontmatter here\n"))
        self.assertTrue(any("frontmatter" in e for e in errs))

    def test_workflow_summary_phrases_flagged(self):
        for desc in ("Use when filing, then reviewing.", "Use when you need each step done.",
                     "Use when running the workflow."):
            with self.subTest(desc=desc):
                errs = v.validate_skill(make_skill(self.root, desc=desc))
                self.assertTrue(any("workflow" in e.lower() or "summar" in e.lower() for e in errs))

    def test_parse_frontmatter(self):
        self.assertEqual(v.parse_frontmatter("---\na: b\nc: d: e\n---\nx"), {"a": "b", "c": "d: e"})
        with self.assertRaises(ValueError):
            v.parse_frontmatter("nothing")


class RepoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_valid_manifests_pass(self):
        make_manifests(self.root)
        self.assertEqual(v.validate_manifest(self.root), [])

    def test_manifest_versions_must_match(self):
        make_manifests(self.root, market_version="0.2.0")
        self.assertTrue(any("version" in e for e in v.validate_manifest(self.root)))

    def test_manifest_name_must_be_belay(self):
        make_manifests(self.root, name="other")
        self.assertTrue(any("name" in e for e in v.validate_manifest(self.root)))

    def test_missing_manifest_reported(self):
        self.assertTrue(v.validate_manifest(self.root))

    def test_broken_relative_link_reported(self):
        (self.root / "a.md").write_text("[ok](b.md) [bad](missing.md) [web](https://x.io) [anc](#top)", encoding="utf-8")
        (self.root / "b.md").write_text("hi", encoding="utf-8")
        errs = v.validate_links(self.root)
        self.assertEqual(len(errs), 1)
        self.assertIn("missing.md", errs[0])

    def test_main_ok_and_failure(self):
        make_manifests(self.root)
        self.assertEqual(v.main([str(self.root)]), 0)
        make_manifests(self.root, market_version="9")
        self.assertEqual(v.main([str(self.root)]), 1)


if __name__ == "__main__":
    unittest.main()
