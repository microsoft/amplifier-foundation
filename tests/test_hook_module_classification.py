"""Regression tests for hook module classification via amplifier-core's real
module loader.

Root cause this guards against: amplifier-core's `ModuleLoader._get_module_metadata`
falls back to `_guess_from_naming()` for any module that doesn't declare an
explicit `__amplifier_module_type__` attribute (none of foundation's in-tree
hook modules do). `_guess_from_naming()` does a first-match substring scan over
a fixed keyword order (`orchestrat`, `loop`, `provider`, `tool`, `hook`,
`context`) -- so a hook module whose id contains an *earlier* keyword than
"hook" is silently misclassified.

`hooks-tool-dedupe` tripped exactly this: it contains "tool" (an earlier
keyword) as well as "hook", so it was classified as a `tool` module and
validated with `ToolValidator` instead of `HookValidator`, which then failed
`protocol_compliance` with "No tool was mounted and mount() did not return a
Tool instance" -- even though the module loaded and mounted correctly as a
hook. The fix renamed the module to `hooks-dedupe` (no competing keyword);
this test exercises the *actual* amplifier-core loader classification and
validation path (not just `mount()` called directly against a fake
coordinator, which every module's own test suite already covers and which
would NOT catch this class of bug) so a future hook module with a colliding
substring in its name fails CI instead of silently never activating.

See: amplifier-core python/amplifier_core/loader.py, `_guess_from_naming()`
and `_get_module_metadata()`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from amplifier_core.loader import ModuleLoader

MODULES_DIR = Path(__file__).parent.parent / "modules"

# Every in-tree module that follows foundation's `hooks-*` naming convention.
# Discovered dynamically so a newly added hook module is automatically
# covered by this regression guard without needing to update this list.
HOOK_MODULE_IDS = sorted(
    p.name for p in MODULES_DIR.iterdir() if p.is_dir() and p.name.startswith("hooks-")
)


@pytest.fixture
def loader() -> ModuleLoader:
    return ModuleLoader()


def test_hook_modules_were_discovered() -> None:
    """Sanity check: the dynamic discovery above must find at least the
    modules known to exist at the time this test was written, otherwise the
    parametrized tests below would silently pass on an empty set."""
    assert len(HOOK_MODULE_IDS) >= 5


@pytest.mark.parametrize("module_id", HOOK_MODULE_IDS)
def test_hook_module_classifies_as_hook(loader: ModuleLoader, module_id: str) -> None:
    """Every `hooks-*` module must classify as type='hook' via the same
    `_get_module_metadata()` amplifier-core's real loader calls at bundle
    composition time (both the top-level `hooks:` block and a behavior's
    `hooks:` block resolve modules through this exact method).

    A module classified as anything else (e.g. 'tool', because its id
    contains a keyword the naming-fallback checks before 'hook') will be
    validated with the wrong protocol validator and fail to load.
    """
    module_path = MODULES_DIR / module_id
    module_type, mount_point = loader._get_module_metadata(module_id, module_path)

    assert module_type == "hook", (
        f"'{module_id}' classified as type='{module_type}' instead of 'hook' -- "
        "its module id likely contains a keyword ('tool', 'loop', 'provider', "
        "'orchestrat', or 'context') that amplifier-core's naming-fallback "
        "heuristic matches before 'hook'. Rename the module so its id does not "
        "contain a competing keyword (see hooks-tool-dedupe -> hooks-dedupe)."
    )
    assert mount_point == "hooks"
