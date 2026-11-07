# issue-create: baseline (no skill)

Method: a mid-tier model, no skill loaded, ran each behaviour task in a seeded scratch repo behind the fake `gh`
(`evals/harness/`). Evidence is the transcript plus `calls.log` and the state file. Date: 2026-11-07.
Single run per task; this is a failure catalogue, not a statistic.

## duplicate-found (scenario `has-duplicate`)
- Did well: listed all issues, read #7, named it as the duplicate, created nothing.
- Gap: found it by listing everything with a low `--limit`, not by `--search`. In a repo with more issues than the limit the duplicate would be missed.

## vague-feature-to-story (scenario `default`)
- Created the issue with no confirmation question (the user never saw the draft).
- Acceptance criteria were plain bullets, not Given/When/Then, and nothing was stated as out of scope.
- Applied `enhancement` unasked, guessed from the title.
- Never read the issue back with `issue view` after creating it.
- Searched by listing all issues, not `--search`.
- Wrote the body inline with `--body` in one attempt and `--body-file` in the next (inconsistent; inline text is fragile for markdown).

## missing-labels (scenario `no-labels`)
- Invented scope ("record who did what, on which target and when; newest first; append-only") with no source in the request, and created the issue without confirming it.
- Applied `enhancement` unasked.
- Correctly declined to guess the "ready" label, but offered to CREATE the label and apply it once told its name. That is the anti-goal: whether an issue is ready is a human decision, and creating or applying a ready label is never the tool's call.
- No Given/When/Then, no read-back.

## What the skill must fix (drives SKILL.md)
1. Search with `--search` (all states) before drafting; stop and ask on a likely duplicate.
2. Draft, show it, ask exactly once, create only after a yes.
3. Acceptance criteria as Given/When/Then; an explicit out-of-scope or open-questions section; no invented requirements (anything the user did not say goes under open questions).
4. Apply only labels that exist AND that the user asked for or that classify the type from the repo's own label set; never create labels; never apply or offer to apply the ready label.
5. Body via a file, never inline.
6. Read the created issue back and compare to the draft; print the URL; stop (no implementation).

## Method note (limits)
Baseline agents were told to ask the user by ending their turn; confirmation is therefore observable as a final message that asks and stops. The with-skill run answers "yes" once with a follow-up message.
