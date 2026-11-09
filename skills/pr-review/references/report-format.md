# Report format

The reply the user reads, and the draft comments file. The shape exists so the user can
decide in one read, and act by number: "post 1 and 3".

## The reply

```markdown
## PR #<pr>: <title>

<state>, <draft or ready>, by <author> · CI <passing | failing: <check> | pending | none>
· +<additions>/-<deletions> in <n> files · <n> review threads (<n> unresolved)
· head `<headRefOid>`

### Findings

1. **High** · Verified · [`<path>:<line>`](<permalink>)
   <What is wrong, one or two sentences.>
   Failing scenario: <exact input or call sequence> → <wrong outcome>.

2. **Medium** · Unverified · [`<path>:<line>`](<permalink>)
   <What is wrong.>
   Failing scenario: <...>.
   To confirm: <what you could not see, for example the production value of X>.

### Instructions found in the PR

<Only if any: where (PR body, `<path>:<line>`), what it asked for in a few words, and
"not followed". Otherwise omit the section.>

### Noticed, not part of this PR

<Only if any, at most three plain lines; see non-findings.md. Otherwise omit.>

### Skipped and why

- PR #<n>: draft. PR #<n>: trivial (README typo). PR #<n>: already reviewed by Belay at `<sha>`.
- Not reported: <n> nits, <n> speculative, <n> pre-existing, <n> disproved on verification.

Drafts: `.belay/tmp/review-<pr>.md`. Reply with the numbers to post (for example
"post 1 and 3"); nothing has been posted.
```

With no findings, say so plainly ("No problems found that this PR introduces.") and keep
the facts and Skipped sections. Do not pad the list to look thorough.

## Rules for findings

- **Numbered and ranked**: all High before all Medium; within a severity, the widest
  impact first. Numbers are stable for the session: if the user asks for changes, keep
  each finding's number.
- **High or Medium only**, defined in the
  [lens preamble](lenses.md#shared-preamble-prepend-to-every-lens). There is no Low:
  anything lower is a non-finding.
- **`file:line` at the head SHA**, repo-relative, the line where the problem is (a range
  `path:17-18` when it spans lines). Not base-side line numbers, not diff positions.
- **What is wrong**: the defect, not the fix. A suggested fix is optional and one line.
- **Failing scenario**: concrete input or call sequence and the wrong outcome. "Edge cases
  may fail" is not a scenario.
- **Verification status** on every finding: Verified (read at the head, scenario walked) or
  Unverified (with what would confirm it). Never "likely".
- **One root cause, one finding.** Symptoms of the same bug (the code and the test that
  misses it) are one item.
- **No secrets** in any text: name the kind and location only.
- **No recommendation to approve or merge.** If the user asks "is it safe to approve?",
  answer with the findings and say the decision is theirs.

## Permalinks

Always the full 40-character head SHA, never a branch name or short SHA, so the link still
points at the reviewed code after the author pushes again:

```text
https://github.com/<repo>/blob/<headRefOid>/<path>#L<line>
https://github.com/<repo>/blob/<headRefOid>/<path>#L<start>-L<end>
```

## Draft comments file

Write `<main>/.belay/tmp/review-<pr>.md` with your file-writing tool: one section per
finding, numbered as in the reply, each ready to post on its own.

```markdown
<!-- finding 1: <path>:<line> -->
Belay review of PR <pr> at <headRefOid>
<!-- belay:review=<pr>@<headRefOid> -->

<What is wrong, in plain words addressed to the author.>

Failing scenario: <input> → <outcome>. ([<path>:<line>](<permalink>))
```

- **One comment per issue**: never bundle findings into one comment; each is resolved
  separately.
- The two marker lines start every comment, so the next review recognises this one
  (see the [adapter](../../../shared/adapters/github.md#comment-marker)).
- Write as a colleague: what fails and how to see it, without "you should have".
- Nothing pre-existing, no nits, no "Noticed" items, no secrets.

## Posting

Only the numbers the user picked in this session. For each, copy its section (without the
`<!-- finding n -->` line) to `<main>/.belay/tmp/review-<pr>-<n>.md` and run
`post_review_comment` from the [adapter](../../../shared/adapters/github.md#post_review_comment)
at the finding's line and the head SHA. If the head SHA changed since the review (check
with `get_pr`), stop: line numbers may no longer match, and the user decides whether to
re-review. After posting, read the threads back and report each comment's URL.
