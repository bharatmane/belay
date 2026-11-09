import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import validate_repo as v  # noqa: E402

sys.path.insert(0, str(ROOT / "evals" / "harness"))
import fake_gh  # noqa: E402
import pr_fixtures  # noqa: E402

SCENARIOS = set(fake_gh.SCENARIOS) | set(pr_fixtures.SCENARIOS) | set(fake_gh.DISPATCH)


def eval_files():
    return sorted((ROOT / "evals").glob("*.json"))


class EvalDefinitions(unittest.TestCase):
    def test_issue_create_evals_exist(self):
        self.assertTrue((ROOT / "evals" / "issue-create.json").is_file())

    def test_every_eval_file_is_well_formed(self):
        for path in eval_files():
            with self.subTest(path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["skill"], path.stem)
                self.assertGreaterEqual(len(data["should_trigger"]), 3)
                self.assertGreaterEqual(len(data["should_not_trigger"]), 3)
                self.assertTrue(all(isinstance(p, str) and p for p in data["should_trigger"] + data["should_not_trigger"]))
                self.assertGreaterEqual(len(data["behaviour"]), 3)
                for task in data["behaviour"]:
                    self.assertIn(task["scenario"], SCENARIOS)
                    self.assertTrue(task["id"] and task["prompt"])
                    self.assertGreaterEqual(len(task["expect"]), 3)


class SkillStructure(unittest.TestCase):
    def test_every_skill_has_evals_and_passes_validation(self):
        for skill in sorted(p for p in (ROOT / "skills").glob("*") if p.is_dir()):
            with self.subTest(skill.name):
                evals = ROOT / "evals" / f"{skill.name}.json"
                self.assertTrue(evals.is_file(), f"missing {evals}")
                data = json.loads(evals.read_text(encoding="utf-8"))
                self.assertGreaterEqual(len(data["should_trigger"]), 3)
                self.assertGreaterEqual(len(data["should_not_trigger"]), 3)
                self.assertEqual(v.validate_skill(skill), [])

    def test_shipped_skills_are_present(self):
        names = {p.name for p in (ROOT / "skills").glob("*") if p.is_dir()}
        self.assertTrue({"issue-create", "pr-review", "dispatch"} <= names)

    def test_dispatch_is_explicit_invocation_only(self):
        skill = ROOT / "skills" / "dispatch"
        fm = v.parse_frontmatter((skill / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(fm.get("disable-model-invocation"), "true")
        self.assertEqual(fm.get("description"), "Use only when the user explicitly asks to run the "
                         "Belay loop, pick up the next ready issue, or resume a Belay run for a named issue.")
        for ref in ("stages.md", "resume-and-limits.md"):
            self.assertTrue((skill / "references" / ref).is_file(), ref)

    def test_dispatch_uses_only_real_helper_commands(self):
        import re
        sys.path.insert(0, str(ROOT / "scripts"))
        import belay  # noqa: E402
        parser = belay._build_parser()
        sub = next(a for a in parser._actions if a.dest == "command")
        commands = set(sub.choices)
        texts = [p.read_text(encoding="utf-8") for p in (ROOT / "skills" / "dispatch").rglob("*.md")]
        used = {m for t in texts for m in re.findall(r"\bB ([a-z][a-z-]+)", t)}
        self.assertTrue(used, "no helper calls found")
        self.assertEqual(used - commands, set())
        flags = {f for a in parser._actions if a.dest == "command" for p in a.choices.values()
                 for act in p._actions for f in act.option_strings}
        used_flags = {f for t in texts for line in t.splitlines() if re.search(r"\bB [a-z]", line)
                      for f in re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", line.split("|")[-1])}
        self.assertEqual(used_flags - flags - {"--root", "--config"}, set())


if __name__ == "__main__":
    unittest.main()
