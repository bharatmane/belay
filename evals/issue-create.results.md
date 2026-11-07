# issue-create: with-skill results

Same method and seeds as `issue-create.baseline.md` (fake `gh`, one run per task, mid-tier model, skill read from
`skills/issue-create/SKILL.md`; the simulated user answers "yes" once when asked). Date: 2026-11-07.
Evidence: transcript plus `calls.log` and state file of each scratch repo.

## duplicate-found: 4/4
- [x] `issue list --search` (all states) ran before any create; no create happened.
- [x] Reply names #7 (open, "Login page returns 500 on empty password") as the likely duplicate.
- [x] No new issue; state holds exactly the seeded issues.
- [x] No `belay:ready` anywhere.

## vague-feature-to-story: 9/9
- [x] Search before create. [x] `label list` before create.
- [x] Body has Given/When/Then. [x] Out of scope and open questions (PDF format, bulk vs single, permissions).
- [x] Exactly one confirmation question. [x] No `belay:ready`. [x] Only existing label (`enhancement`).
- [x] `issue view 5` after create. [x] URL printed, stopped, no implementation.

## missing-labels: 8/8
- [x] Search and `label list` before create. [x] No `label create` run and no ready label applied.
- [x] Told the user readiness is their call and that the belay labels do not exist (and gave the commands for the user to run).
- [x] Created issue has no `belay:*` label. [x] One confirmation. [x] Given/When/Then. [x] Read back via `issue view`.

## Remaining weaknesses (honest)
- One run per task on one model; no variance measured. Trigger evals (should / should-not trigger) are checked structurally by `tests/test_skills.py`; live trigger testing is Task 11.
- The skill printed `gh label create` for the user to run. That is allowed (the user acts), but it is the closest the runs came to the baseline's "offer to create the ready label" failure; watch for it in later runs.
- The duplicate run did not open #7's body before naming it a likely duplicate (title match only).
