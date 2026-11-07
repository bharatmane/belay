import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent / "evals" / "harness"
FAKE = HARNESS / "fake_gh.py"


class FakeGhCase(unittest.TestCase):
    scenario = "default"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.state = self.dir / ".fake-gh" / "state.json"
        self.env = dict(os.environ, BELAY_FAKE_GH_STATE=str(self.state), BELAY_FAKE_GH_SCENARIO=self.scenario)

    def gh(self, *args, expect=0):
        p = subprocess.run([sys.executable, str(FAKE), *args], cwd=self.dir, env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, expect, p.stdout + p.stderr)
        return p

    def js(self, *args):
        return json.loads(self.gh(*args).stdout)


class ListAndSearch(FakeGhCase):
    def test_list_default_is_open_only_with_json_fields(self):
        rows = self.js("issue", "list", "-R", "o/r", "--json", "number,title,state")
        self.assertTrue(rows)
        self.assertEqual(set(rows[0]), {"number", "title", "state"})
        self.assertTrue(all(r["state"] == "OPEN" for r in rows))

    def test_state_all_includes_closed(self):
        states = {r["state"] for r in self.js("issue", "list", "--state", "all", "--json", "state")}
        self.assertEqual(states, {"OPEN", "CLOSED"})

    def test_search_matches_title_or_body_ignoring_qualifiers(self):
        rows = self.js("issue", "list", "--state", "all", "--search", "EXPIRED in:title,body",
                       "--json", "number,title")
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.js("issue", "list", "-S", "zzzznothing", "--json", "number"), [])

    def test_label_filter_and_limit(self):
        rows = self.js("issue", "list", "--state", "all", "--label", "bug", "--json", "number,labels")
        self.assertTrue(rows)
        self.assertTrue(all("bug" in [x["name"] for x in r["labels"]] for r in rows))
        self.assertEqual(len(self.js("issue", "list", "--state", "all", "--limit", "1", "--json", "number")), 1)

    def test_view_and_missing(self):
        n = self.js("issue", "list", "--json", "number")[0]["number"]
        v = self.js("issue", "view", str(n), "--json", "number,title,body,labels,state,author,url,updatedAt")
        self.assertEqual(v["number"], n)
        self.assertIn("login", v["author"])
        p = self.gh("issue", "view", "9999", "--json", "number", expect=1)
        self.assertIn("Could not resolve to an issue", p.stderr)


class Create(FakeGhCase):
    def test_create_records_body_prints_url_and_logs(self):
        body = self.dir / "b.md"
        body.write_text("Given x\nWhen y\nThen z\n", encoding="utf-8")
        p = self.gh("issue", "create", "-R", "o/r", "--title", "New thing", "--body-file", str(body),
                    "--label", "bug", "--label", "enhancement")
        url = p.stdout.strip()
        self.assertTrue(url.startswith("https://github.com/example/fake/issues/"))
        n = url.rsplit("/", 1)[1]
        v = self.js("issue", "view", n, "--json", "title,body,labels")
        self.assertEqual(v["title"], "New thing")
        self.assertIn("Given x", v["body"])
        self.assertEqual({x["name"] for x in v["labels"]}, {"bug", "enhancement"})
        calls = (self.state.parent / "calls.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), 2)
        rec = json.loads(calls[0])
        self.assertEqual(set(rec) >= {"argv", "cwd", "timestamp"}, True)
        self.assertEqual(rec["argv"][:2], ["issue", "create"])

    def test_create_with_unknown_label_fails(self):
        body = self.dir / "b.md"
        body.write_text("x", encoding="utf-8")
        p = self.gh("issue", "create", "--title", "t", "--body-file", str(body), "--label", "nope", expect=1)
        self.assertIn("nope", p.stderr)

    def test_create_requires_title_and_readable_body(self):
        self.gh("issue", "create", "--body-file", "x", expect=1)
        self.gh("issue", "create", "--title", "t", "--body-file", "missing.md", expect=1)

    def test_edit_comment_recorded(self):
        n = self.js("issue", "list", "--json", "number")[0]["number"]
        c = self.dir / "c.md"
        c.write_text("hello", encoding="utf-8")
        self.gh("issue", "comment", str(n), "--body-file", str(c))
        self.gh("issue", "edit", str(n), "--add-label", "bug")
        st = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(st["comments"][0]["body"], "hello")


class Misc(FakeGhCase):
    def test_label_list(self):
        names = [x["name"] for x in self.js("label", "list", "--json", "name")]
        self.assertIn("bug", names)

    def test_api_user(self):
        self.assertEqual(self.gh("api", "user", "--jq", ".login").stdout.strip(), "octocat")
        self.assertEqual(json.loads(self.gh("api", "user").stdout)["login"], "octocat")

    def test_auth_status_ok_pr_unsupported_unknown_exit_1(self):
        self.gh("auth", "status")
        self.assertIn("not supported", self.gh("pr", "list", expect=1).stderr)
        p = self.gh("repo", "delete", expect=1)
        self.assertIn("fake gh: unsupported command: repo delete", p.stderr)


class Scenarios(unittest.TestCase):
    def run_scenario(self, name):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, BELAY_FAKE_GH_STATE=str(Path(d) / "s.json"), BELAY_FAKE_GH_SCENARIO=name)
            out = {}
            for key, args in (("issues", ["issue", "list", "--state", "all", "--json", "number,title"]),
                              ("labels", ["label", "list", "--json", "name"])):
                p = subprocess.run([sys.executable, str(FAKE), *args], cwd=d, env=env, capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                out[key] = json.loads(p.stdout)
            return out

    def test_empty(self):
        self.assertEqual(self.run_scenario("empty")["issues"], [])

    def test_has_duplicate(self):
        titles = [i["title"] for i in self.run_scenario("has-duplicate")["issues"]]
        self.assertIn("Login page returns 500 on empty password", titles)

    def test_no_labels_has_no_belay_labels(self):
        names = [x["name"] for x in self.run_scenario("no-labels")["labels"]]
        self.assertFalse([n for n in names if n.startswith("belay")])

    def test_default_has_belay_labels(self):
        names = [x["name"] for x in self.run_scenario("default")["labels"]]
        self.assertIn("belay:ready", names)


class NewScratch(unittest.TestCase):
    def test_creates_seeded_state(self):
        p = subprocess.run([sys.executable, str(HARNESS / "new_scratch.py"), "has-duplicate"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        scratch = Path(p.stdout.splitlines()[0].split(": ", 1)[1].strip())
        try:
            st = json.loads((scratch / ".fake-gh" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(st["scenario"], "has-duplicate")
            self.assertIn(str(HARNESS), p.stdout)
        finally:
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
