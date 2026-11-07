import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import belay as b  # noqa: E402


def write(text: str) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / ".belay.toml"
    p.write_text(text, encoding="utf-8")
    return p


class ConfigHardeningTests(unittest.TestCase):
    def test_paths_stripped_and_validated(self):
        cfg, _ = b.load_config(write('high_risk_paths = ["  src/auth/**  "]\nowner = "  bob "'))
        self.assertEqual(cfg.high_risk_paths, ("src/auth/**",))
        self.assertEqual(cfg.owner, "bob")
        cfg, _ = b.load_config(write('owner = "   "'))
        self.assertEqual(cfg.owner, "")
        for bad in ('[""]', '["  "]', '["src/[ab]/**"]'):
            with self.assertRaises(b.BelayRefusal) as cm:
                b.load_config(write(f"high_risk_paths = {bad}"))
            self.assertEqual(cm.exception.code, 2)
        with self.assertRaises(b.BelayRefusal) as cm:
            b.load_config(write('high_risk_paths = ["a[b]"]'))
        self.assertIn("character classes", cm.exception.reason)


class ConfigTests(unittest.TestCase):
    def test_defaults_when_file_missing(self):
        for path in (None, Path(tempfile.mkdtemp()) / "nope.toml"):
            cfg, warns = b.load_config(path)
            self.assertEqual(warns, [])
            self.assertEqual(cfg.tracker, "github")
            self.assertEqual(cfg.base_branch, "main")
            self.assertEqual(cfg.idle_minutes, 60)
            self.assertEqual(cfg.high_risk_paths, ())
            self.assertEqual(cfg.test_command, "")
            self.assertEqual(cfg.owner, "")
            self.assertIs(cfg.pr_draft, True)
            self.assertEqual(cfg.active_minutes, 90)
            self.assertEqual(cfg.attempts_per_stage, 3)
            self.assertEqual(cfg.github_repo, "")
            self.assertEqual(cfg.jira_site, "")
            self.assertEqual(cfg.jira_project, "")
            self.assertEqual(cfg.labels, {n: f"belay:{n}" for n in
                             ("ready", "working", "waiting", "review", "failed", "stop", "approved")})

    def test_values_override_defaults(self):
        p = write('''
tracker = "jira"
base_branch = "develop"
idle_minutes = 15
high_risk_paths = ["src/auth/**"]
test_command = "pytest"
owner = "bob"
pr_draft = false
[github]
repo = "o/n"
[jira]
site = "x.atlassian.net"
project = "PROJ"
[labels]
ready = "go"
[budget]
active_minutes = 10
attempts_per_stage = 5
''')
        cfg, warns = b.load_config(p)
        self.assertEqual(warns, [])
        self.assertEqual(cfg.tracker, "jira")
        self.assertEqual(cfg.base_branch, "develop")
        self.assertEqual(cfg.idle_minutes, 15)
        self.assertEqual(cfg.high_risk_paths, ("src/auth/**",))
        self.assertEqual(cfg.test_command, "pytest")
        self.assertEqual(cfg.owner, "bob")
        self.assertIs(cfg.pr_draft, False)
        self.assertEqual((cfg.github_repo, cfg.jira_site, cfg.jira_project), ("o/n", "x.atlassian.net", "PROJ"))
        self.assertEqual(cfg.labels["ready"], "go")
        self.assertEqual(cfg.labels["stop"], "belay:stop")
        self.assertEqual((cfg.active_minutes, cfg.attempts_per_stage), (10, 5))

    def test_unknown_key_warns_not_crashes(self):
        cfg, warns = b.load_config(write('bogus = 1\n[budget]\nextra = 2\n[labels]\nnope = "x"\n'))
        self.assertEqual(cfg.tracker, "github")
        self.assertTrue(any("bogus" in w for w in warns))
        self.assertTrue(any("extra" in w for w in warns))
        self.assertTrue(any("nope" in w for w in warns))

    def test_invalid_toml_raises_refusal_code_2(self):
        with self.assertRaises(b.BelayRefusal) as cm:
            b.load_config(write("this is = = not toml"))
        self.assertEqual(cm.exception.code, 2)
        self.assertTrue(cm.exception.reason)

    def test_negative_budget_raises_refusal_code_2(self):
        for text in ("[budget]\nactive_minutes = -1\n", "[budget]\nattempts_per_stage = 0\n",
                     "idle_minutes = -5\n"):
            with self.assertRaises(b.BelayRefusal) as cm:
                b.load_config(write(text))
            self.assertEqual(cm.exception.code, 2)

    def test_tracker_must_be_github_or_jira(self):
        with self.assertRaises(b.BelayRefusal) as cm:
            b.load_config(write('tracker = "linear"\n'))
        self.assertEqual(cm.exception.code, 2)

    def test_bom_prefixed_config_loads(self):
        d = Path(tempfile.mkdtemp())
        f = d / ".belay.toml"
        f.write_bytes(b"\xef\xbb\xbf" + b'base_branch = "dev"\n')
        cfg, _ = b.load_config(f)
        self.assertEqual(cfg.base_branch, "dev")

    def test_empty_label_override_refused(self):
        with self.assertRaises(b.BelayRefusal) as cm:
            b.load_config(write('[labels]\nready = ""\n'))
        self.assertEqual(cm.exception.code, 2)

    def test_wrong_value_types_refused(self):
        for text in ('idle_minutes = "5"\n', "pr_draft = 1\n", 'github = "x"\n',
                     "idle_minutes = true\n"):
            with self.assertRaises(b.BelayRefusal) as cm:
                b.load_config(write(text))
            self.assertEqual(cm.exception.code, 2, text)

    def test_default_labels_not_shared_and_frozen(self):
        c1, _ = b.load_config(None)
        self.assertEqual(c1.labels["ready"], "belay:ready")
        self.assertEqual(b.Config().labels["stop"], "belay:stop")
        with self.assertRaises(Exception):
            c1.tracker = "jira"


class RunIdTests(unittest.TestCase):
    def test_run_id_sanitises_issue(self):
        now = datetime(2026, 11, 7, 9, 5, 3, tzinfo=timezone.utc)
        self.assertEqual(b.make_run_id("42", now), "20261107T090503Z-42")
        self.assertEqual(b.make_run_id("PROJ-12/x y", now), "20261107T090503Z-PROJ-12_x_y")
        self.assertEqual(b.make_run_id("7", datetime(2026, 1, 2, 3, 4, 5)), "20260102T030405Z-7")
        rid = b.make_run_id("a/b c!", now)
        self.assertRegex(rid, r"^20261107T090503Z-[A-Za-z0-9_-]+$")
        self.assertNotIn("/", rid)


if __name__ == "__main__":
    unittest.main()
