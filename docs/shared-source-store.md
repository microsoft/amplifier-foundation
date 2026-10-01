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
