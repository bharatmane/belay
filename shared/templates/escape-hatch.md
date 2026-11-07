# Escape hatch comment template

Posted once when a run stops at a limit: budget spent, attempts for a stage spent, or a
failure it cannot resolve. It leaves the issue in a state a human can pick up without
reading the session: what stopped, where the work is, and the three ways forward. Follow
the escape-hatch steps in [safety](../safety.md#escape-hatch-when-a-limit-is-hit) first:
`belay wait` before posting, or the hook may deny the comment.

## Placeholders

| Placeholder | Fill with |
|---|---|
| `{{run_id}}` | The run id. |
| `{{limit}}` | Which limit, in plain words: `active-time budget spent`, `attempts spent for stage <stage>`, or a short description of the failure. |
| `{{code}}` | The helper exit code that stopped the run (4 for budget or attempts), or `none` when no helper refusal was involved. |
| `{{head}}` | Short SHA of the last commit on the branch. |
| `{{branch}}` | The run's branch. Add `(not pushed)` if it exists only locally. |
| `{{last_evidence}}` | The last recorded test run: command, exit code and SHA, or `none recorded`. |
| `{{blocker}}` | One or two sentences on what blocked progress. No log output. |
| `{{failed_label}}` | The configured `labels.failed` name. |

## Template

```markdown
Belay run {{run_id}}
<!-- belay:run={{run_id}} -->

**Stopped: {{limit}}** (helper exit code {{code}}, label `{{failed_label}}`)

- Branch: `{{branch}}` at `{{head}}`
- Last test run: {{last_evidence}}
- Blocker: {{blocker}}

Nothing more happens on this issue until a person acts. Choose one:

- **Extend:** the configured owner asks in a Claude Code session to extend run `{{run_id}}`. Belay checks the owner's GitHub login, adds the minutes and resumes.
- **Take over:** check out `{{branch}}` and continue by hand.
- **Retry from scratch:** remove `{{failed_label}}` and add the ready label again.
```
