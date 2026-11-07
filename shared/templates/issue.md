# Issue template

The body of an issue made by `create_issue` in the [GitHub adapter](../adapters/github.md).
It is written so that a person, or a later Belay run, can tell whether the issue is ready to
build: the goal is measurable, names its evidence, bounds its scope and says when to stop
and ask.

## Placeholders

| Placeholder | Fill with |
|---|---|
| `{{goal}}` | One or two sentences: the outcome and who benefits. Measurable, not a task list. |
| `{{context}}` | What exists today and why it falls short. Link related issues by number. |
| `{{acceptance_criteria}}` | A numbered list, each item `Given ... When ... Then ...`, each one checkable by a test or a person. |
| `{{evidence}}` | What proves it is done: the test command or the observable behaviour a reviewer can check. |
| `{{out_of_scope}}` | What this issue deliberately does not change, so the work stays bounded. |
| `{{risks}}` | What could break, including any `high_risk_paths` likely to change. `None known.` if none. |
| `{{stop_and_ask}}` | Situations where the builder must stop and ask instead of choosing, for example "the API contract needs to change". |

## Rules

- The title is not part of the body; pass it with `--title`. Make it a short outcome
  ("Export invoices as CSV"), not a task ("Work on export").
- Keep every heading, even when a section is short, so later readers know where to look.
- No secrets, customer data, log dumps or local absolute paths: issues are often public.
- Never add the ready label. A human decides when an issue is ready.

## Template

```markdown
## Goal

{{goal}}

## Context

{{context}}

## Acceptance criteria

{{acceptance_criteria}}

## Evidence of done

{{evidence}}

## Out of scope

{{out_of_scope}}

## Risks

{{risks}}

## Stop and ask if

{{stop_and_ask}}
```
