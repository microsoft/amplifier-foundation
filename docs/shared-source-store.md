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
lock. This ordering supports macOS, which rejects renaming write-disabled
directories. A root with write bits is incomplete: `verify()` and cached status
reject it, and another `ensure()` waits for the writer. Windows retains its
seal-before-rename ordering; directory attributes there are not POSIX access
controls. Existing completed objects and bindings keep their format.

Failed publication removes only the owned stage (or its renamed, incomplete
inode), restoring directory permissions without following symlinks. Cleanup
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
