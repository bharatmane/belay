# Run comment template

Every progress comment Belay posts on an issue with `comment` in the
[GitHub adapter](../adapters/github.md). The first two lines are fixed: a visible line for
people and a hidden marker that `read_new_comments` searches for.

## Placeholders

| Placeholder | Fill with |
|---|---|
| `{{run_id}}` | The run id from `belay claim`, for example `20261107T142233Z-42`. |
| `{{event}}` | One of the events below. |
| `{{state_label}}` | The state label the issue carries after this comment, for example `belay:working`. |
| `{{details}}` | The details for that event, as described below. |

## Events

| Event | Details |
|---|---|
| `Claimed` | Branch name, base branch and its short SHA, and the active-time budget in minutes. |
| `Questions` | Every blocking question at once, numbered, each answerable in a line. End with: "Reply on this issue; Belay reads the replies when the run is resumed." |
| `Resumed` | Which answers were read (by comment link) and what changes because of them. |
| `Ready for review` | The PR link, the test command, its exit code and the SHA it ran on, and the number of commits. |

Only counts, ids, SHAs, exit codes, links and short plain sentences. No log output, stack
traces, environment values, file contents or local absolute paths (see
[safety](../safety.md)). For a stop at a limit use the
[escape hatch](escape-hatch.md) instead.

## Template

```markdown
Belay run {{run_id}}
<!-- belay:run={{run_id}} -->

**{{event}}** (label `{{state_label}}`)

{{details}}
```
