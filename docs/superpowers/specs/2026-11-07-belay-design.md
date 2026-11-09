# Belay — design

Status: approved and implemented as v0.1.0-dev on branch feat/v1 (GitHub only). Goal 2 (Jira
parity) is deferred by plan decision: the Jira adapter is documented as unsupported.
Date: 2026-11-07. Owner: Bharat.

Items marked **(my call)** are decisions the design author made without the owner
having chosen them. They are listed again at the end so they can be checked quickly.

## 1. Intent

Coding agents can now take an issue and produce a pull request. The risk is not
that they cannot do the work; it is that they do it unattended, overrun, claim
success without proof, and sometimes merge their own changes.

**Belay is a family of reusable agent skills that takes work from an issue tracker
to a pull request safely.** The agent climbs; Belay is the rope. A human always
decides and always does the merge.

It works with **GitHub Issues** and **Jira**, and is distributed as an open-source
(MIT) plugin in the portable Agent Skills format (`SKILL.md` folders) so it can be
used from Claude Code and other tools that support the standard.

## 2. Why this and not something that already exists

Researched 2026-11-07. Existing projects cover pieces: issue-to-PR runners,
PR-review skills, Jira skills, handoff notes. What none of them combine:

- an **enforced** stop switch and **active-time budget** per issue,
- completion claims gated on **fresh evidence**,
- **owner-only overrides** of a stopped gate,
- **human-only merge** as a hard rule,
- one **tracker-neutral** layer for GitHub and Jira.

That combination is the product. The individual skills are useful but are not the
differentiator.

## 3. Goals

1. Safe default behaviour: an agent run is bounded, interruptible, and auditable.
2. Works the same on GitHub Issues and Jira from the user's point of view. (Deferred: v0.1 is
   GitHub only.)
3. Each skill is useful on its own; the dispatcher composes them.
4. Easy to install and read: markdown skills plus one small helper script.
5. Honest about what is enforced and what is only instructed.

## 4. Non-goals

- Not a daemon, scheduler, or background poller. Runs are started from an
  interactive session by a person.
- Not an agent runtime or multi-agent framework.
- Not a replacement for CI, branch protection, or code review by humans.
- No auto-merge, ever, including behind a flag.
- No support for trackers beyond GitHub and Jira in the first release
  (the adapter layer must make adding one straightforward).

## 5. Anti-goals (would make this a failure even if it technically works)

- An agent merges, approves, or marks its own work as reviewed.
- A run continues after the stop switch is set or the budget is spent.
- "Done" is claimed with no recorded command output behind it.
- Anything company-specific, personal, or copied from a source whose license does
  not allow it ends up in the repo.
- Setup so heavy that people give up (no long prerequisite list).

## 6. Constraints

- Public repository: `github.com/bharatmane/belay` (owner confirmed public is fine).
  Everything committed is public.
- Quality bar: even though the first release is small, it must meet the standard
  in section 14b. Small scope, high polish.
- License: MIT. Third-party material only as allowed by its license (see section 13).
- Must run on Windows, macOS and Linux. The owner develops on Windows.
- Skills must not require superpowers or any other plugin; they may mention
  optional companions.
- Wording, names and structure are original. No text is carried over from any
  private or proprietary workflow.

## 7. Shape of the project

```
belay/
  .claude-plugin/
    plugin.json            plugin manifest
    marketplace.json       so it can be added as a marketplace
  skills/
    issue-create/SKILL.md
    pr-review/SKILL.md
    dispatch/SKILL.md
  shared/
    config.md              config file reference
    safety.md              the safety rules every skill follows
    adapters/
      github.md            tracker operations via the gh CLI
      jira.md              tracker operations via MCP, jira-cli, or REST
    templates/             issue, PR, comment, run-record templates
  scripts/
    belay.py               enforcement helper (stdlib only)
  tests/
  docs/
  THIRD-PARTY-NOTICES.md
  LICENSE
  README.md
```

Skill names are namespaced by the plugin: `belay:issue-create`, `belay:pr-review`,
`belay:dispatch`.

## 8. Configuration

One file in the user's repo, `.belay.toml` **(my call: TOML, one file; changed from
YAML during planning because Python's standard library reads TOML from 3.11 but has
no YAML parser, and Python 3.11 or newer is an acceptable floor)**:

```toml
tracker = "github"            # "github" or "jira"
base_branch = "main"
idle_minutes = 60             # idle time beyond this is not credited
high_risk_paths = []          # globs that need the approved label before changes
test_command = ""             # what "tests pass" means for this repo
owner = ""                    # GitHub login or Jira account id allowed to override
pr_draft = true               # open new PRs as drafts (safer default)

[github]
repo = "owner/name"

[jira]
site = "example.atlassian.net"
project = "PROJ"

[labels]                      # names are configurable; these are the defaults
ready = "belay:ready"
working = "belay:working"
waiting = "belay:waiting"
review = "belay:review"
failed = "belay:failed"
stop = "belay:stop"
approved = "belay:approved"

[budget]
active_minutes = 90           # time spent working; waiting on a human does not count
attempts_per_stage = 3
```

Run state lives in `.belay/` in the repo root (git-ignored): run records, the stop
file, and the dispatch log.

## 9. The tracker layer

Two separate concerns **(my call)**:

- **Tracker** (where issues live): GitHub Issues or Jira.
- **Code host** (where PRs live): GitHub in the first release. This matters because
  the common Jira setup is Jira issues with GitHub pull requests.

Skills never mention a specific tracker. They use these operations, and each adapter
file explains how to perform them with its own tools:

| Operation | Purpose |
|---|---|
| `find_ready` | list issues carrying the ready label, excluding stopped ones |
| `get_issue` | read title, body, labels, comments |
| `set_state` | swap state labels (and status where the tracker has one) |
| `comment` | post a comment with the Belay run marker |
| `read_new_comments` | comments newer than the last Belay comment |
| `create_issue` | create with confirmed fields |
| `link_pr` | connect a PR to the issue |

GitHub adapter: the `gh` CLI. Jira adapter: Atlassian's MCP server when present,
otherwise `jira-cli`, otherwise a documented REST call. Jira status names differ
per project, so the adapter must **always fetch the available transitions first**
and never guess a status name.

## 10. Safety core

Every skill follows `shared/safety.md`. The rules, and how each is enforced:

| Rule | Enforced by | If the helper is missing |
|---|---|---|
| One issue at a time, one worktree per issue | helper refuses a second active claim | instruction only |
| Stop switch (`.belay/STOP` file or stop label) checked before each stage and retry | helper exits non-zero | instruction only |
| Active-time budget and attempts per stage | helper tracks time and counts | instruction only |
| Stale-claim recovery (heartbeat older than idle limit) | helper | instruction only |
| No completion claim without evidence matching current HEAD | helper rejects `done` without a matching record | instruction only |
| High-risk paths need the approved label | helper checks changed paths | instruction only |
| Overrides count only from the configured owner | helper compares identity | instruction only |
| Never merge, approve, force-push, or reset published history | instruction | instruction |
| Comments carry only counts and ids, never data or secrets | instruction | instruction |

Be honest in the README: a markdown skill can be ignored by a model. The helper makes
the important rules mechanical. Without it, skills print a clear warning that safety
is advisory only.

**The helper (`scripts/belay.py`)** is Python 3.11+ standard library only, one file,
**(my call)** roughly: `claim`, `heartbeat`, `wait`, `resume`, `budget`,
`check-stop`, `evidence`, `done`, `override`, `status`. State is JSON in
`.belay/runs/`. It does not talk to any tracker; the skills do that through the
adapters.

## 11. Skills in the first release

1. **`issue-create`** — turns an idea into a well-formed issue: duplicate check,
   goal, acceptance criteria as Given/When/Then, risks, labels. One confirmation,
   then create and read it back. Never sets the ready label.
2. **`pr-review`** — read-only review of someone else's PR: facts first, temporary
   checkout, independent review lenses, every high or medium finding verified before
   it is reported, ranked report plus draft comments. Asks before posting anything.
3. **`dispatch`** — the safe loop. Picks one ready issue, claims it, works in an
   isolated worktree, builds with tests, records evidence, self-reviews, opens the
   PR, then stops with the issue waiting for a human. For the first release the
   build, test and PR-open stages live inside this skill; they are split into
   their own skills later **(my call)**.

Planned after the first release: `issue-ready` (is this issue clear enough to
build), `review-respond` (handle review comments on your own PR), `status`
(read-only "what needs me").

## 12. Dispatch flow (outline)

1. Check stop switch and budget. Read config.
2. Find a ready issue (or take the one given). Refuse if another run is active.
3. Claim: label, comment with run id, create the run record.
4. Read the issue and the repo area. If anything blocking is unclear, ask all
   questions once in a single comment, set waiting, stop.
5. Create an isolated worktree off the base branch. Record a baseline test run.
6. Implement in small commits, tests first. After each step: heartbeat,
   stop check, budget check.
7. Record evidence (command, exit code, HEAD, time). Self-review the diff.
8. Open the PR linked to the issue, post an evidence comment, set review state,
   stop. A human reviews and merges.
9. On a limit or repeated failure: save evidence, post the blocker, mark failed,
   stop. Only the owner can extend.

## 13. Credit and licensing

- Original wording and structure by default; ideas are cited, not copied.
- Where small pieces of MIT or Apache-2.0 material are adapted, keep the
  copyright and license text, add an attribution header to the file, and list it in
  `THIRD-PARTY-NOTICES.md` with the source link and what changed.
- Do not copy from projects with no license or with share-alike terms
  (juftin/skills; netresearch/jira-skill).
- README has an "Inspired by" section. Projects that shaped the design:
  Symphony (OpenAI), baton (mraza007), gh-devflow-plugins (hhiroshell),
  dev-agent-skills (fvadicamo), baton (blader), jira-skill (netresearch).
  For skill-writing craft: Anthropic's skill-creator and plugin-dev, the official
  code-review, pr-review-toolkit and ralph-loop plugins, OpenAI's curated skills,
  and superpowers. Several of these have no repo-level license in GitHub's
  metadata, so only ideas are taken from them, never wording.
- Licenses above were read from GitHub's metadata, not the LICENSE files; verify
  each LICENSE before adapting anything from it.

## 14. Packaging and releases

- Plugin layout: manifest in `.claude-plugin/plugin.json`, skills as folders under
  `skills/`. Sources disagreed on some manifest fields: **verify against the
  official Claude Code plugin docs while building**.
- Follow the Agent Skills format for each `SKILL.md` (name: lowercase and hyphens,
  at most 64 characters; description at most 1024 characters, written to say when
  to use the skill).
- Versioning: the plugin version is a cache key, so every release gets a unique
  version; `main` carries a `-dev` suffix between releases. Tag releases.
- Install paths documented: plugin marketplace; copy into the personal skills
  folder; other agents' skills folders.

## 14b. Quality bar (learned from studying the best skills)

Studied 2026-11-07: Anthropic's skill-creator and plugin-dev, the official
code-review, pr-review-toolkit and ralph-loop plugins, OpenAI's curated GitHub and
tracker skills (gh-address-comments, gh-fix-ci, yeet, define-goal, linear), and the
superpowers guide to writing skills. Lessons adopted here (ideas only, not text):

**Skill writing**
- **Descriptions say when to use, never what the skill does.** Start with "Use
  when…", third person, concrete trigger phrases, under about 500 characters. A
  description that summarises the workflow gets followed instead of the skill body.
  (Anthropic advises "pushy" descriptions because models under-trigger; reconcile
  by listing many trigger situations, not by describing the process.)
- Body under about 500 lines; heavy detail goes in `references/` with clear
  pointers; imperative voice; explain *why* a rule exists instead of shouting it.
- **Deterministic work belongs in scripts, judgment in prose.** If a rule can be
  checked mechanically, automate it (this is why the helper exists).
- Risky skills are **explicit-invocation only**: `dispatch` must not trigger from
  casual conversation (verify the exact manifest/frontmatter field when building).

**Review discipline (`pr-review`)**
- High signal only. Every finding needs a concrete failing scenario and is verified
  by an independent pass before it is reported; unverified findings are labelled.
- Keep an explicit list of non-findings (pre-existing issues, style nits, things a
  linter catches, deliberately silenced rules).
- Skip closed, draft, trivial, or already-reviewed PRs. Post nothing by default;
  number the findings so the user chooses what to post; one comment per issue;
  cite code with full-SHA permalinks.

**Issue and PR craft**
- Issues carry a goal that is measurable, names its evidence, bounds its scope,
  and says when the agent should stop and ask (the "definition of ready").
- PR bodies explain *why* before *what*; discover and follow the repo's PR
  template; never flip an existing PR between draft and ready; preserve existing
  body content such as images; pass bodies with `--body-file`; no local absolute
  paths; include a Verification section only for real behavioural evidence, not
  generic "tests passed".
- Out-of-scope CI providers are reported with a link, not attempted. Fixes are
  proposed and approved before they are implemented.

**Loops and safety**
- An iteration or time cap is the primary safety net; completion signals alone are
  unreliable. At a limit the agent writes what blocked it, what it tried, and
  alternatives, then stops (the escape-hatch pattern).
- The helper must not depend on bash (known Windows breakage in hook scripts):
  Python standard library, `--json` output, non-zero exit codes on refusal,
  tolerant of `gh` output-field changes.

**Engineering hygiene**
- Test skills like code: run the task **without** the skill (baseline) and **with**
  it, keep the prompts as evals in the repo, and record the failures the skill is
  meant to fix. Include should-trigger and should-not-trigger prompts for each
  description.
- CI validates frontmatter, manifest, description length, internal links, and runs
  helper tests on Windows, macOS and Linux.
- Portable: follow the Agent Skills spec; optionally ship Codex UI metadata
  (`agents/openai.yaml`) and document the cross-tool skills folder.

## 14c. Testing

- Helper script: unit tests (stdlib `unittest`) for the state machine, budget
  maths, stop switch, evidence matching, and owner checks, including clock cases.
- Skills: baseline-versus-with-skill evals (section 14b), plus a written smoke-test
  protocol run against a throwaway GitHub repo, and separately against a Jira
  project when one is available.
- First real use on the owner's own repositories before anything is announced.

## 15. Build order

1. Repo scaffolding, LICENSE, README, notices, plugin manifest (verified against docs).
2. `shared/` rules, config reference, GitHub adapter.
3. Helper script with tests.
4. `issue-create`, then `pr-review` (both standalone, low risk).
5. `dispatch` on GitHub, tested against a scratch repo.
6. Jira adapter and Jira testing.
7. Use on the owner's repos; then decide on the later skills.

## 16. Left to the builder

Exact wording of every skill, template layouts, helper command syntax and JSON
schema, label colours, the README, and the test fixtures — within the rules above.

## 17. My calls, in one place (please check these)

1. TOML config in one file, `.belay.toml` (changed from YAML during planning; see
   section 8). The helper therefore needs Python 3.11 or newer.
2. Tracker and code host are separate concepts; GitHub is the only code host at first.
3. Helper script in Python 3.11+ standard library only, optional but strongly advised;
   skills warn loudly when it is absent.
4. First release has three skills; build, test and PR-open are folded into `dispatch`.
5. Label names `belay:*` and a hidden run marker in comments.
6. Jira via MCP first, `jira-cli` second, REST third.
7. No daemon and no polling, now or later.

## 18. Open questions for the owner

1. Is there a Jira site available for testing the Jira adapter?
2. PRs open as drafts by default (`pr_draft = true`). Resolved by the design author
   during planning as the safer default; the owner can change it.
3. Confirm MIT as the license.
