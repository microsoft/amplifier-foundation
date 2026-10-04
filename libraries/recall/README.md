# Amplifier Recall

## Purpose and public contract

This package owns a derived search index and versioned memory records, separate
from canonical native history. It follows Foundation's
[mechanism, not policy](../../README.md#philosophy) design and
[optional package boundary](../README.md): caller-selected storage and independent
installation. Amplifier Unified is a consumer, not the authority for generic
consent, personalization or indexing behavior.

[`RecallStore`](src/amplifier_recall/store.py) accepts explicitly authorized source
metadata, stable record identities, revisions and bounded source batches. It
returns scoped search/message pages, staged-generation results, memory versions
and durable mutation receipts. Publication requires the expected source revision;
interrupted staging does not replace the committed index. Search scope never grants
authorization: callers must recheck current visibility before exposing results.
Source discovery, canonical transcript mutation, models, background consolidation,
consent, budgets and retention decisions remain outside this mechanism.

## Contract acceptance entry point

Use the standalone qualification command below with an explicit Python 3.11 or
3.13 interpreter. The [installed-consumer harness](../qualify.py) verifies independent
wheel imports and runs [native-history preservation](tests/test_recall_library.py)
and [scoped index/version contracts](tests/test_scoped_recall.py). Public behavior
changes also require declared consumer authorization/visibility compatibility
checks. [Provenance](PROVENANCE.json), [landing](LANDING.json) and
[qualification](QUALIFICATION.json) retain exact historical scopes; they are not
current host, browser, account or deployment acceptance.

Optional, standalone derived search and versioned record storage. Install `amplifier-recall` independently; importing Foundation does not import or install it. Callers supply authorized source records and storage paths. No agent runtime, model, catalog scan, consent policy, or background worker is started.

## Bounded indexing and reads

`begin_source(metadata, signature, revision)` creates an invisible staging generation. Append complete records with stable IDs in batches of at most 100 using `append_source`. After the caller verifies the source still has the same revision, publish with `commit_source(token, expected_revision=revision)`. Publication is atomic and compares the previous generation, so an older concurrent ingestion cannot replace a newer committed one. Interrupted staging never replaces the prior searchable generation; callers may explicitly discard it.

`search_scope` applies task/workspace/root-source filters in SQLite without materializing the source estate in Python. Search pages contain at most 50 matches; cursors bind the query, scope, and index revision. A changed index requires a fresh search. Ranked search uses SQL offset within that pinned revision. `source_page` uses an ID cursor with at most 100 metadata records. `signature` and `message` use exact indexed source/message lookup. Reading selected message text is separately paged.

Authorization remains with the caller. Search filters do not grant access: recheck current source visibility before exposing results or text. Source metadata must be compact and should not contain transcripts. The compatibility `signatures` and `prune` methods are explicit whole-index maintenance/export operations, not boot or request-path APIs.

Opening an old database preserves its original sources and FTS records without scanning or reindexing them. Legacy sources become searchable in the new index only after the caller explicitly ingests them; source logs are never changed. This library does not silently infer availability or consent from old metadata.

## Authoritative startup

Initialization is serialized by the caller within its owned store partition.
Only an absent main database with no WAL, SHM, or rollback-journal sidecars is
new. Existing stores validate memory notes, versions, and command receipts through
a WAL-aware read-only connection before any writer or derived schema migration.
Missing authority tables or required columns refuse startup instead of appearing
empty or allowing an original command to mutate again. Refusal preserves the main
database and pre-existing nonempty WAL; derived SHM coordination can change.

The complete historical search-only profile remains supported: its original
four-column `sources` table and four-column `messages` FTS5 table, including FTS
shadow tables, with no memory tables or modern schema markers. This recognized
profile gains memory storage without scanning or reindexing old source rows.
If every memory table and every modern schema marker has been deleted so that the
remaining metadata exactly matches this pre-marker profile, the two histories
are indistinguishable; detecting that deletion is outside this bounded guard.

Derived source/document/FTS initialization and documented column migrations
remain available when memory authority is intact. Startup uses fixed schema
metadata and zero-row readability checks, not memory scans, file hashes, copying,
a persistent mirror, or recovery/replay. Normal crash WAL recovery is supported;
this is not a broad database-corruption recovery mechanism.

## Versioned records

Memory records support optimistic revision checks, durable command receipts, and bounded version reads. The caller chooses retention and text limits with `retain_versions` and `max_text_characters`; omitting them imposes no application policy. Deletion clears retained note text from versions and receipts. Consent, personalization defaults, daily model budgets, automated selection, suppression, and delivery policy belong to the integrating application and are not shipped in this package.

## Standalone qualification

From the Foundation checkout, run an explicit Python 3.13 interpreter with `libraries/qualify.py recall --output /owned/qualification/path`. It builds wheel and sdist, installs the wheel in a separate consumer, confirms Core/Foundation/Unified are absent, checks import creates no state, and runs the package tests. Tests cover the 5,000-source ranked search case, SQL scope isolation, staged ingestion and restart, competing commits, explicit retention, and preserving old databases. Host/native/browser acceptance is separate.
