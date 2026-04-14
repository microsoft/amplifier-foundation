# PreparedBundle Split Refactor Design

## Goal

Break up the 1,390-line `bundle.py` god file into a package with focused files along the natural lifecycle seam: Bundle (declarative composition/config) vs PreparedBundle (runtime session preparation/orchestration).

## Background

`amplifier_foundation/bundle.py` contained four major classes — `Bundle`, `BundleModuleSource`, `BundleModuleResolver`, and `PreparedBundle` — in a single 1,390-line file. Three independent experts flagged it as the top code health issue in the codebase. The file mixed two distinct concerns: declarative bundle composition/configuration (Bundle) and runtime session preparation/orchestration (PreparedBundle), making it harder to navigate, reason about, and maintain.

Nine other packages in the same directory (`mentions/`, `sources/`, `paths/`, `cache/`, etc.) already follow the package-with-internal-modules convention, making this file an outlier.

## Approach

**Option B — Package Conversion** was chosen after consulting four experts (foundation-expert, core-expert, zen-architect, amplifier-expert). Three of four favored this approach.

### Rejected Alternatives

- **Option A (peer files)** — Rejected unanimously. Placing `bundle_dataclass.py` and `bundle_prepared.py` as peers loses the encapsulation signal and violates the existing project convention where related concepts are packaged together.

- **Option C (minimal extraction)** — Zen-architect's preference: extract only `BundleModuleResolver` (zero coupling to other classes). Rejected by 3 of 4 experts because the Bundle/PreparedBundle seam is a real architectural boundary, not speculative. YAGNI applies to features, not to organizing code along existing seams.

## Architecture

### Package Structure

```
amplifier_foundation/bundle/
  __init__.py      — re-exports public symbols only
  _dataclass.py    — Bundle class + 4 private helpers (~580 lines)
  _prepared.py     — BundleModuleSource + BundleModuleResolver + PreparedBundle (~480 lines)
```

The `_` prefix on internal modules signals that consumers should import via the package (`amplifier_foundation.bundle`), not from the submodules directly.

### Circular Import Management

`Bundle.prepare()` creates both `BundleModuleResolver` and `PreparedBundle`, producing a bidirectional dependency between the two modules.

Resolution uses the standard Python lazy-import pattern:

- **`_prepared.py`** imports `Bundle` at **module load** (top-level) — safe because `_dataclass.py` loads first
- **`_dataclass.py`** imports `PreparedBundle` and `BundleModuleResolver` **lazily** inside the `prepare()` method body — avoids circular import at module initialization
- **`_dataclass.py`** uses a `TYPE_CHECKING` guard so pyright sees the types for static analysis without triggering a runtime import

No new abstractions. No dependency injection. Standard pattern.

## Components

### `_dataclass.py` (~580 lines)

Contains the declarative bundle composition layer:

- **`Bundle`** dataclass with all its methods:
  - `compose()` — compose bundles together
  - `to_mount_plan()` — convert to mount plan
  - `prepare()` — create a PreparedBundle (lazy imports from `_prepared.py`)
  - `resolve_context_path()` — resolve context file paths
  - `resolve_agent_path()` — resolve agent file paths
  - `get_system_instruction()` — get the system instruction
  - `resolve_pending_context()` — resolve pending context entries
  - `load_agent_metadata()` — load agent metadata from files
  - `from_dict()` — class method to construct from dict

- **4 private helpers** called only by Bundle:
  - `_parse_agents()`
  - `_load_agent_file_metadata()`
  - `_parse_context()`
  - `_validate_module_list()`

### `_prepared.py` (~480 lines)

Contains the runtime session preparation/orchestration layer:

- **`BundleModuleSource`** — 9-line wrapper dataclass
- **`BundleModuleResolver`** (~122 lines) — resolves bundle modules; has zero coupling to Bundle or PreparedBundle
- **`PreparedBundle`** dataclass with all methods kept together:
  - `_build_bundles_for_resolver()` — build bundle list for resolver
  - `_create_system_prompt_factory()` — create the system prompt factory closure
  - `create_session()` — create a session from the prepared bundle
  - `spawn()` — spawn a running session

**Key design decision:** `_create_system_prompt_factory` and `spawn()` were NOT split into separate files despite the original proposal suggesting this. The zen-architect correctly identified this as over-engineering — both capture `self` in closures, so extraction would mean either passing `self` as a parameter or creating new abstractions. PreparedBundle's methods stay together, respecting cohesion.

**`BundleModuleResolver`** stays in `_prepared.py` rather than getting its own `_resolver.py`. Three of four experts flagged this as a reasonable future refinement but not needed today — zero external consumers, 133 lines, well-demarcated within the file.

### `__init__.py` Re-exports

Public symbols only:

```python
from amplifier_foundation.bundle._dataclass import Bundle
from amplifier_foundation.bundle._prepared import (
    BundleModuleResolver,
    BundleModuleSource,
    PreparedBundle,
)
```

Private helpers (`_load_agent_file_metadata`, `_parse_agents`, `_parse_context`, `_validate_module_list`) are **not** re-exported. They are internal implementation details of Bundle. The package's public API should not advertise private helpers.

## Data Flow

The lifecycle flow across modules:

1. **Configuration** (`_dataclass.py`): `Bundle.from_dict()` or `Bundle.compose()` creates a Bundle from configuration data
2. **Preparation** (`_dataclass.py` → `_prepared.py`): `Bundle.prepare()` lazy-imports `PreparedBundle` and `BundleModuleResolver`, constructs them, and returns a `PreparedBundle`
3. **Session creation** (`_prepared.py`): `PreparedBundle.create_session()` or `PreparedBundle.spawn()` creates a running session with the resolved modules and system prompt

The lazy import in step 2 is the only cross-module coupling point.

## Error Handling

No changes to error handling. All existing error paths, exceptions, and validation logic are preserved exactly as they were in the monolithic file. This is a pure structural refactor.

## Testing Strategy

Since this is a pure structural refactor with zero behavior changes:

- **All 881 existing tests must pass** — this is the primary gate. If the test suite is green, the refactor preserved behavior.

- **Import path verification** — smoke check that key import paths resolve:
  - `from amplifier_foundation.bundle import Bundle`
  - `from amplifier_foundation.bundle import PreparedBundle`
  - `from amplifier_foundation.bundle import BundleModuleResolver`
  - `from amplifier_foundation.bundle import BundleModuleSource`
  - `from amplifier_foundation import Bundle` (top-level re-export unchanged)

- **One test update** — `test_agent_metadata.py` changes its import from:
  ```python
  from amplifier_foundation.bundle import _load_agent_file_metadata
  ```
  to:
  ```python
  from amplifier_foundation.bundle._dataclass import _load_agent_file_metadata
  ```
  Tests reaching into internals is fine; the package's public API just shouldn't advertise private helpers.

- **No new tests needed** — we are not adding behavior, only moving code between files.

## Future Refinements

Three experts noted potential follow-up work, none needed today:

- **Extract `_resolver.py`** — `BundleModuleResolver` has zero coupling to other classes and could get its own file if it grows or gains external consumers.

- **Extract `Bundle.prepare()`** — Moving it to a standalone function in `_prepared.py` would eliminate the circular import entirely. Only warranted if the lazy import pattern causes friction.

- **Rename `_dataclass.py`** — The zen-architect noted the name is generic. `_core.py` or `_bundle.py` could be more descriptive. Minor naming preference, not blocking.

## Open Questions

None. All design decisions were validated through the four-expert consultation and user approval of each section.