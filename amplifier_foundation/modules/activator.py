"""Module activation for amplifier-foundation.

This module provides basic module resolution - downloading modules from URIs
and making them importable. This enables foundation to provide a turn-key
experience where bundles can be loaded and executed without additional libraries.

For advanced resolution strategies (layered resolution, settings-based overrides,
workspace conventions), see amplifier-module-resolution.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path
from weakref import WeakKeyDictionary

from amplifier_foundation.modules.install_state import InstallStateManager
from amplifier_foundation.paths.resolution import get_amplifier_home
from amplifier_foundation.sources.resolver import SimpleSourceResolver

logger = logging.getLogger(__name__)

# Wall-clock bound (seconds) for a single ``uv`` install invocation. This is only
# meaningful because installs are now run via a non-blocking async subprocess:
# a timeout can never fire while the event loop is frozen inside a synchronous
# subprocess.run(). Generous enough for a from-source editable build; can be
# lifted into config later if this module grows a config surface.
DEFAULT_INSTALL_TIMEOUT = 300


class ModuleInstallTimeout(RuntimeError):
    """Raised when a module install exceeds DEFAULT_INSTALL_TIMEOUT seconds.

    Fails loud instead of hanging: the previous synchronous ``subprocess.run()``
    had no timeout, so a wedged ``uv`` install would freeze the entire event loop
    (and any liveness heartbeat) until the worker was reaped.
    """


# Per-event-loop install lock registry.
#
# Making installs non-blocking means activate_all()'s ``asyncio.gather()`` can,
# for the first time, run multiple editable ``uv pip install`` invocations
# *truly* concurrently into the SAME interpreter environment. Concurrent editable
# installs race on site-packages metadata (.pth files, RECORD, dist-info), so we
# serialize the install critical section with an ``asyncio.Lock``.
#
# The lock is created lazily *inside the running loop* and keyed by that loop, so
# it never binds to a loop at import time and never triggers cross-loop
# "bound to a different event loop" errors when foundation spawns child sessions
# that each drive their own event loop. Entries are weakly held: when a loop is
# garbage-collected its lock disappears automatically.
_install_locks: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    WeakKeyDictionary()
)


def _get_install_lock() -> asyncio.Lock:
    """Return the install lock bound to the currently running event loop.

    Must be called from within a running coroutine (installs always are).
    """
    loop = asyncio.get_running_loop()
    lock = _install_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _install_locks[loop] = lock
    return lock


async def _run_install(
    cmd: list[str], *, timeout: float = DEFAULT_INSTALL_TIMEOUT
) -> None:
    """Run a ``uv`` install command without blocking the event loop.

    Preserves the error surface of the previous synchronous
    ``subprocess.run(..., check=True, capture_output=True, text=True)`` call:

    * a non-zero exit raises ``subprocess.CalledProcessError`` carrying the
      captured stdout/stderr, and
    * a missing ``uv`` binary still raises ``FileNotFoundError``.

    Adds a wall-clock ``timeout``: on expiry the child is killed, reaped, and a
    typed :class:`ModuleInstallTimeout` is raised so the caller fails loud
    instead of hanging.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await proc.wait()  # reap the killed child so we don't leak a zombie
        except ProcessLookupError:
            pass
        raise ModuleInstallTimeout(
            f"Install exceeded {timeout}s and was killed: {' '.join(cmd)}"
        ) from None

    if proc.returncode != 0:
        # Match the old check=True behavior: surface a CalledProcessError whose
        # stdout/stderr are decoded text, exactly as capture_output+text gave.
        raise subprocess.CalledProcessError(
            proc.returncode or -1,
            cmd,
            output=stdout_b.decode(errors="replace") if stdout_b else "",
            stderr=stderr_b.decode(errors="replace") if stderr_b else "",
        )


class ModuleActivator:
    """Activate modules by downloading and making them importable.

    This class handles the basic mechanism of:
    1. Downloading module source from git/file/http URIs
    2. Installing Python dependencies (via uv or pip)
    3. Adding module paths to sys.path for import

    Apps provide the policy (which modules to load, from where).
    This class provides the mechanism (how to load them).
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        install_deps: bool = True,
        base_path: Path | None = None,
    ) -> None:
        """Initialize module activator.

        Args:
            cache_dir: Directory for caching downloaded modules.
            install_deps: Whether to install Python dependencies.
            base_path: Base path for resolving relative module paths.
                       For bundles loaded from git, this should be the cloned
                       bundle's base_path so relative paths like ./modules/foo
                       resolve correctly.
        """
        self.cache_dir = cache_dir or get_amplifier_home() / "cache"
        self.install_deps = install_deps
        self._resolver = SimpleSourceResolver(
            cache_dir=self.cache_dir, base_path=base_path
        )
        self._install_state = InstallStateManager(self.cache_dir)
        self._activated: set[str] = set()
        # Track bundle package paths added to sys.path for inheritance by child sessions
        self._bundle_package_paths: list[str] = []

    async def activate(self, module_name: str, source_uri: str) -> Path:
        """Activate a module by downloading and making it importable.

        Args:
            module_name: Name of the module (e.g., "loop-streaming").
            source_uri: URI to download from (e.g., "git+https://...").

        Returns:
            Local path to the activated module.

        Raises:
            ModuleActivationError: If activation fails.
        """
        # Skip if already activated this session
        cache_key = f"{module_name}:{source_uri}"
        if cache_key in self._activated:
            resolved = await self._resolver.resolve(source_uri)
            return resolved.active_path

        # Download module source
        resolved = await self._resolver.resolve(source_uri)
        module_path = resolved.active_path

        # Install dependencies if requested
        if self.install_deps:
            await self._install_dependencies(module_path)

        # Add to sys.path if not already there
        path_str = str(module_path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)

        self._activated.add(cache_key)
        return module_path

    @property
    def bundle_package_paths(self) -> list[str]:
        """Get list of bundle package paths added to sys.path.

        These paths need to be shared with child sessions during spawning
        to ensure bundle packages remain importable.
        """
        return list(self._bundle_package_paths)

    async def activate_all(self, modules: list[dict]) -> dict[str, Path]:
        """Activate multiple modules with parallelization.

        Args:
            modules: List of module specs with 'module' and 'source' keys.

        Returns:
            Dict mapping module names to their local paths.
        """
        # Phase 1: Resolve all sources and check install state
        to_activate = []
        for mod in modules:
            module_name = mod.get("module")
            source_uri = mod.get("source")
            if not module_name or not source_uri:
                continue
            to_activate.append((module_name, source_uri))

        # Phase 2: Parallel activation
        if to_activate:
            tasks = [self.activate(name, uri) for name, uri in to_activate]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            activated = {}
            for (name, _), result in zip(to_activate, results):
                if isinstance(result, Exception):
                    logger.error(f"Failed to activate {name}: {result}")
                else:
                    activated[name] = result
            return activated

        return {}

    async def activate_bundle_package(self, bundle_path: Path) -> None:
        """Install a bundle's own Python package to enable internal imports.

        When a bundle contains both a Python package (pyproject.toml at root) and
        modules that import from that package, we need to install the bundle's
        package BEFORE activating modules. This enables patterns like:

            # In modules/tool-shadow/__init__.py
            from amplifier_bundle_shadow import ShadowManager

        where amplifier_bundle_shadow is the bundle's own package.

        Args:
            bundle_path: Path to bundle root directory containing pyproject.toml.

        Note:
            This is a no-op if the bundle has no pyproject.toml.
            Must be called BEFORE activate_all() for modules that need it.
        """
        if not bundle_path or not bundle_path.exists():
            return

        pyproject = bundle_path / "pyproject.toml"
        if not pyproject.exists():
            logger.debug(
                f"No pyproject.toml at {bundle_path}, skipping bundle package install"
            )
            return

        # Check if pyproject.toml actually defines an installable package.
        # Bundles may have a root pyproject.toml with only [tool.*] sections
        # for ruff/pyright/pytest configuration — these are NOT installable.
        import tomllib

        with open(pyproject, "rb") as f:
            pyproject_data = tomllib.load(f)

        if "project" not in pyproject_data and "build-system" not in pyproject_data:
            logger.debug(
                f"pyproject.toml at {bundle_path} has no [project] or [build-system], "
                "skipping bundle package install (tool-config only)"
            )
            return

        logger.debug(f"Installing bundle package from {bundle_path}")
        await self._install_dependencies(bundle_path)

        # CRITICAL: Also add bundle's src/ directory to sys.path explicitly.
        # Editable installs (uv pip install -e) create .pth files or importlib finders,
        # but these mechanisms don't reliably propagate to child sessions spawned via
        # the task tool. By explicitly adding to sys.path and tracking the path,
        # we ensure child sessions can inherit these paths during spawning.
        src_dir = bundle_path / "src"
        if src_dir.exists() and src_dir.is_dir():
            src_path_str = str(src_dir)
            if src_path_str not in sys.path:
                sys.path.insert(0, src_path_str)
                logger.debug(f"Added bundle src directory to sys.path: {src_path_str}")
            if src_path_str not in self._bundle_package_paths:
                self._bundle_package_paths.append(src_path_str)

    async def _install_dependencies(self, module_path: Path) -> None:
        """Install Python dependencies for a module.

        Uses uv to install into the current Python environment. The --python flag
        ensures installation targets the correct environment even when run via
        `uv tool install` where there's no active virtualenv.

        Skips installation if the module is already installed with matching fingerprint.

        Args:
            module_path: Path to the module directory.

        Raises:
            subprocess.CalledProcessError: If installation fails.
        """
        # Check if already installed with matching fingerprint
        if self._install_state.is_installed(module_path):
            logger.debug(f"Skipping install for {module_path.name} (already installed)")
            return

        # Check for pyproject.toml or requirements.txt
        pyproject = module_path / "pyproject.toml"
        requirements = module_path / "requirements.txt"

        if pyproject.exists():
            cmd = [
                "uv",
                "pip",
                "install",
                "-e",
                str(module_path),
                "--python",
                sys.executable,
                "--quiet",
            ]
            try:
                # Serialize the install + state-mark critical section per event
                # loop so newly-concurrent gather() installs cannot corrupt shared
                # site-packages metadata. The subprocess itself is non-blocking, so
                # the loop (and any heartbeat) keeps running while uv works.
                async with _get_install_lock():
                    await _run_install(cmd)
                    # Mark as installed after successful install
                    self._install_state.mark_installed(module_path)
            except subprocess.CalledProcessError as e:
                logger.error(
                    f"Failed to install module from {module_path}.\nstdout: {e.stdout}\nstderr: {e.stderr}"
                )
                raise
            except FileNotFoundError:
                logger.error(
                    "uv is not installed. Please install uv: https://docs.astral.sh/uv/getting-started/installation/"
                )
                raise
        elif requirements.exists():
            cmd = [
                "uv",
                "pip",
                "install",
                "-r",
                str(requirements),
                "--python",
                sys.executable,
                "--quiet",
            ]
            try:
                # Serialize the install + state-mark critical section per event
                # loop so newly-concurrent gather() installs cannot corrupt shared
                # site-packages metadata. The subprocess itself is non-blocking, so
                # the loop (and any heartbeat) keeps running while uv works.
                async with _get_install_lock():
                    await _run_install(cmd)
                    # Mark as installed after successful install
                    self._install_state.mark_installed(module_path)
            except subprocess.CalledProcessError as e:
                logger.error(
                    f"Failed to install requirements from {requirements}.\nstdout: {e.stdout}\nstderr: {e.stderr}"
                )
                raise
            except FileNotFoundError:
                logger.error(
                    "uv is not installed. Please install uv: https://docs.astral.sh/uv/getting-started/installation/"
                )
                raise

    def finalize(self) -> None:
        """Save any pending state changes.

        Should be called at the end of module activation to persist
        the install state to disk.
        """
        self._install_state.save()


class ModuleActivationError(Exception):
    """Raised when module activation fails."""

    pass
