# Managed Git worktrees

## Purpose and public contract

This package owns reusable Git checkout lifecycle and retained source/ownership
evidence. It follows Foundation's [mechanism, not policy](../../README.md#philosophy)
design and [optional package boundary](../README.md), with independent installation
and explicit managed storage. Amplifier Unified is one consumer; its task and
handoff policy does not define this library's generic contract.

[`GitWorktrees`](src/amplifier_worktrees/git.py) accepts a source checkout, exact
inspected revision, stable command identity and an explicit clean/dirty-copy mode.
It returns checkout records, immutable source evidence and status; stale revisions
or unsafe cleanup refuse. Imported/attached paths do not gain deletion ownership.
Partial and unknown attempts remain inspectable without automatic retry. Session
handoff, active-work guards, permissions and cleanup intent remain caller-owned.
This library is neither a runtime supervisor nor a repository synchronization service.

## Contract acceptance entry point

Run an explicit Python 3.11 or 3.13 interpreter from the Foundation checkout:
`python libraries/qualify.py worktrees --output /owned/qualification/path`.
The [installed-consumer harness](../qualify.py) builds wheel/sdist, installs the
wheel separately and checks import state/runtime isolation before running
[checkout preservation and ownership tests](tests/test_worktrees_library.py).
They cover clean/dirty copies, stale-source/branch conflicts, unmerged or unsupported
indexes, external storage and detached-commit retention. Public API changes also
require declared handoff/portability consumer compatibility checks.
[Landing](LANDING.json) and [qualification](QUALIFICATION.json) preserve historical
revision/artifact scopes. OS/Git/filesystem variants, actual handoff, browser/account
and deployment acceptance need their own receipts.

A small reusable Git library for inspect/create/attach/status/remove. It accepts
bounded argv and never invokes a shell. Repository hooks and external diff
helpers are disabled. A file lock in the common Git directory serializes
participating hosts; Git retains its own branch/worktree locking. It performs no
fetch, push, merge, reset, or forced deletion.

Create requires the exact inspected source revision (HEAD, index/worktree diffs,
untracked content hashes). Default mode starts a clean detached checkout from a
committed ref; a requested new branch uses ordinary Git ownership checks. An
explicit `carry_dirty` copies staged/unstaged patches and nonignored untracked
regular files/symlinks. It requires current HEAD and refuses unmerged indexes,
submodules, and index flags it cannot preserve. Originals are never changed.
Untracked capture is bounded to 2000 files / 20 MB; each Git output is bounded to
20 MB and each Git invocation to 30 seconds.

Private durable JSON records and source manifests precede mutation. Patch bytes,
hashes and untracked evidence are retained under the manifest evidenceDirectory.
A partial attempt remains inspectable and is never replayed automatically. Stable
command identities deduplicate create/attach/remove. Existing checkouts may be
attached but do not acquire deletion ownership. App-created cleanup verifies
current repository/path ownership and refuses changed, untracked or ignored files.
Unreferenced detached commits must be retained on a branch/tag or another worktree
before removal. It preserves branches, source folders, manifests and history. The host supplies
active-task/handoff guards and explicit user intent; this library owns no runtime
or permission controller.

Managed storage must be outside the source checkout. This avoids storing the
manifest itself among the source files being copied. A checkout can be a source
for another checkout under the same managed parent.

Configured clean/smudge/process filters are disabled per invocation, with no source
configuration edits. Inspection, checkout and apply operate on raw Git/worktree
representations. LFS pointers stay pointers in clean checkouts; materialized dirty
files are carried only within the normal explicit-copy bounds.

A host may inject an opaque JSON `execution_host` descriptor at construction. It
is copied into new create/attach records as `executionHost`, never derived from
private machine data by this library. Legacy records are not rewritten. The host
owns identity validation and guards before mutation or runtime admission.

## Standalone qualification

Build with `uv build`. Install the wheel in a separate environment and run `python -m pytest tests`. Tests must not import Unified implementation or native runtime packages. `PROVENANCE.json` records extraction sources. The distribution owns host integration and real-account acceptance.
