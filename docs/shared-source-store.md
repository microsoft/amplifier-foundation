# Shared source store

`AMPLIFIER_SOURCE_STORE` opts an application into commit-addressed Git source
storage. Without it, the existing resolver and installer contracts are unchanged.
The application owns the cache scope and its repository/ref bindings; Foundation
owns read-only exact-commit objects, per-object locks and atomic publication.

`SharedSourceStore.bind(cache, url, ref, revision)` publishes a verified object
and writes a private binding under the application cache. Bundles and skills
share that binding scope. Ordinary resolution reads the binding; explicit update
resolves a new exact commit and replaces the binding without mutating old trees.
Branch/tag ambiguity requires an explicit ref. Credential-bearing URLs are not
adopted into shared storage.
The Git status API inspects the binding without cloning or replacing it.

On POSIX, publication freezes the verified stage's descendants, renames its
still-writable root, then seals that root while retaining the per-object writer
lock. This ordering supports macOS filesystems that reject renaming write-disabled
directories (Apple's archived documentation describes this restriction, but
macOS 26.6.2 CI allowed the rename on both its default volume and HFS+).
A root with write bits is incomplete: `verify()` and cached status
reject it, and another `ensure()` waits for the writer. Windows retains its
seal-before-rename ordering; directory attributes there are not POSIX access
controls. Existing completed objects and bindings keep their format.

Before publication, the private stage receives its final worktree permissions,
then ordinary Git status refreshes its copied index's stat data. Git metadata is
sealed afterward, without changing tracked-file permissions again. This does not
change HEAD, staged content, or Git's racy-timestamp checks. Existing sources and
published objects remain read-only inputs; their indexes are never refreshed.
A failed or cancelled preparation is drained and removed without publication.
The refresh applies only to ordinary Git metadata contained in the stage;
redirected or symbolic metadata keeps the existing read-only validation path.

Git clones into a child of an owned staging directory, so its full-clone
fallback can replace the checkout inode without replacing cleanup custody.
Failed publication removes only that stable staging directory (or the verified
checkout's renamed, incomplete inode), restoring directory permissions without
following symlinks. Cleanup
failure is attached to the primary exception rather than replacing it; an
incomplete residue is retained and rejected, not silently adopted or repaired.
Cancellation drains the active I/O, including repeated cancellation, before
releasing its writer lock. Successfully sealed objects survive caller
cancellation.

These are cooperative store API guarantees, not protection against unrelated
filesystem writers. Older Foundation readers do not check the POSIX root seal;
hosts sharing a store across versions must not expose an existing binding to an
object being newly published by the corrected writer until all readers use the
completion check. Normal binding creation still follows completed publication.

A legacy source is preserved until the application adopts a verified staging
copy. Dirty or ignored/untracked work and external Git directories cannot enter
the store. A dirty legacy checkout remains authoritative over a binding; refresh
fails without deleting the edit. The application must preflight every duplicate
of a repository/ref before adoption to avoid eclipsing a user's alternate copy.

Module installation uses a writable build view with the same exact source
provenance and a process lock covering the build command. It installs wheels,
not editable references to the read-only tree. uv owns artifact caching and
resolution. Existing mutable/local sources preserve their editable behavior and
original install path. Stored objects and build views are retained: consumers
must prove all readers and references before implementing object collection.

Build views use the existing `builds/<repository-and-commit-id>.lock` for both
materialization and backend execution. A unique owned sibling stage is copied,
made writable and given matching Git provenance, `immutable: false`,
`buildInput: true` and `buildViewVersion: 1` before rename into the final path.
Readers never use mere directory existence as completion. Failed preparation
removes only the invocation's identity-checked stage; cleanup errors are notes on
the original failure. A completed publication survives later cleanup failure.
Atomic visibility is not a power-loss durability/fsync guarantee.

Copy policy is based on `lstat` type, not names: actual sockets and FIFOs are
runtime endpoints and omitted without opening them, including nested Git
fsmonitor endpoints. Regular files with the same names remain build content.
Other unsupported special files fail preparation. Contained relative symlinks
are copied literally (including dangling links); escaping or absolute links are
rejected for builds, so a writable backend cannot follow them back to the
immutable source or an external destination. This is stricter than object
storage, which can retain such links without building from them. Preparation
never chmods, refreshes an index, rewrites metadata or repairs the source object.

Reuse validates ordinary control directories/files, matching exact provenance,
completion flags and a contained existing requested subpath. Legacy completed
views whose final metadata has `immutable: false, buildInput: true` remain
compatible after the same validation; generated backend files are expected, so
reuse does not require a clean Git tree. `build_lock()` rejects damaged or
incomplete build views instead of silently returning an unlocked context.

An old direct-copy failure can leave matching immutable source metadata in a
partial build target. Under the same lock, recovery prepares a fresh completed
view, then moves the attributable old directory into a unique
`builds/.<id>.incomplete-*/checkout` sibling before publishing. All old entries,
including unknown additions, are retained, not merged or recursively deleted.
That metadata establishes attribution, not that every retained entry is ours.
Missing, malformed, mismatched or symbolic control paths are ambiguous: they
are left untouched and rejected for operator inspection. Interrupted stages and
quarantines are not automatically collected. These cooperative API guarantees
do not protect against unrelated writers changing paths outside the locks.

`AMPLIFIER_INSTALL_PREPARATION` can name one isolated preparation attempt with
a fresh 32-character hexadecimal token. With explicit dependency refresh, a
duplicate installation can reuse that attempt's receipt only when source content,
explicit constraints/overrides and the complete installed package metadata match.
An intervening graph/source/policy change or explicit force triggers installation.
A new attempt always resolves fresh; receipts do not pin future dependencies.
External install interpreters and unqualifiable external build inputs never reuse
the caller's metadata evidence.

Hosts that construct registrations from shared settings can create
`BundleRegistry(home=owned_home, persist=False, read_persisted=False)`.
This skips saved registrations even when an older imported registry remains in
that directory. Both flags default to `True`, preserving existing CLI/library
behavior. The explicit `home` still owns source caches.
