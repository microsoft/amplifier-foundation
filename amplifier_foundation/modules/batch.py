"""Collect bundle requirements before changing an isolated Python environment.

Applications own profile selection and activation. This mechanism performs one
resolver transaction for the union of compatible module requirements. It never
mounts a session or executes a provider. Existing CLI prepare() stays unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
import tomllib
from contextlib import ExitStack
from pathlib import Path


def requirement_name(line):
    """Identify named requirements and local directory entries from uv export."""
    from urllib.parse import unquote, urlsplit

    value = line.strip().removeprefix("-e ").strip()
    if value.startswith("file:"):
        parsed = urlsplit(value.split(" ; ", 1)[0])
        try:
            metadata = tomllib.loads(
                (Path(unquote(parsed.path)) / "pyproject.toml").read_text()
            )
            return package_name(metadata.get("project", {}).get("name", ""))
        except (OSError, ValueError):
            return None  # Preserve unknown policy for the resolver to reject.
    match = re.match(r"^([A-Za-z0-9_.-]+)", value)
    return package_name(match[1]) if match else None


def package_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()


class DependencyConflict(ValueError):
    """Profiles select different sources for one Python distribution."""


class DependencyBatch:
    def __init__(self):
        self.sources = {}
        self.names = {}
        self.policy = None
        self.installed = False
        self.install_count = 0
        self._install_task = None

    def add(self, activator, source):
        if self.installed or self._install_task is not None:
            raise RuntimeError(
                "An installing or completed dependency batch cannot be extended"
            )
        if not activator.refresh_dependencies:
            raise ValueError(
                "Batch preparation requires an isolated refresh environment"
            )
        source = Path(source).resolve()
        if not activator._needs_python_install(source):
            return
        policy = (
            str(activator.install_python),
            *(
                Path(p).read_bytes() if p else None
                for p in (activator.install_constraints, activator.install_overrides)
            ),
        )
        if self.policy is not None and policy != self.policy:
            raise ValueError(
                "A dependency batch requires one interpreter and install policy"
            )
        self.policy = policy
        pyproject = source / "pyproject.toml"
        metadata = tomllib.loads(pyproject.read_text()) if pyproject.exists() else {}
        name = package_name(metadata.get("project", {}).get("name", ""))
        if name and name in self.names and self.names[name] != source:
            raise DependencyConflict(f"Conflicting sources for distribution {name}")
        if name:
            self.names[name] = source
        self.sources.setdefault(source, activator)

    @property
    def fingerprint(self):
        from .preparation import source_signature

        rows = [(str(path), source_signature(path)) for path in sorted(self.sources)]
        policy = [p.hex() if isinstance(p, bytes) else p for p in self.policy or ()]
        return hashlib.sha256(
            json.dumps([rows, policy], sort_keys=True).encode()
        ).hexdigest()

    async def install(self):
        if self.installed:
            return self.report()
        if self._install_task is None:
            self._install_task = asyncio.create_task(self._complete_install())
        try:
            return await asyncio.shield(self._install_task)
        except asyncio.CancelledError:
            # A resolver thread cannot be cancelled. Join it before the caller
            # tears down its candidate; never leave writes running in the dark.
            try:
                await asyncio.shield(self._install_task)
            finally:
                raise

    async def _complete_install(self):
        if self.sources:
            await asyncio.to_thread(self._install)
            self.install_count += 1
        self.installed = True
        return self.report()

    def report(self):
        return {
            "sources": len(self.sources),
            "packages": len(self.names),
            "resolverTransactions": self.install_count,
        }

    def _install(self):
        from amplifier_foundation.sources.shared import build_lock, build_view

        # One transaction resolves the full requirement set. Never reinstall
        # each profile in sequence: it makes cost scale with workspace count and
        # invalidates earlier whole-environment preparation receipts.
        activator = next(iter(self.sources.values()))
        cmd = [
            "uv",
            "pip",
            "install",
            "--python",
            activator.install_python,
            "--quiet",
            "--no-sources",
            "--upgrade",
        ]
        # Selected source paths and the caller's policy identify the candidate.
        # A blanket --refresh discards reusable wheels for every unchanged
        # component. uv still resolves the complete union and rebuilds changed
        # local inputs; immutable commit-addressed inputs reuse their artifacts.
        if activator.install_constraints:
            cmd.extend(["--constraints", str(activator.install_constraints)])
        # build_view itself takes the repository lock. Materialize every view
        # before holding build locks; sibling modules can share one repository.
        views = {
            source: build_view(source)
            for source in sorted(self.sources)
            if (source / "pyproject.toml").exists()
        }
        locks = {}
        for path, _ in views.values():
            lock = build_lock(path)
            locks.setdefault(getattr(lock, "lock_file", str(path)), lock)
        with ExitStack() as stack:
            overrides = []
            for identity in sorted(locks):
                stack.enter_context(locks[identity])
            if activator.install_overrides:
                for line in Path(activator.install_overrides).read_text().splitlines():
                    if requirement_name(line) not in self.names:
                        overrides.append(line)
            for source in sorted(self.sources):
                if (source / "pyproject.toml").exists():
                    path, immutable = views[source]
                    cmd.extend(([] if immutable else ["-e"]) + [str(path)])
                    metadata = tomllib.loads((source / "pyproject.toml").read_text())
                    name = metadata.get("project", {}).get("name")
                    if name:
                        # The explicit selected source must also satisfy any
                        # transitive direct URL requirement for this package.
                        overrides.append(f"{name} @ {path.as_uri()}")
                elif (source / "requirements.txt").exists():
                    cmd.extend(["-r", str(source / "requirements.txt")])
            if overrides:
                directory = stack.enter_context(tempfile.TemporaryDirectory())
                policy = Path(directory) / "overrides.txt"
                policy.write_text("\n".join(overrides) + "\n")
                cmd.extend(["--overrides", str(policy)])
            environment = dict(os.environ)
            # The explicit policy above incorporates the caller's overrides.
            # uv combines UV_OVERRIDE with --overrides; inheriting the original
            # file would reintroduce superseded URLs for selected root packages.
            if activator.install_overrides:
                environment.pop("UV_OVERRIDE", None)
            subprocess.run(
                cmd, check=True, capture_output=True, text=True, env=environment
            )
        # Only successful whole-batch resolution establishes install evidence.
        for source, owner in self.sources.items():
            owner._install_state.mark_installed(source)
        for owner in {id(owner): owner for owner in self.sources.values()}.values():
            owner.finalize()
