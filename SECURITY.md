# Security policy

Belay is a set of guardrails for coding agents: a hook, a helper and skill text. A way for an
agent to get past one of them is a security issue, even when it needs an unusual command.

## What to report

- A command or tool call that gets past the hook while it should deny it: the stop switch,
  the budget, the human-only rules (merge, approve, force push, push to the base branch,
  control labels) or the guard on `.belay/STOP`, `.belay/runs/` and `.belay.toml`.
- A way to make the helper (`scripts/belay.py`) pass a gate it should refuse: claim, budget,
  attempts, evidence, high-risk paths, overrides.
- Text in an issue, pull request or comment that makes a skill act on it as an instruction.
- Anything in the plugin that leaks secrets, tokens or local paths into issues or comments.

Limits that are already documented (in the README's "Known limits" and in
[shared/safety.md](shared/safety.md#hook-and-helper-limits)) are known; a report that shows
one of them is worse than documented is still welcome.

## How to report

Please do not open a public issue, discussion or pull request with the details.

Report privately through GitHub security advisories: on the repository page, open
**Security**, then **Report a vulnerability**. This creates a private advisory that only
the maintainers can see. If that button is not available, open a public issue that asks for
a private contact and says nothing about the problem itself.

Include what you ran, what you expected the hook or helper to do, what it did, and the
Belay version (`version` in `.claude-plugin/plugin.json`).

## What happens next

The maintainer acknowledges a report as time allows (this is a volunteer project with no
response-time guarantee), confirms whether it reproduces, and fixes it in a release. The
advisory is published once a fix is out, crediting you unless you ask not to be named.

## Supported versions

Only the latest release gets fixes. Before `0.1.0` there are no releases: fixes land on the
main branch.

## Scope

Belay limits what an agent does in your repository; it does not replace branch protection,
required reviews or CI. Keep those on.
