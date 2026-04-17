# tool-delegate (exp-lean fork)

This is an **experimental fork** of `modules/tool-delegate/` for the exp-lean bundle family. It is not a standalone module and is not intended for use outside exp-lean bundles.

## Why the fork exists

The upstream `tool-delegate` module emits a tool description containing a hardcoded routing block that names specific agents (`foundation:explorer`, `foundation:bug-hunter`, `foundation:zen-architect`, `foundation:modular-builder`, `foundation:git-ops`, `foundation:session-analyst`) as mandatory delegation targets. This block is injected into every session that mounts the tool, regardless of which agents are actually registered.

The exp-lean bundle family intentionally composes arbitrary agent rosters:

- `exp-lean-foundation` has **zero** specialist agents.
- `exp-lean-amplifier-dev` has a specific **7-agent** dev roster.
- Future configurator-generated lean bundles will have **user-chosen** rosters.

In all of these cases, the upstream routing block surfaces agents that are not actually composed. The model references those agents to the user, attempts to delegate, and fails. Users see "phantom agents."

This fork removes the hardcoded routing block surgically. The dynamic "Available agents:" block at the bottom of the tool description — which reads the actual registered agent roster at mount time — is preserved unchanged. It already correctly emits `No agents currently registered. Use agent="self" or a bundle path.` when zero agents are composed.

## Fork provenance

- **Forked from:** `microsoft/amplifier-foundation` @ commit `5354fbb5138b971c01c36ad9843f431a36fd7d21`
- **Upstream path:** `modules/tool-delegate/`
- **Fork path:** `experiments/exp-lean/modules/tool-delegate/`

## Diff vs upstream

### `amplifier_module_tool_delegate/__init__.py` — three surgical text edits

1. **Docstring header.** Adds a fork pointer at the top of the module docstring.

2. **`_compose_description()` body.** Removes upstream lines 211-217 (the `NEVER do these yourself - ALWAYS delegate:` block listing `foundation:*` agents). Replaces with a two-line instruction telling the model to consult the dynamic "Available agents" list below.

3. **`input_schema` for `agent` parameter.** Replaces the example `'foundation:explorer'` with generic `'self' or bundle path like 'namespace:agent-name'`. Same sanitization applied to the module-level docstring example at line 29.

No logic changes. `_get_agent_list()`, config parsing, session resumption, context control, provider preference handling, tool inheritance — all byte-identical to upstream.

### `pyproject.toml` — renamed to prevent dist collision

- **`name`** changed from `amplifier-module-tool-delegate` → `amplifier-module-tool-delegate-exp-lean`. Upstream and fork have the same version (`0.1.0`) in their respective `pyproject.toml` files; installing both into the same Python environment without the rename would silently overwrite one with the other.
- **`description`** updated to explicitly identify this as a fork and point at this README.
- **`maintainers`** added to signal that exp-lean is the maintenance owner even though Microsoft holds the repo.
- **Entry point name** (`tool-delegate` under `[project.entry-points."amplifier.modules"]`) is **preserved** unchanged. Bundle YAMLs that reference `module: tool-delegate` continue to work without modification. Since lean sessions install only the fork (not upstream), there is no entry point collision in practice.
- **Dependency list** (`amplifier-core>=0.1.0`) is copied verbatim from upstream. The module also imports from `amplifier-foundation` (`ProviderPreference`, `tracing.generate_sub_session_id`), which the upstream `pyproject.toml` does not declare. This is a pre-existing upstream bug inherited here intentionally — filing it against upstream is out of scope for this experiment. In practice `amplifier-foundation` is always already installed when this module runs, so the missing declaration never surfaces.

### `tests/` — intentionally not carried forward

The upstream `tests/` directory is **not** included in this fork. Reasons:

- The upstream tests assert on the text of `_compose_description()` — including fragments that this fork removes. Copying them would either bake in failures or require fork-specific rewrites that drift from upstream.
- The fork is delivered via `git+...#subdirectory=...` and installed by `uv pip install`. Tests are not part of the runtime install path.
- The fork's behavioral contract is documented: everything is upstream-identical except the three text edits above. Any regression in that contract would surface either as upstream tests failing against upstream (our signal to re-audit the fork) or as a shadow-environment smoke test failing (see Maintenance protocol below).

If fork-specific tests become warranted later, a single test asserting that the rendered tool description does **not** contain any hardcoded `foundation:*` agent names would be sufficient.

## Maintenance protocol

When upstream `tool-delegate` gets updated:

1. Diff the upstream `__init__.py` against this fork at the pinned SHA above.
2. For any new content added to `_compose_description`, `input_schema`, or the module docstring, scan for hardcoded `foundation:*` or other bundle-specific agent names.
3. If found, re-apply the same sanitization (remove or replace with generic examples).
4. Re-pin this fork's provenance SHA to the new upstream commit.
5. Verify in a shadow environment: load a lean bundle, confirm the tool description contains no phantom agent names.

The fork is small by design. Keeping the diff minimal means upstream bug fixes and feature additions can be picked up with a clean merge in most cases.

## Non-goals

- This fork is **not** a long-term replacement for upstream tool-delegate. It exists because the exp-lean family ships faster than the upstream tool can add a config-based description override. If upstream adds `description_override` (or equivalent), this fork should be deleted and lean bundles should configure the upstream module directly.
- This fork is **not** for general use. Other bundles should continue to use upstream `modules/tool-delegate/`.
