# Amplifier Recall

Optional, standalone derived search and versioned record storage. Install `amplifier-recall` independently; importing Foundation does not import or install it. Callers supply authorized source records and storage paths. No agent runtime, model, catalog scan, consent policy, or background worker is started.

## Bounded indexing and reads

`begin_source(metadata, signature, revision)` creates an invisible staging generation. Append complete records with stable IDs in batches of at most 100 using `append_source`. After the caller verifies the source still has the same revision, publish with `commit_source(token, expected_revision=revision)`. Publication is atomic and compares the previous generation, so an older concurrent ingestion cannot replace a newer committed one. Interrupted staging never replaces the prior searchable generation; callers may explicitly discard it.

`search_scope` applies task/workspace/root-source filters in SQLite without materializing the source estate in Python. Search pages contain at most 50 matches; cursors bind the query, scope, and index revision. A changed index requires a fresh search. Ranked search uses SQL offset within that pinned revision. `source_page` uses an ID cursor with at most 100 metadata records. `signature` and `message` use exact indexed source/message lookup. Reading selected message text is separately paged.

Authorization remains with the caller. Search filters do not grant access: recheck current source visibility before exposing results or text. Source metadata must be compact and should not contain transcripts. The compatibility `signatures` and `prune` methods are explicit whole-index maintenance/export operations, not boot or request-path APIs.

Opening an old database preserves its original sources and FTS records without scanning or reindexing them. Legacy sources become searchable in the new index only after the caller explicitly ingests them; source logs are never changed. This library does not silently infer availability or consent from old metadata.

## Versioned records

Memory records support optimistic revision checks, durable command receipts, and bounded version reads. The caller chooses retention and text limits with `retain_versions` and `max_text_characters`; omitting them imposes no application policy. Deletion clears retained note text from versions and receipts. Consent, personalization defaults, daily model budgets, automated selection, suppression, and delivery policy belong to the integrating application and are not shipped in this package.

## Standalone qualification

From the Foundation checkout, run an explicit Python 3.13 interpreter with `libraries/qualify.py recall --output /owned/qualification/path`. It builds wheel and sdist, installs the wheel in a separate consumer, confirms Core/Foundation/Unified are absent, checks import creates no state, and runs the package tests. Tests cover the 5,000-source ranked search case, SQL scope isolation, staged ingestion and restart, competing commits, explicit retention, and preserving old databases. Host/native/browser acceptance is separate.
