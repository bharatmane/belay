# Releasing

The maintainer performs every tag, push and directory submission. Automation and agents
working on this repository prepare a release commit and stop.

## Why versions matter

Claude Code uses the plugin `version` as a cache key: users stay on a version until it
changes. So every release gets a version that has never been used, and `main` carries a
`-dev` suffix between releases (for example `0.1.1-dev` after `0.1.0`). The version appears
in two places, `.claude-plugin/plugin.json` and the plugin entry in
`.claude-plugin/marketplace.json`, and `scripts/validate_repo.py` fails if they differ.

## Steps

1. **Pick the version** `X.Y.Z` (semantic versioning: patch for fixes, minor for new skills
   or options, major for a change that breaks existing `.belay.toml` files or run records).
2. **Set it** in both manifests: `"version": "X.Y.Z"`.
3. **Update `CHANGELOG.md`**: rename `## Unreleased` to `## X.Y.Z - YYYY-MM-DD` and add a new
   empty `## Unreleased` above it.
4. **Check**:

   ```bash
   python scripts/validate_repo.py
   python -m unittest discover -s tests -v
   claude plugin validate .
   ```

   All must pass. Then run the [smoke tests](smoke-tests.md) against the scratch repository
   and record the results there, with the date and versions.
5. **Commit** the release: `git commit -am "release: vX.Y.Z"`.
6. **Tag and push** (maintainer):

   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin main vX.Y.Z
   ```

7. **Back to development**: set both manifests to the next `-dev` version (for example
   `X.Y.(Z+1)-dev`), commit `chore: start X.Y.(Z+1)-dev`, push.

## Distribution

1. **This repository as a marketplace.** Users run
   `/plugin marketplace add bharatmane/belay` and `/plugin install belay@belay`. No approval
   is needed; a pushed release is available at once.
2. **Anthropic's plugin directory.** Submit through the
   [plugin directory submission form](https://clau.de/plugin-directory-submission) only
   after `0.1.0` is tagged and has had real use on the maintainer's own repositories. A
   directory entry pins a release ref and commit SHA, and the plugin `name` becomes a
   permanent slug, so submit a release you are prepared to stand behind.
3. **Agent Skills tooling and community directories.** List Belay only after the
   maintainer has confirmed how each one accepts submissions. Do not submit on a guess.
