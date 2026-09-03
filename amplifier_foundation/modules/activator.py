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
import platform
import site
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from amplifier_foundation.exceptions import BundleError
from amplifier_foundation.modules.install_state import InstallStateManager
from amplifier_foundation.paths.resolution import get_amplifier_home, parse_uri
from amplifier_foundation.sources.resolver import SimpleSourceResolver

logger = logging.getLogger(__name__)


def _distribution_installed(pkg_name: str) -> bool:
    """Return True if a distribution named ``pkg_name`` is installed.

    Keys on the distribution name via ``importlib.metadata`` rather than guessing
    an import name from ``pkg_name.replace("-", "_")``. Both editable and wheel
    installs register a queryable ``.dist-info``, and ``uv sync`` removing an
    editable install also removes that metadata. This correctly answers "is it
    installed?" for bundles whose import package differs from their distribution
    name (e.g. ``amplifier-bundle-evaluation`` -> ``amplifier_evaluation``) or
    that ship no import package at all (``packages=[]``), both of which the old
    import-name guess mis-detected as "not installed" and rebuilt on every
    process. See issue #326.
    """
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        distribution(pkg_name)
        return True
    except PackageNotFoundError:
        return False


class BundlePackageInstallError(BundleError):
    """A bundle's own root Python package could not be installed.

    Raised by :meth:`ModuleActivator.activate_bundle_package` so the failure names
    the bundle that OWNS the offending ``pyproject.toml`` -- not whichever bundle
    happened to be preparing when the install ran. Without this attribution the
    user sees ``Failed to load bundle 'foundation'`` for a package that belongs to
    an unrelated ``--app`` bundle they added an hour ago.
    """

    def __init__(self, bundle_path: Path, package: str, reason: str) -> None:
        self.bundle_path = bundle_path
        self.package = package
        self.reason = reason
        super().__init__(
            f"Could not install the root Python package '{package or bundle_path.name}' "
            f"of bundle at {bundle_path}: {reason}\n"
            f"That package is installed only because a module declared by the bundle "
            f"resolves inside that directory. If this bundle was added with "
            f"`amplifier bundle add`, `amplifier bundle remove <name>` restores sessions."
        )


def bundle_root_declares_module(
    bundle_path: Path, module_sources: Iterable[str]
) -> bool:
    """Does at least one declared module ``source`` resolve INSIDE ``bundle_path``?

    This is the question :meth:`ModuleActivator.activate_bundle_package` exists to
    serve -- "modules that import from their parent bundle's package" -- asked of
    the modules actually declared, rather than inferred from the mere presence of a
    ``pyproject.toml`` with a ``[project]`` table. A skills-only behavior shipped
    from a Python *application* repo has a ``[project]`` table (the application) but
    declares no module that lives there; installing the application into the
    Amplifier environment is never what its author meant, and when the package
    cannot install (``requires-python`` above the running interpreter) every session
    on the machine fails at bundle preparation.

    Two source shapes count as "inside":

    * Local paths. Relative ``./`` and ``../`` sources are rewritten to absolute
      paths at load time (``_dataclass._resolve_relative_sources``), so a plain
      ``Path(source).resolve().is_relative_to(bundle_path)`` is exact.
    * ``git+`` sources whose repo AND ref hash to the same cache directory as
      ``bundle_path`` -- the same pure computation the git handler uses to place
      clones (``GitSourceHandler._get_cache_path``), evaluated against the cache
      directory the bundle itself was fetched into (``bundle_path.parent``). A
      ``#subdirectory=modules/x`` module of the same repo therefore matches; a
      module fetched from any other repo does not.

    Anything unparseable is treated as "not inside" -- the conservative answer,
    because the cost of a false positive here is a machine-wide outage while the
    cost of a false negative is one module failing to import, loudly, by name.
    """
    try:
        root = bundle_path.resolve()
    except OSError:
        return False
    git_handler = None
    for source in module_sources:
        if not isinstance(source, str) or not source:
            continue
        try:
            parsed = parse_uri(source)
        except Exception as exc:  # noqa: BLE001
            # An unparseable source is simply "not ours" -- the conservative answer.
            logger.debug(f"Ignoring unparseable module source {source!r}: {exc}")
            continue
        if parsed.is_git:
            if git_handler is None:
                from amplifier_foundation.sources.git import GitSourceHandler

                git_handler = GitSourceHandler()
            try:
                if git_handler._get_cache_path(parsed, root.parent).resolve() == root:
                    return True
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    f"Could not place git source {source!r} in the cache: {exc}"
                )
            continue
        if parsed.is_file:
            raw = source.removeprefix("file://")
            try:
                candidate = Path(raw).expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            if candidate == root or candidate.is_relative_to(root):
                return True
    return False


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
        strict: bool = False,
    ) -> None:
        """Initialize module activator.

        Args:
            cache_dir: Directory for caching downloaded modules.
            install_deps: Whether to install Python dependencies.
            base_path: Base path for resolving relative module paths.
                       For bundles loaded from git, this should be the cloned
                       bundle's base_path so relative paths like ./modules/foo
                       resolve correctly.
            strict: If True, activation failures in activate_all() raise
                    ModuleActivationError instead of being logged and skipped.
                    Mirrors BundleRegistry(strict=...) for include failures.
        """
        self.cache_dir = cache_dir or get_amplifier_home() / "cache"
        self.install_deps = install_deps
        self.strict = strict
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

        Raises:
            ModuleActivationError: If strict=True and any module fails to
                activate. All failures are reported together, not just the
                first one.
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
            failures: list[tuple[str, BaseException]] = []
            for (name, _), result in zip(to_activate, results):
                if isinstance(result, BaseException):
                    failures.append((name, result))
                    logger.error(f"Failed to activate {name}: {result}")
                else:
                    activated[name] = result

            if failures and self.strict:
                detail = "\n".join(f"  - {name}: {err}" for name, err in failures)
                raise ModuleActivationError(
                    f"{len(failures)} of {len(to_activate)} modules failed to "
                    f"activate (strict mode):\n{detail}"
                ) from failures[0][1]

            return activated

        return {}

    async def activate_bundle_package(
        self,
        bundle_path: Path,
        progress_callback: Callable[[str, str], None] | None = None,
        *,
        module_sources: Iterable[str] | None = None,
    ) -> None:
        """Install a bundle's own Python package to enable internal imports.

        When a bundle contains both a Python package (pyproject.toml at root) and
        modules that import from that package, we need to install the bundle's
        package BEFORE activating modules. This enables patterns like:

            # In modules/tool-<name>/__init__.py
            from amplifier_bundle_<name> import SomeHelper

        where amplifier_bundle_<name> is the bundle's own package.

        Args:
            bundle_path: Path to bundle root directory containing pyproject.toml.
            module_sources: The ``source`` strings of every module the bundle
                declares. When given, the package is installed ONLY if at least
                one of them resolves inside ``bundle_path`` (see
                :func:`bundle_root_declares_module`) -- a root ``pyproject.toml``
                alone is not evidence that any module imports from it. ``None``
                preserves the historical behavior (install whenever the pyproject
                declares a package) for callers that cannot supply the list.

        Raises:
            BundlePackageInstallError: the package's ``requires-python`` excludes
                the running interpreter, or the install itself failed. Either way
                the error names THIS bundle root and package.

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

        # A [project] table proves the repo ships a Python package. It does not
        # prove any module in this bundle imports from it -- an application repo
        # that ships a skills-only behavior has a [project] table for the
        # application. Only install when a declared module actually lives here.
        if module_sources is not None and not bundle_root_declares_module(
            bundle_path, module_sources
        ):
            logger.info(
                f"Skipping root package install for bundle at {bundle_path}: none of the "
                f"bundle's declared modules resolve inside it, so its pyproject describes "
                f"an application, not a module dependency."
            )
            return

        # Skip packages that are already installed in the current environment.
        # This prevents editable-installing packages (like amplifier-core) that were
        # already installed from PyPI as prebuilt wheels. Without this check, a repo
        # cloned into the cache for its YAML/context files (via bundle includes) would
        # trigger a source build that may require native toolchains (Rust, protobuf, etc).
        #
        # Detection keys on the distribution name (importlib.metadata), NOT a guessed
        # import name. See _distribution_installed() and issue #326.
        pkg_name = pyproject_data.get("project", {}).get("name", "")
        if pkg_name and _distribution_installed(pkg_name):
            logger.debug(
                f"Package '{pkg_name}' already installed, "
                f"skipping editable install from {bundle_path}"
            )
            return

        # Fail with a sentence, not a resolver transcript: if the package's own
        # requires-python excludes the interpreter Amplifier runs on, uv will refuse
        # anyway -- say so first, naming the bundle, before spawning it.
        requires_python = str(
            pyproject_data.get("project", {}).get("requires-python", "")
        ).strip()
        if requires_python:
            try:
                from packaging.specifiers import SpecifierSet
            except ImportError:
                # `packaging` is not a declared dependency; without it the check is
                # skipped and uv's own resolver error is surfaced (attributed) below.
                SpecifierSet = None  # type: ignore[assignment]
            if SpecifierSet is not None:
                running = platform.python_version()
                if not SpecifierSet(requires_python).contains(
                    running, prereleases=True
                ):
                    raise BundlePackageInstallError(
                        bundle_path,
                        pkg_name,
                        f"it requires Python {requires_python} but this Amplifier "
                        f"environment runs Python {running}",
                    )

        if progress_callback:
            progress_callback("installing_package", pkg_name or bundle_path.name)
        logger.debug(f"Installing bundle package from {bundle_path}")
        try:
            await self._install_dependencies(bundle_path)
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or e.stdout or "").strip()
            raise BundlePackageInstallError(
                bundle_path,
                pkg_name,
                f"`uv pip install -e` exited {e.returncode}"
                + (f"\n{detail}" if detail else ""),
            ) from e
        except FileNotFoundError as e:
            raise BundlePackageInstallError(
                bundle_path, pkg_name, "uv is not installed"
            ) from e

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

    @staticmethod
    def _needs_python_install(module_path: Path) -> bool:
        """Check if this module needs Python (pip/uv) installation.

        Reads the module's amplifier.toml to determine its transport type.
        Non-Python transports (rust, wasm, grpc) ship pre-built binaries and
        don't need a Python build step.

        Args:
            module_path: Path to the module directory.

        Returns:
            True if Python installation is needed, False if it should be skipped.
        """
        amplifier_toml = module_path / "amplifier.toml"
        if not amplifier_toml.exists():
            # Legacy Python module — no amplifier.toml means assume Python install
            return True
        try:
            import tomllib

            with open(amplifier_toml, "rb") as f:
                data = tomllib.load(f)
            transport = data.get("module", {}).get("transport", "python")
            if transport in ("rust", "wasm", "grpc"):
                logger.debug(
                    f"Module {module_path.name} uses {transport!r} transport, "
                    "skipping Python install"
                )
                return False
        except Exception:
            logger.warning(
                "Could not parse amplifier.toml in %s, assuming Python install",
                module_path,
                exc_info=True,
            )
        return True

    async def _install_dependencies(
        self,
        module_path: Path,
        module_name: str | None = None,
        progress_callback: Callable[[str, str], None] | None = None,
        force: bool = False,
    ) -> None:
        """Install Python dependencies for a module.

        Uses uv to install into the current Python environment. The --python flag
        ensures installation targets the correct environment even when run via
        `uv tool install` where there's no active virtualenv.

        Skips installation if the package is already importable in the current
        environment (e.g. installed from PyPI as a prebuilt wheel), or if the module
        is already installed with a matching fingerprint.

        Args:
            module_path: Path to the module directory.
            module_name: Optional human-readable module name for progress reporting.
            progress_callback: Optional callback(action, detail) for progress reporting.
            force: If True, skip all early-exit checks and force a reinstall from source.
                Use this only when you specifically need to rebuild (e.g. a --force flag
                on update). When False (default), packages already importable from the
                current environment are never editable-installed from source.

        Raises:
            subprocess.CalledProcessError: If installation fails.
        """
        # Non-Python modules (rust/wasm/grpc) ship pre-built binaries and
        # don't need pip/uv installation.
        if not self._needs_python_install(module_path):
            return

        if not force:
            # Skip packages that are already importable in the current environment.
            # This prevents editable-installing packages (like amplifier-core) that were
            # already installed from PyPI as prebuilt wheels. Without this check, a repo
            # cloned into the cache for its YAML/context files would trigger a source
            # build that may require native toolchains (Rust, protobuf, etc).
            #
            # This guard is the definitive check regardless of which caller invoked us
            # (activate_bundle_package, update_bundle, or any future caller). The guard
            # in activate_bundle_package() is kept as belt-and-suspenders but this one
            # is authoritative.
            pyproject = module_path / "pyproject.toml"
            if pyproject.exists():
                try:
                    import tomllib

                    with open(pyproject, "rb") as f:
                        data = tomllib.load(f)
                    pkg_name = data.get("project", {}).get("name", "")
                    if pkg_name and _distribution_installed(pkg_name):
                        logger.debug(
                            f"Package '{pkg_name}' already installed from wheels, "
                            f"skipping editable install from {module_path}"
                        )
                        return
                except Exception:
                    pass  # If we can't check, proceed with install

        # Check if already installed with matching fingerprint
        if not force and self._install_state.is_installed(module_path):
            # Cross-check: verify the package is still actually importable.
            # `uv tool upgrade` runs `uv sync` which can remove editable installs
            # without changing the Python symlink mtime, leaving a stale cache
            # entry that causes _install_dependencies() to skip reinstallation.
            _stale = False
            _pyproject = module_path / "pyproject.toml"
            if _pyproject.exists():
                try:
                    import tomllib

                    with open(_pyproject, "rb") as f:
                        _data = tomllib.load(f)
                    _pkg_name = _data.get("project", {}).get("name", "")
                    if _pkg_name and not _distribution_installed(_pkg_name):
                        logger.debug(
                            f"Package '{_pkg_name}' no longer installed "
                            f"(removed by uv sync?), invalidating cache "
                            f"for {module_path.name}"
                        )
                        self._install_state.invalidate(module_path)
                        _stale = True
                except Exception:
                    pass
            if not _stale:
                logger.debug(
                    f"Skipping install for {module_path.name} (already installed)"
                )
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


class ModuleActivationError(BundleError):
    """Raised when module activation fails.

    Subclasses BundleError so that callers already handling bundle
    preparation failures render this cleanly instead of letting it
    escape as an unhandled traceback.
    """
