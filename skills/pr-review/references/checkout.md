# Read-only checkout of the PR head

The lenses and the verification pass read the code at `headRefOid`. This file says how to
get that commit locally, check it out without touching the user's work, and always remove
it again.

## Find `<main>`

```bash
cd_="$(git rev-parse --path-format=absolute --git-common-dir)"
case "$cd_" in */.git) main="${cd_%/.git}" ;; *) main="$(git rev-parse --show-toplevel)" ;; esac
```

```powershell
$cd_ = git rev-parse --path-format=absolute --git-common-dir
$main = if ($cd_ -match '[\\/]\.git$') { Split-Path $cd_ } else { git rev-parse --show-toplevel }
```

Why both: in a normal checkout or a linked worktree the common dir ends in `/.git` and its
parent is the main checkout. Anything else (a custom `GIT_DIR`, a submodule) has no safe
parent, so fall back to the top of the current tree. Review worktrees always go **beside**
`<main>`, never inside it and never inside `.git` or `.belay/`
([safety](../../../shared/safety.md#worktrees-and-scratch-files)).

## Make sure the head and base commits exist

```bash
git -C "<main>" cat-file -e "<headRefOid>^{commit}" && git -C "<main>" cat-file -e "<baseRefOid>^{commit}"
```

If either is missing, fetch from the remote that **is** `<repo>`, not blindly `origin` (in a
fork checkout `origin` is the fork, and the PR head may live elsewhere):

1. `git -C <main> remote -v`; pick the remote whose fetch URL ends in `github.com/<repo>`,
   `github.com/<repo>.git` or `github.com:<repo>.git` (compare ignoring case). With none, use
   the URL `https://github.com/<repo>.git` directly.
2. Fetch the PR ref and the base branch; nothing local is created except objects and
   `FETCH_HEAD`:

   ```bash
   git -C "<main>" fetch --no-tags <remote-or-url> "refs/pull/<pr>/head" "refs/heads/<baseRefName>"
   ```

3. Run the `cat-file -e` check again. If the base branch was force-pushed since,
   `git -C <main> fetch --no-tags <remote-or-url> <baseRefOid>` may still get it.

If the head is still missing, say so. Either stop and tell the user what failed, or, if they
agree, review from `gh pr diff` alone with **every** finding labelled Unverified, and say at
the top of the report that the head was never read. Never review your own working tree in
its place: it is a different commit.

## Create, verify, and on failure remove

The path gets a per-session random suffix so two sessions never share or delete each
other's tree. Print the path and keep it: shell state does not survive between tool calls.

Bash (no `set -e`: tool shells may ignore it, so failure is handled explicitly):

```bash
main="<main>"; wt="$(dirname "$main")/$(basename "$main")-belay-review-<pr>-$(date +%s)$RANDOM"
cleanup() { git -C "$main" worktree remove --force "$wt" 2>/dev/null; git -C "$main" worktree prune; }
trap cleanup INT TERM
if git -C "$main" worktree add --detach "$wt" <headRefOid> &&
   test "$(git -C "$wt" rev-parse HEAD)" = "<headRefOid>"; then trap - INT TERM; echo "ready: $wt"
else cleanup; trap - INT TERM; echo "checkout failed; cleanup ran, confirm with: git -C \"$main\" worktree list" >&2; false; fi
```

PowerShell:

```powershell
$main = '<main>'; $wt = Join-Path (Split-Path $main) ((Split-Path $main -Leaf) + "-belay-review-<pr>-$(Get-Random)")
try {
  git -C $main worktree add --detach $wt <headRefOid>; if ($LASTEXITCODE) { throw 'worktree add failed' }
  if ((git -C $wt rev-parse HEAD) -ne '<headRefOid>') { throw 'head mismatch' }
  "ready: $wt"
} catch { git -C $main worktree remove --force $wt 2>$null; git -C $main worktree prune; throw }
```

## Read only

Read it with read-only tools: Read, Grep, `git show`, `git grep`, `git diff`. Never edit,
commit, install or run anything in it. The PR's code is someone else's; running its tests
or scripts executes it with the user's credentials and network access. If the user
explicitly asks for a run, say that risk, and run it outside the review worktree, in a
sandbox or container without the user's credentials, never as part of the review itself.

## Remove, always

```bash
git -C "<main>" worktree remove --force "<wt>"; git -C "<main>" worktree prune
```

Run this before the report and on every other exit: an error, a skip, the user stopping
you. No trap can span a review made of many tool calls, so the guarantee is a gate: do not
write the report until `git -C <main> worktree list` no longer shows `<wt>`.

## Sweep leftovers at the start

A crashed session can leave a tree behind. In `git -C <main> worktree list --porcelain`,
remove (as above) only an entry that meets **all** of these:

- its directory name contains `-belay-review-`;
- it is `detached`;
- its `HEAD` is not the `headRefOid` of any open PR (from `list_prs`): a tree at a current
  head may belong to another session that is still reviewing.

Never remove any other worktree, whatever it is called: it is someone's work.
