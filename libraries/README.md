# Optional application libraries

These packages provide shared application mechanisms without extending the
default `amplifier-foundation` wheel or importing its native runtime. Each has its
own package metadata, tests, public API and installation. Applications choose the
libraries they need. Importing a library must not create storage, discover session
history, start a worker or schedule background work.

| Directory | Package | Mechanism |
| --- | --- | --- |
| `scheduling/` | `amplifier-scheduling` | Recurrence calculations, revisions and durable run claims |
| `operations/` | `amplifier-operations` | Operation evidence, output cursors and coordination records |
| `worktrees/` | `amplifier-worktrees` | Managed Git checkouts, source preservation and ownership evidence |
| `recall/` | `amplifier-recall` | Derived search and versioned memory storage |

Application authorization, task execution, notifications, user interface state,
consent and personalization policy stay with consumers. These packages do not
import a Unified host or acquire native session ownership. Kernel-mounted modules
retain their Core-only dependency boundary; these libraries are application APIs.

Build an individual package with `uv build --directory libraries/<name>`, then
install its wheel into an owned consumer environment. Point installers explicitly
at that environment. Never use `uv run --active` against an inherited environment
from a running application. The packages retain independent import names; moving
their source here does not require consumer code to import Foundation's root.

## Parallel ownership

Each library owns its source, API contract, tests and package metadata. Use a
separate Git worktree for each concurrent change. Changes to this index, shared CI
or release metadata have one integration owner. Library tests are independent;
cross-library contract changes also run their declared consumer tests.

`LANDING.json` and available extraction manifests record provenance. Qualification
receipts describe the exact tested artifact and coverage limits; they do not claim
that a source change has been published or adopted by a running host. Each library
can be published and upgraded independently from the base Foundation wheel.
