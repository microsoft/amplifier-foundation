"""Opt-in, commit-addressed sources shared by bundles, modules and skills.

Applications own branch bindings. The store owns immutable checkouts. Refresh
creates a new binding; it never checks out files beneath an existing reader.
Absent AMPLIFIER_SOURCE_STORE, existing resolver behavior is unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from filelock import AsyncFileLock

from amplifier_foundation.paths.resolution import ResolvedSource, parse_uri
from amplifier_foundation.sources.git import _with_longpaths


async def complete_io(function, *args, **kwargs):
    """Do not release a source writer lock while a cancelled thread is writing."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def binding_root(cache):
    cache = Path(cache)
    return cache.parent if cache.name in {"skills", "bundles"} else cache


def source_id(url, ref):
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Shared sources require credential-free repository URLs")
    url = parsed._replace(path=parsed.path.rstrip("/").removesuffix(".git")).geturl()
    return hashlib.sha256((url + "@" + ref).encode()).hexdigest()


def atomic(path, value):
    from amplifier_foundation.settings import atomic_write

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(value), private=True)


def clean_checkout(root):
    """Tracked changes and unknown extras are local work, even when ignored."""
    root = Path(root)
    if not (root / ".git").is_dir() or (root / ".git").is_symlink():
        return False  # Linked worktrees can refer to a mutable external Git dir.
    # Commit-addressed roots can exceed MAX_PATH even for Git object reads.
    dirty = subprocess.check_output(
        _with_longpaths(
            [
                "git",
                "--no-optional-locks",
                "status",
                "--porcelain",
                "--untracked-files=no",
            ]
        ),
        cwd=root,
        text=True,
    )
    extras = subprocess.check_output(
        _with_longpaths(["git", "ls-files", "--others", "-z"]), cwd=root
    ).split(b"\0")
    extras = [name for name in extras if name and name != b".amplifier_cache_meta.json"]
    return not dirty and not extras


class SharedSourceStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def checkout(self, url, revision):
        if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", revision):
            raise ValueError("An exact source revision is required")
        target = self.root / "objects" / source_id(url, "") / revision
        if target.is_symlink() or not target.resolve().is_relative_to(self.root):
            raise ValueError("Shared source object is outside its owned store")
        return target

    def verify(self, url, revision):
        target = self.checkout(url, revision)
        metadata = json.loads((target / ".amplifier_cache_meta.json").read_text())
        if (
            source_id(metadata.get("git_url", ""), "") != source_id(url, "")
            or metadata.get("commit") != revision
        ):
            raise ValueError("Shared source identity changed")
        actual = subprocess.check_output(
            _with_longpaths(["git", "rev-parse", "HEAD"]), cwd=target, text=True
        ).strip()
        if actual != revision or not clean_checkout(target):
            raise ValueError("Shared source contents changed")
        return target

    async def ensure(self, url, revision, *, existing=None):
        target = self.checkout(url, revision)
        target.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncFileLock(target.with_name("." + revision + ".lock")):
            if target.exists():
                return await asyncio.to_thread(self.verify, url, revision)
            stage = Path(tempfile.mkdtemp(prefix=".source-", dir=target.parent))
            try:
                from .git import GitSourceHandler

                if existing is not None:
                    if not await asyncio.to_thread(clean_checkout, existing):
                        raise ValueError(
                            "Only exact, clean sources may enter the shared store"
                        )
                    await complete_io(
                        shutil.copytree,
                        existing,
                        stage,
                        dirs_exist_ok=True,
                        symlinks=True,
                    )
                else:
                    await complete_io(
                        GitSourceHandler()._clone_at_commit, url, revision, stage
                    )

                def publish():
                    atomic(
                        stage / ".amplifier_cache_meta.json",
                        {
                            "git_url": url,
                            "ref": revision,
                            "commit": revision,
                            "immutable": True,
                        },
                    )
                    actual = subprocess.check_output(
                        _with_longpaths(["git", "rev-parse", "HEAD"]),
                        cwd=stage,
                        text=True,
                    ).strip()
                    dirty = subprocess.check_output(
                        _with_longpaths(
                            [
                                "git",
                                "--no-optional-locks",
                                "status",
                                "--porcelain",
                                "--untracked-files=no",
                            ]
                        ),
                        cwd=stage,
                        text=True,
                    )
                    if actual != revision or dirty or not clean_checkout(stage):
                        raise ValueError(
                            "Only exact, clean sources may enter the shared store"
                        )
                    # Builds must use writable build views, never modify this tree.
                    for path in stage.rglob("*"):
                        if not path.is_symlink():
                            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
                    stage.chmod(0o555)
                    stage.rename(target)

                await complete_io(publish)
            finally:
                if stage.exists():
                    from ._rmtree import rmtree_robust

                    await complete_io(rmtree_robust, stage)
        return target

    async def bind(self, cache, url, ref, revision, *, existing=None):
        target = await self.ensure(url, revision, existing=existing)
        path = (
            binding_root(cache) / ".source-bindings" / (source_id(url, ref) + ".json")
        )
        atomic(path, {"git_url": url, "ref": ref, "commit": revision})
        return target

    def cached(self, uri, cache):
        """Inspect cached status without creating a binding or cloning."""
        from .git import GitSourceHandler

        parsed = parse_uri(uri)
        handler = GitSourceHandler()
        url, ref = handler._build_git_url(parsed), parsed.ref or "HEAD"
        path = (
            binding_root(cache) / ".source-bindings" / (source_id(url, ref) + ".json")
        )
        legacy = handler._get_cache_path(parsed, Path(cache))
        if legacy.exists() and (not path.exists() or not clean_checkout(legacy)):
            return legacy
        if not path.exists():
            return None
        record = json.loads(path.read_text())
        if source_id(record["git_url"], record["ref"]) != source_id(url, ref):
            raise ValueError("Shared source binding changed")
        return self.verify(url, record["commit"])

    async def resolve(self, uri, cache, *, refresh=False):
        parsed = parse_uri(uri)
        from .git import GitSourceHandler

        handler = GitSourceHandler()
        url, ref = handler._build_git_url(parsed), parsed.ref or "HEAD"
        path = (
            binding_root(cache) / ".source-bindings" / (source_id(url, ref) + ".json")
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncFileLock(path.with_suffix(".lock")):
            # Binding scopes coalesce bundles/skills, but legacy directories
            # retain the handler's original cache path and any edits there.
            legacy = handler._get_cache_path(parsed, Path(cache))
            if (
                legacy.exists()
                and (
                    not path.exists()
                    or not await asyncio.to_thread(clean_checkout, legacy)
                )
                and not refresh
            ):
                active = legacy / parsed.subpath if parsed.subpath else legacy
                if (
                    not active.resolve().is_relative_to(legacy.resolve())
                    or not active.exists()
                ):
                    raise ValueError("Shared source subpath is unavailable")
                return ResolvedSource(active_path=active, source_root=legacy)
            if (
                refresh
                and legacy.exists()
                and not await asyncio.to_thread(clean_checkout, legacy)
            ):
                raise ValueError(
                    "Local changes retained; refresh requires a clean source"
                )
            if path.exists() and not refresh:
                record = json.loads(path.read_text())
                if source_id(record["git_url"], record["ref"]) != source_id(url, ref):
                    raise ValueError("Shared source binding changed")
                target = await self.ensure(url, record["commit"])
            else:
                revision = (
                    ref
                    if re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", ref)
                    else await remote_revision(url, ref)
                )
                if not revision:
                    raise ValueError("Shared source ref is unavailable")
                target = await self.bind(cache, url, ref, revision)
        active = target / parsed.subpath if parsed.subpath else target
        if not active.resolve().is_relative_to(target) or not active.exists():
            raise ValueError("Shared source subpath is unavailable")
        return ResolvedSource(active_path=active, source_root=target)


async def remote_revision(url, ref):
    """Resolve explicit branch/tag names without selecting an ambiguous match."""
    patterns = (
        [ref]
        if ref == "HEAD" or ref.startswith("refs/")
        else ["refs/heads/" + ref, "refs/tags/" + ref, "refs/tags/" + ref + "^{}"]
    )

    def read():
        output = subprocess.check_output(
            ["git", "ls-remote", url, *patterns], text=True, timeout=35
        )
        found = {
            name: revision
            for line in output.splitlines()
            for revision, name in [line.split()]
            if name in patterns
            and re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", revision)
        }
        if ref == "HEAD" or ref.startswith("refs/"):
            return found.get(ref)
        branch, tag = (
            found.get(patterns[0]),
            found.get(patterns[2]) or found.get(patterns[1]),
        )
        if branch and tag:
            raise ValueError("Ambiguous branch and tag; use an explicit refs path")
        return branch or tag

    return await asyncio.to_thread(read)


async def resolve_shared_source(uri, cache, *, refresh=False):
    root = os.environ.get("AMPLIFIER_SOURCE_STORE")
    if not root:
        raise ValueError("No shared source store configured")
    return await SharedSourceStore(root).resolve(uri, cache, refresh=refresh)


def build_view(source):
    """A stable writable build input; immutable checkout remains untouched.

    Kept outside the object store, with exact Git provenance for qualification.
    uv's wheel cache reuses artifacts; no editable package is shared by hosts.
    """
    original = Path(source)
    source = original.resolve()
    for root in (source, *source.parents):
        marker = root / ".amplifier_cache_meta.json"
        if not marker.is_file() or not (root / ".git").is_dir():
            continue
        data = json.loads(marker.read_text())
        if not data.get("immutable"):
            return original, False
        store = root.parent.parent.parent
        target = store / "builds" / source_id(data["git_url"], data["commit"])
        from filelock import FileLock

        target.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(target) + ".lock"):
            if not target.exists():
                shutil.copytree(root, target, symlinks=True)
                for path in (target, *target.rglob("*")):
                    if not path.is_symlink():
                        path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o200)
                atomic(
                    target / ".amplifier_cache_meta.json",
                    {**data, "immutable": False, "buildInput": True},
                )
        return target / source.relative_to(root), True
    return original, False


def build_lock(source):
    """A build backend may write metadata; serialize all users of its view."""
    from contextlib import nullcontext

    from filelock import FileLock

    source = Path(source).resolve()
    for root in (source, *source.parents):
        marker = root / ".amplifier_cache_meta.json"
        if marker.is_file() and (root / ".git").is_dir():
            data = json.loads(marker.read_text())
            return (
                FileLock(str(root) + ".lock")
                if data.get("buildInput")
                else nullcontext()
            )
    return nullcontext()
