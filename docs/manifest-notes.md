# Manifest notes

Verified 2026-11-07 against the official Claude Code docs:
`code.claude.com/docs/en/plugins-reference` (manifest reference),
`.../plugin-marketplaces` (marketplace guide) and `.../skills` (skill frontmatter).
The docs win over any other source. Limitation: the skills page was read through a
summarising fetch that covered the first 100,000 of ~119,000 characters; the
`disable-model-invocation` entry was quoted from it directly.

## plugin.json (`.claude-plugin/plugin.json`)

- The manifest is optional; when present, `name` is the only required key.
- `name`: kebab-case, no spaces, `@`, `:` or path separators. Names starting with
  `claude-`, `anthropic-` etc. are reserved (belay is fine).
- Recommended (validator warns if missing): `version`, `description`, `author`.
- `author` is an object: `name` (required), optional `email`, `url`.
- Other metadata: `homepage` (must parse as a URL), `repository` (string URL, not
  validated), `license` (SPDX id such as `MIT`), `keywords` (array of strings).
- `version` is not checked against semver; setting it pins users to that version
  until it changes, so every release needs a new value.
- Unknown top-level keys are stripped with a warning.
- Component paths must start with `./`. `skills/` is scanned by default, so no
  `skills` key is needed. Put nothing but the manifest inside `.claude-plugin/`.
- Validate locally with `claude plugin validate .` (`--strict` makes warnings fail).

## marketplace.json (`.claude-plugin/marketplace.json`)

Required top-level keys: `name`, `owner` (object with `name`), `plugins` (array).
Optional: `description`. Each plugin entry needs `name` and `source`; `description`
is shown in `/plugin`. A relative `source` is written from the marketplace root (the
directory holding `.claude-plugin/`), starts with `./`, and must not contain `..`.
For belay the plugin lives at the repo root, so `source` is `"./"`.
The entry `name` must equal the `name` in plugin.json. The manifest `version`
overrides an entry `version`, so keep both equal (the validator enforces it).

```json
{
  "name": "belay",
  "description": "...",
  "owner": { "name": "Bharat Mane" },
  "plugins": [
    { "name": "belay", "source": "./", "description": "...", "version": "0.1.0-dev" }
  ]
}
```

Install flow: `claude plugin marketplace add <owner>/<repo>` then
`claude plugin install belay@belay`.

## Skill frontmatter: blocking model-initiated invocation

The exact field is `disable-model-invocation: true` (default `false`). Docs: "Set to
`true` to prevent Claude from automatically loading this skill. Use for workflows you
want to trigger manually with `/name`." The description is then not placed in context
and the skill runs only when the user types the slash command. It also stops
preloading into subagents.

`user-invocable: false` is the opposite (hides it from the `/` menu) and does NOT stop
Claude invoking it. Do not use it for this purpose.

Other rules: field names must match exactly (unknown fields are silently ignored);
booleans accept true/false/yes/no/on/off/1/0; frontmatter is only read when `---` is
the first line. The skills page gives no length limit for `name`; the Agent Skills
spec limits (name 64 chars lowercase-hyphen, description 1024) are enforced by the
repo validator as a portability bar. Skills uploaded to claude.ai or the Skills API
accept only `name`, `description`, `license`, `compatibility`, `metadata`,
`allowed-tools`; Claude Code accepts more, including `disable-model-invocation`.

Consequence for Task 10: `dispatch/SKILL.md` must carry
`disable-model-invocation: true`.
