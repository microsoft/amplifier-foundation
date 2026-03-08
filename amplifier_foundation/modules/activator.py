"""Module activation for amplifier-foundation.

This module provides basic module resolution - downloading modules from URIs
and making them importable. This enables foundation to provide a turn-key
experience where bundles can be loaded and executed without additional libraries.

For advanced resolution strategies (layered resolution, settings-based overrides,
workspace conventions), see amplifier-module-resolution.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import site
import subprocess
import sys
from pathlib import Path
from typing import Callable

from amplifier_foundation.modules.install_state import InstallStateManager
from amplifier_foundation.paths.resolution import get_amplifier_home
from amplifier_foundation.sources.resolver import SimpleSourceResolver

logger = logging.getLogger(__name__)


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

    async def activate(
        self,
        module_name: str,
        source_uri: str,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> Path:
        """Activate a module by downloading and making it importable.

        Args:
            module_name: Name of the module (e.g., "loop-streaming").
            source_uri: URI to download from (e.g., "git+https://...").
            progress_callback: Optional callback(action, detail) for progress reporting.
                Called with ("activating", module_name) at start, and
                ("installing", module_name) if dependency installation is needed.

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

        if progress_callback:
            progress_callback("activating", module_name)

        # Download module source
        resolved = await self._resolver.resolve(source_uri)
        module_path = resolved.active_path

        # Install dependencies if requested
        if self.install_deps:
            await self._install_dependencies(
                module_path, module_name, progress_callback
            )

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

    async def activate_all(
        self,
        modules: list[dict],
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> dict[str, Path]:
        """Activate multiple modules with parallelization.

        Args:
            modules: List of module specs with 'module' and 'source' keys.
            progress_callback: Optional callback(action, detail) for progress reporting.
                Passed through to individual activate() calls.

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
            tasks = [
                self.activate(name, uri, progress_callback=progress_callback)
                for name, uri in to_activate
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            activated = {}
            for (name, _), result in zip(to_activate, results):
                if isinstance(result, Exception):
                    logger.error(f"Failed to activate {name}: {result}")
                else:
                    activated[name] = result
            return activated

        return {}

    async def activate_bundle_package(
        self,
        bundle_path: Path,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> None:
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

        # Skip packages that are already importable in the current environment.
        # This prevents editable-installing packages (like amplifier-core) that were
        # already installed from PyPI as prebuilt wheels. Without this check, a repo
        # cloned into the cache for its YAML/context files (via bundle includes) would
        # trigger a source build that may require native toolchains (Rust, protobuf, etc).
        pkg_name = pyproject_data.get("project", {}).get("name", "")
        if pkg_name:
            import importlib.util

            normalized = pkg_name.replace("-", "_")
            if importlib.util.find_spec(normalized) is not None:
                logger.debug(
                    f"Package '{pkg_name}' already installed, "
                    f"skipping editable install from {bundle_path}"
                )
                return

        if progress_callback:
            progress_callback("installing_package", pkg_name or bundle_path.name)
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

        # Also handle lib/ layout (e.g. [tool.hatch.build.targets.wheel] packages = ["lib/..."]).
        # Some bundles (e.g. amplifier-bundle-execution-environments) place their shared
        # package under lib/ instead of src/, so we need to cover both conventions.
        lib_dir = bundle_path / "lib"
        if lib_dir.exists() and lib_dir.is_dir():
            lib_path_str = str(lib_dir)
            if lib_path_str not in sys.path:
                sys.path.insert(0, lib_path_str)
                logger.debug(f"Added bundle lib directory to sys.path: {lib_path_str}")
            if lib_path_str not in self._bundle_package_paths:
                self._bundle_package_paths.append(lib_path_str)

    @staticmethod
    def _build_git_dep_overrides(pyproject_path: Path) -> list[str]:
        """Build override specs for git URL dependencies that are already installed.

        Modules may declare dependencies as direct git URLs in [project.dependencies],
        e.g. ``amplifier-core @ git+https://...``.  When uv resolves these, it fetches
        from git and builds from source — which fails for packages that need native
        toolchains (Rust, protobuf).  If the package is already installed (e.g. from
        PyPI as a prebuilt wheel), we generate an override that pins it to the
        installed version so uv never attempts the git fetch.

        Returns a list of ``"name==version"`` strings suitable for a uv overrides file.
        """
        import importlib.metadata

        import tomllib

        try:
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            return []

        deps = data.get("project", {}).get("dependencies", [])
        overrides: list[str] = []
        for dep in deps:
            if "git+" not in dep:
                continue
            # Extract package name from "name @ git+https://..." or "name@ git+..."
            pkg_name = dep.split("@")[0].strip()
            if not pkg_name:
                continue
            normalized = pkg_name.replace("-", "_")
            try:
                version = importlib.metadata.version(normalized)
                overrides.append(f"{pkg_name}=={version}")
                logger.debug(
                    f"Overriding git dependency '{pkg_name}' with "
                    f"installed version {version}"
                )
            except importlib.metadata.PackageNotFoundError:
                pass  # Not installed — let uv resolve normally
        return overrides

    async def _install_dependencies(
        self,
        module_path: Path,
        module_name: str | None = None,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        """Install Python dependencies for a module.

        Uses uv to install into the current Python environment. The --python flag
        ensures installation targets the correct environment even when run via
        `uv tool install` where there's no active virtualenv.

        Skips installation if the module is already installed with matching fingerprint.

        Args:
            module_path: Path to the module directory.
            module_name: Optional human-readable module name for progress reporting.
            progress_callback: Optional callback(action, detail) for progress reporting.

        Raises:
            subprocess.CalledProcessError: If installation fails.
        """
        # Check if already installed with matching fingerprint
        if self._install_state.is_installed(module_path):
            logger.debug(f"Skipping install for {module_path.name} (already installed)")
            return

        if progress_callback and module_name:
            progress_callback("installing", module_name)

        # Check for pyproject.toml or requirements.txt
        pyproject = module_path / "pyproject.toml"
        requirements = module_path / "requirements.txt"

        if pyproject.exists():
            # Build overrides for git URL dependencies that are already installed.
            # This prevents uv from fetching/building packages from git when a
            # prebuilt wheel is already available (e.g. amplifier-core from PyPI).
            overrides = self._build_git_dep_overrides(pyproject)
            overrides_file = None
            try:
                cmd = [
                    "uv",
                    "pip",
                    "install",
                    "-e",
                    str(module_path),
                    "--python",
                    sys.executable,
                    "--quiet",
                    # Ignore [tool.uv.sources] in the package's pyproject.toml.
                    # Modules use this section for dev convenience (pointing
                    # amplifier-core to git), but at runtime the PyPI wheel is
                    # already installed. Without this flag, uv would try to
                    # build amplifier-core from git source, which requires
                    # native toolchains (Rust, protobuf) that users don't have.
                    "--no-sources",
                ]
                if overrides:
                    import tempfile

                    overrides_file = tempfile.NamedTemporaryFile(
                        mode="w", suffix=".txt", delete=False
                    )
                    overrides_file.write("\n".join(overrides))
                    overrides_file.close()
                    cmd.extend(["--overrides", overrides_file.name])

                subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                # Mark as installed after successful install
                self._install_state.mark_installed(module_path)
                # Refresh Python's package discovery so subprocess-installed packages
                # (e.g. editable installs that write .pth files into site-packages) are
                # immediately visible to the current process without requiring a restart.
                importlib.invalidate_caches()
                for site_dir in site.getsitepackages():
                    site.addsitedir(site_dir)
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
            finally:
                if overrides_file is not None:
                    Path(overrides_file.name).unlink(missing_ok=True)
        elif requirements.exists():
            try:
                subprocess.run(
                    [
                        "uv",
                        "pip",
                        "install",
                        "-r",
                        str(requirements),
                        "--python",
                        sys.executable,
                        "--quiet",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                # Mark as installed after successful install
                self._install_state.mark_installed(module_path)
                # Refresh Python's package discovery so subprocess-installed packages
                # (e.g. editable installs that write .pth files into site-packages) are
                # immediately visible to the current process without requiring a restart.
                importlib.invalidate_caches()
                for site_dir in site.getsitepackages():
                    site.addsitedir(site_dir)
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
