# Jira adapter

> **Status: planned, not yet supported.** Belay works with GitHub Issues only. Setting
> `tracker = "jira"` in [config](../config.md) is accepted but no skill can act on it yet:
> a skill that finds it must stop and say so rather than guess.

This page records the intended design so the adapter can be built to the same contract as
the [GitHub adapter](github.md): the same seven operations (`find_ready`, `get_issue`,
`set_state`, `comment`, `read_new_comments`, `create_issue`, `link_pr`), with pull requests
still on GitHub.

## Intended order of tools

Use the first that is available:

1. **Atlassian's MCP server**, when the session has it connected: authentication and
   pagination are handled for you.
2. **`jira-cli`**, when installed and logged in.
3. **The Jira REST API**, as a documented last resort, with a token the user supplies
   through their own environment. Belay never stores or prints it.

## Rules the adapter will follow

- **Always fetch the available transitions first, and never guess a status name.**
  Workflows and status names differ per project, and an invalid transition fails or moves
  the issue somewhere unintended. Pick a transition only from the list returned for that
  issue; if none matches the intended state, stop and ask.
- Labels still carry the Belay states and the stop and approved switches, so the
  [safety rules](../safety.md) apply unchanged.
- The `owner` key holds a Jira account id, read from the tool's "current user" call.
- `link_pr` uses the issue key in the PR title or branch name, which Jira's GitHub
  integration recognises, plus a comment on the issue with the PR URL.
