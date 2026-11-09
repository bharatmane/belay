# Non-findings

What is never reported as a finding, however true it is. Apply this list to every
candidate before verification, and again before writing the report.

Why the list is strict: the user reads the review to decide whether this PR is safe to
merge. Every item they read that cannot change that decision costs attention and trust,
and an author who gets ten comments fixes the easy nine and argues the one that matters.
In testing, the unskilled reviewer put a comment typo next to a deadlock in every run.

## Never a finding

| Not a finding | Why | Test |
|---|---|---|
| Nits: typos in comments or strings, wording, punctuation | They do not change behaviour. The author will fix them if anyone mentions them, which is not a reason for you to. | Would the program behave differently if it were fixed? No: drop it. |
| Style: naming, formatting, import order, line length, comment style | Taste, or the formatter's job. | Is it a written repo rule that the diff breaks? Only then is it a repo-rules candidate. |
| Linter- or compiler-catchable: unused variables, type errors the CI type-checker reports, lint warnings | CI already reports them, more reliably than you. | Report a failing CI check as a fact, not as a finding. |
| Pre-existing: problems in lines the diff does not change and does not newly make reachable | This PR did not cause them; blaming its author for them is wrong and derails the review. | `git diff <base> <head>` does not touch the lines, and the change does not create a new path into them. |
| Speculative: "could", "might", "may want to", "consider", "in the future" | Without a failing input it is a guess, and the author must disprove it. | Can you write the input and the wrong outcome? No: drop it. |
| Contract or API changes nothing breaks | A changed return value is only a bug if something mishandles it. | Search callers at the head; none mishandles it: drop it. |
| Missing tests in general, "more coverage would be nice" | Not a defect. | Only a test that passes regardless, asserts the bug, or misses the exact case the PR claims to fix is a candidate. |
| Design preferences | Opinion, not a defect. | Does it fail now, for a concrete input? No: drop it. |
| Anything already raised in an open review thread | A duplicate thread wastes the author's time. | Mention "already raised in an open thread" under Skipped and why. |

## When a pre-existing problem becomes a finding

It is introduced by the PR, and therefore reportable, when the diff:

- calls the broken code from a new place, or with a new input that triggers it;
- removes a guard that used to stop the bad input reaching it;
- copies the broken pattern into new code.

Cite the changed line that creates the exposure as the finding's `file:line`.

## Noticed, not part of this PR

Decision: a genuinely serious pre-existing problem (security hole, data loss, crash on a
main path) may get **one plain line** under a separate, unnumbered heading
"Noticed, not part of this PR", at most three lines in total. Nothing else pre-existing is
mentioned at all.

Why not silence: a reviewer who saw plaintext passwords and said nothing has withheld
something the user would want to know. Why not a finding: it is not this author's change,
it must not block this PR, and a numbered item would be offered for posting on the wrong
PR. So: unnumbered, never in the draft comments file, never offered for posting; suggest
an issue instead.

## Skipped and why

Dropped candidates are counted, not listed in detail, in the report's last section:
"Not reported: 1 nit, 1 speculative, 2 disproved on verification." This shows the user the
review was thorough without making them read the noise.
