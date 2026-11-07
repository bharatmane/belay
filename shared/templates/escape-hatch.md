# Escape hatch comment template

Posted once when a run stops at a limit: budget spent, attempts for a stage spent, or a
failure it cannot resolve. It leaves the issue in a state a human can pick up without
reading the session: what stopped, where the work is, and the ways forward. Follow the
order in [safety](../safety.md#escape-hatch-when-a-limit-is-hit): `belay fail` first, then
this comment, then the failed label. Not for the stop switch, which is a pause and posts
nothing.

## Placeholders

| Placeholder | Fill with |
|---|---|
| `{{run_id}}` | The run id. |
| `{{limit}}` | Which limit, in plain words: `active-time budget spent`, `attempts spent for stage <stage>`, or a short description of the failure. |
| `{{code}}` | `helper exit code 4` when the helper refused, or `no helper refusal` for a failure the run could not resolve. |
| `{{head}}` | Short SHA of the last commit on the branch. |
| `{{branch}}` | The run's branch. Add `(not pushed: only on the machine that ran Belay)` if it exists only locally. |
| `{{last_evidence}}` | The last recorded test run: command, exit code and SHA, or `none recorded`. |
| `{{blocker}}` | One or two sentences on what blocked progress. No log output. |
| `{{failed_label}}` | The configured `labels.failed` name. |

## Rules

- Keep the **Extend** line only when the active-time budget was spent. Extending adds
  active-time minutes, never attempts, so it cannot help a run that spent its attempts.
- If the branch is not pushed, keep the "Take over" line: it still works on that machine,
  and the branch note says so.

## Template

```markdown
Belay run {{run_id}}
<!-- belay:run={{run_id}} -->

**Stopped: {{limit}}** ({{code}}; label `{{failed_label}}`)

- Branch: `{{branch}}` at `{{head}}`
- Last test run: {{last_evidence}}
- Blocker: {{blocker}}

Nothing more happens on this issue until a person acts. Choose one:

- **Extend** (budget stops only): the configured owner asks in a Claude Code session to extend run `{{run_id}}`. Belay checks the owner's GitHub login, adds active-time minutes and reopens the run.
- **Take over:** continue from `{{branch}}` by hand.
- **Retry from scratch:** remove `{{failed_label}}` and add the ready label again; a new run starts.
```
