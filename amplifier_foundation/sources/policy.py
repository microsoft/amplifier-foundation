"""Context-local admission of already prepared sources.

Hosts own source selection and content verification. This mechanism never fetches,
installs, creates cache bindings, or decides which source roots are trusted.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from amplifier_foundation.exceptions import BundleNotFoundError
from amplifier_foundation.paths.resolution import ResolvedSource

SOURCE_RESOLUTION_POLICY_VERSION = 1


class SourceResolutionDenied(BundleNotFoundError):
    """The active host policy refused a source before handler resolution."""


SourceAdmission = Callable[..., ResolvedSource]
_admission: ContextVar[SourceAdmission | None] = ContextVar(
    "foundation_source_admission", default=None
)


@contextmanager
def source_resolution_policy(admit: SourceAdmission) -> Iterator[None]:
    """Require admission before every SimpleSourceResolver handler.

    admit(uri, *, base_path) must synchronously verify its host-owned binding
    and return an existing absolute ResolvedSource, or raise
    SourceResolutionDenied. Returning None never enables fallback. The host is
    responsible for closure membership, file digests and requested/effective
    provenance; Foundation checks existence and canonical path containment.

    Context follows asyncio child tasks, including internally created registry
    and activator resolvers. New processes/threads require their own scope.
    An active policy cannot be replaced or disabled by a nested scope.
    """
    if not callable(admit):
        raise TypeError("Source admission must be callable")
    current = _admission.get()
    if current is not None and current is not admit:
        raise SourceResolutionDenied(
            "An active source admission policy cannot be replaced"
        )
    token = _admission.set(admit)
    try:
        yield
    finally:
        _admission.reset(token)


def admitted_source(uri: str, *, base_path: Path) -> ResolvedSource | None:
    """Return None only when no host policy is installed."""
    admit = _admission.get()
    if admit is None:
        return None
    result = admit(uri, base_path=base_path)
    if not isinstance(result, ResolvedSource):
        raise SourceResolutionDenied(
            "Source admission did not return a prepared source"
        )
    try:
        active, root = Path(result.active_path), Path(result.source_root)
        if not active.is_absolute() or not root.is_absolute():
            raise ValueError("Absolute paths required")
        active, root = active.resolve(strict=True), root.resolve(strict=True)
        if not root.is_dir() or not active.is_relative_to(root):
            raise ValueError("Source escaped its admitted root")
    except (OSError, RuntimeError, TypeError, ValueError):
        raise SourceResolutionDenied(
            "Admitted source paths are unavailable or outside their root"
        ) from None
    return ResolvedSource(active_path=active, source_root=root)


def check_prepared_source(path: Path, *, source_hint: str | None = None) -> None:
    """Admit cached paths without activating or resolving through handlers."""
    result = admitted_source(
        source_hint if source_hint is not None else str(path), base_path=path.parent
    )
    if result is not None and result.active_path != path.resolve(strict=True):
        raise SourceResolutionDenied(
            "Prepared source disagrees with its admitted binding"
        )


def source_resolution_restricted() -> bool:
    """Whether this context requires admission (not a dependency policy)."""
    return _admission.get() is not None
