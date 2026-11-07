# Issue quality bar

What a finished draft must meet before it is shown to the user. The sections come from the
[issue template](../../../shared/templates/issue.md); this file says what makes each one good.

An issue is read later by someone with none of today's context: a teammate, a reviewer, or
an agent run that builds exactly what is written. Every gap you fill with a guess becomes a
requirement nobody asked for. Every vague line becomes an argument at review time.

## Type

Classify before drafting, because the type decides the title's shape, the label, and what
the acceptance criteria must prove.

| Type | It is this when | Title shape | Criteria prove |
|---|---|---|---|
| bug | Existing behaviour is wrong: an error, a crash, a wrong result, a regression | What goes wrong, where: "Login returns 500 on empty password" | The failing case now behaves correctly, and nearby cases still do |
| feature | Users get something they cannot do today. A "story" is a feature | The new outcome: "Export invoices as PDF" | Each new behaviour, observable by a user or a test |
| chore | No user-visible change: dependencies, build, CI, refactoring, docs housekeeping | The change: "Upgrade the test runner to v9" | Nothing user-visible changed, and the stated technical outcome holds |

If it could be two types, pick the one the user's words point to and say so in the draft;
the user corrects it at the confirmation.

## Goal: measurable

One or two sentences: the outcome, and who benefits. A reader must be able to tell, from
the goal alone, whether it was met.

- Good: "Billing users can download any of their invoices as a PDF from the billing page."
- Bad: "Improve invoice exporting." (Improve how much? For whom? When is it done?)

Do not add numbers the user did not give. "Fast" stays a question ("What load time is
acceptable?") rather than becoming "under 200 ms".

## Context

What exists today and why it falls short, in the user's terms. Link related or possibly
duplicate issues by number (`#7`), including closed ones the search found, so the builder
reads them first.

## Acceptance criteria: Given / When / Then

A numbered list. Each item is one scenario, written so that a test or a person can check
it with a yes or no:

```markdown
1. **Given** a signed-in user with at least one invoice
   **When** they choose "Download PDF" on an invoice in the billing page
   **Then** a PDF of that invoice downloads
```

- One behaviour per item. Two "Then"s that can fail independently are two items.
- Concrete nouns and observable results ("a 400 response with message X", "the row
  disappears"), never "works correctly" or "is handled gracefully".
- For a bug, include the exact failing case from the report and at least one neighbouring
  case that must keep working.
- Cover only what the user asked for. A criterion you invented to be thorough is scope the
  user never agreed to. Put the gap in Open questions instead.

## Evidence of done

What a reviewer runs or looks at to accept the work: a test command, a new test's name, or
an observable behaviour ("open /billing, click Download PDF, a PDF opens"). "Tests pass"
alone is not evidence; say which tests.

## Out of scope

What this issue deliberately does not change. Name the nearest tempting extension so it is
excluded on purpose: "Other export formats (CSV, Excel)", "Bulk export of many invoices".
This keeps the work, and the review, bounded.

## Open questions

Everything the request left undecided, one question per bullet. This is where the user's
"I'm not sure yet" goes, and where any detail you were tempted to invent goes.

- Good: "Which PDF layout: the on-screen invoice, or a printable statement format?"
- Bad: writing "The PDF uses A4 with the company logo in the header" when nobody said so.

An open question is cheap to answer later. An invented requirement gets built.

## Risks

What could break: shared code paths, data migrations, public APIs, anything matching the
repository's `high_risk_paths` in `.belay.toml`. `None known.` is an honest answer;
padding is not.

## Stop and ask if

The line between choosing and asking. List situations where the builder must stop and ask
a human instead of deciding, for example:

- an open question above is still unanswered when the work reaches it;
- the change needs a new dependency, a schema change, or a public API change;
- a criterion turns out to contradict existing behaviour.

## One tight example each

Good (feature, from "users should export invoices as PDF from the billing page, not sure
about the format"):

```markdown
## Goal

Billing users can download any of their invoices as a PDF from the billing page.

## Context

Invoices can only be viewed on screen today, so users who need a file copy ask support.

## Acceptance criteria

1. **Given** a signed-in user with at least one invoice
   **When** they choose "Download PDF" on an invoice in the billing page
   **Then** a PDF of that invoice downloads
2. **Given** a signed-in user
   **When** they request the PDF of an invoice that belongs to another account
   **Then** the download is refused

## Evidence of done

A test covering both criteria; a reviewer downloads one invoice from the billing page.

## Out of scope

Other formats (CSV, Excel); bulk export; emailing invoices.

## Open questions

- Which PDF layout and paper size should the export use?

## Risks

Invoice access control: the download must not expose other accounts' invoices.

## Stop and ask if

The PDF layout question is still open when the rendering work starts.
```

Bad (same request):

```markdown
Add PDF export to billing. Use A4 with the logo, include line items and tax breakdown,
support bulk export and email delivery. Should be fast and work well.
```

It invents layout, bulk export and email, none of which the user asked for; nothing is
checkable; there is no scope line; and the one thing the user said was undecided, the
format, is silently decided.
