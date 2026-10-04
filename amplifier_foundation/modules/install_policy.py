"""Host policy for dependency installation in qualified execution environments.

Protect a worker/probe's lifetime with ``dependency_installation_policy(False)``.
This is independent of source/cache policy and does not authorize a runtime
update. Prepare changed dependencies in a separate, mutable staging process.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from amplifier_foundation.exceptions import BundleError

_allowed: ContextVar[bool] = ContextVar(
    "foundation_dependency_install_allowed", default=True
)


class DependencyInstallationDenied(BundleError):
    """The host prohibited installation into the selected Python environment."""


def installation_allowed() -> bool:
    return _allowed.get()


@contextmanager
def dependency_installation_policy(allow_install: bool) -> Iterator[None]:
    """Restrict dependency installs in this task and inherited async work.

    Nested scopes cannot relax an outer prohibition. Activators retain the
    restriction for later lazy activation, even after this scope exits. Default
    standalone callers remain mutable. Hosts must enter this scope at each
    process boundary; ContextVars do not cross subprocesses or raw threads.
    """
    if not isinstance(allow_install, bool):
        raise TypeError("allow_install must be a bool")
    token = _allowed.set(_allowed.get() and allow_install)
    try:
        yield
    finally:
        _allowed.reset(token)


def assert_installation_allowed(python: str, *, captured_allowed: bool = True) -> None:
    if not captured_allowed or not installation_allowed():
        raise DependencyInstallationDenied(
            f"Dependency installation is prohibited for Python environment {python}. "
            "Prepare dependencies in a separate mutable staging environment."
        )
