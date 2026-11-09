# Testing skills

A skill is code that a model runs, so Belay tests it like code: first watch the task fail
without the skill, then write the skill to fix exactly those failures, then show the same
task passing with it. This page explains the method. The files are in `evals/`.

## Why baseline first

A skill written from imagination fixes problems the model may not have and misses the
ones it does. Running the task **without** the skill first (the baseline) gives a written
list of real failures: an issue created with no confirmation, a review that silently
dropped an injected instruction, a duplicate missed because recent issues were listed
instead of searched. Each rule in a skill should trace back to one of those failures, or to
a safety rule in [shared/safety.md](../shared/safety.md). A rule with neither is a
candidate for deletion.

## The eval file

Each skill has `evals/<skill>.json` with four keys:

| Key | What it holds |
|---|---|
| `skill` | The skill name; must equal the file name. |
| `should_trigger` | At least 3 user prompts that should load the skill. |
| `should_not_trigger` | At least 3 prompts that should not, chosen to sit close to the line ("fix issue 12" for `issue-create`, "merge PR 7" for `pr-review`). |
| `behaviour` | At least 3 tasks, each with an `id`, a seeded `scenario`, a `prompt` and an `expect` list of at least 3 checkable statements. |

`tests/test_skills.py` checks the shape of every eval file, that each scenario exists in the
harness, and that every skill passes the repository validator. It does not run a model.

Write `expect` items as things you can check from **state**, not from the model's reply:
"calls.log shows `issue list --search` before `issue create`", "the run record's evidence
head equals `git rev-parse HEAD`", "the fake gh state has no `belay:ready` label on the new
issue". A reply can claim anything; the state shows what happened.

## The harness

`evals/harness/` holds a fake `gh` (`fake_gh.py`, with `gh` and `gh.cmd` wrappers) and
seeding scripts. `python evals/harness/new_scratch.py <scenario>` creates a scratch
directory with a seeded fake tracker, a real git repository where the scenario needs one,
and prints the `PATH` prefix that puts the fake `gh` first. Every call is logged as a JSON
line in `<scratch>/.fake-gh/calls.log`; tracker state is in `<scratch>/.fake-gh/state.json`.
The fake refuses API writes it does not model, so an unexpected write shows up as an error
rather than passing silently. See [the harness README](../evals/harness/README.md) for the
scenarios.

## The loop

1. **Write the evals** before the skill.
2. **Baseline.** For each behaviour task, start a session in a fresh scratch directory with
   the skill absent, give it the prompt, and record what went wrong in
   `evals/<skill>.baseline.md`: the failure, and the evidence from `calls.log` or state.
   This is a failure catalogue, not a score.
3. **Write the skill** to fix the catalogued failures, explaining why each rule exists.
4. **With-skill run.** Repeat each task in a fresh scratch directory with the skill loaded.
   Record pass or fail for each `expect` item in `evals/<skill>.results.md`.
5. **Iterate.** A failed item means a change to the skill (or to a script, when the rule
   can be checked mechanically), then a re-run of that task. Record the failure and the fix
   in the results file instead of overwriting it: the history shows which rules carry
   weight.
6. **Pressure and injection.** Add at least one run where the user pushes against a rule
   ("I'm in a hurry, skip the confirmation") and one where tracker or PR text contains an
   instruction aimed at the agent. Both must leave state unchanged where the rule says so.

## Triggering

Trigger prompts are checked two ways. Mechanically, descriptions must start with "Use when"
or "Use only when", stay under 1024 characters, and avoid summarising the workflow
(`scripts/validate_repo.py`); a description that summarises the steps tends to be followed
instead of the skill body. Live, the [smoke tests](smoke-tests.md#3-triggering) try the
prompts in a real session with the plugin installed. `dispatch` uses
`disable-model-invocation: true`, so it must never load from any prompt, only from
`/belay:dispatch`.

## Limits, stated plainly

- Each task was run once, on one model. Variance across runs and models is not measured.
- The fake `gh` models only the calls the adapter uses. Real label behaviour, PR creation,
  review posting and the installed hook are covered by the [smoke tests](smoke-tests.md),
  not by the evals.
- Results describe the skill as it was when they were recorded; re-run the affected tasks
  after changing a skill, and name the skill commit in the results entry.
