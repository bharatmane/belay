# Pull request body template

The body of a pull request opened by `link_pr` in the [GitHub adapter](../adapters/github.md).
A reviewer reads why before what, so the reasons come first.

## Placeholders

| Placeholder | Fill with |
|---|---|
| `{{issue_ref}}` | `Closes #<number>`, or `Closes <owner>/<name>#<number>` for an issue in another repository. |
| `{{why}}` | The problem and why this approach, in a few sentences. Not a restatement of the diff. |
| `{{what}}` | A short list of the changes a reviewer should look at, most important first. |
| `{{verification}}` | Real behavioural evidence: the command run on the final commit, its exit code and the commit SHA, plus anything checked by hand. |
| `{{risks}}` | What could break and how a reviewer can check it. `None known.` if none. |
| `{{run_id}}` | The Belay run id. |

## Rules

- If the repository has a pull request template (for example
  `.github/pull_request_template.md`), follow its headings and put this content under them;
  keep `{{issue_ref}}` and the closing line.
- Delete the Verification section rather than write "tests passed" without a command, exit
  code and SHA. A generic claim is worse than none because it looks like evidence.
- No local absolute paths, secrets or log dumps.
- Pass the finished body with `--body-file`. When editing an existing PR, keep its current
  body (images included) and change only what is needed.

## Template

```markdown
{{issue_ref}}

## Why

{{why}}

## What changed

{{what}}

## Verification

{{verification}}

## Risks

{{risks}}

---

Opened by Belay run {{run_id}}. A human reviews and merges this pull request.
```
