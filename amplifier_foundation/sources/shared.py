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

from filelock import AsyncFileLock, FileLock

from amplifier_foundation.paths.resolution import ResolvedSource, parse_uri
from amplifier_foundation.sources.git import _with_longpaths


async def complete_io(function, *args, **kwargs):
    """Do not release a source writer lock while a cancelled thread is writing."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            # A second cancellation must not cancel the drain or release custody.
            cancelled = cancelled or error
        except BaseException:
            break  # Retrieve the thread's exception below.
    try:
        result = task.result()
    except BaseException as error:
        if cancelled is not None:
            cancelled.add_note(f"Source I/O failed while settling cancellation: {error!r}")
            raise cancelled from error
        raise
    if cancelled is not None:
        raise cancelled
    return result


def _remove_owned_stage(path, identity):
    """Remove only our failed stage, including directories frozen before failure."""
    from ._rmtree import rmtree_robust

    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(current.st_mode) or (
        current.st_dev, current.st_ino
    ) != identity:
        raise OSError(f"Source cleanup ownership changed; retained {path}")
    # POSIX unlink needs a writable parent, not a writable file. Walk top-down
    # without following symlinks, and leave external symlink destinations alone.
    for root, _, _ in os.walk(path, followlinks=False):
        directory = Path(root)
        directory.chmod(stat.S_IMODE(directory.stat().st_mode) | 0o700)
    rmtree_robust(path)


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


def _index_refresh_owned(root):
    """Optional index writes require ordinary metadata wholly inside this copy."""
    git_dir = root / ".git"
    for name in ("commondir", "gitdir", "objects/info/alternates", "objects/info/http-alternates"):
        if (git_dir / name).exists() or (git_dir / name).is_symlink():
            return False
    for parent, directories, files in os.walk(git_dir, followlinks=False):
        if any((Path(parent) / name).is_symlink() for name in directories + files):
            return False
    paths = subprocess.check_output(
        _with_longpaths([
            "git", "rev-parse", "--show-toplevel", "--absolute-git-dir",
            "--git-common-dir", "--git-path", "index", "--git-path", "objects",
        ]), cwd=root, text=True,
    ).splitlines()
    expected = (root, git_dir, git_dir, git_dir / "index", git_dir / "objects")
    return len(paths) == len(expected) and all(
        (root / value).resolve() == path.resolve()
        for value, path in zip(paths, expected)
    )


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
        # On POSIX the final root seal is the completion point. Some macOS
        # filesystems reject a write-disabled rename, so the path briefly exists
        # while the writer still holds its lock and is sealing it.
        if os.name != "nt" and target.stat().st_mode & 0o222:
            raise ValueError("Shared source publication is incomplete")
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
            staging = Path(tempfile.mkdtemp(prefix=".source-", dir=target.parent))
            staging_stat = staging.stat()
            staging_identity = (staging_stat.st_dev, staging_stat.st_ino)
            # Git's full-clone fallback deletes/recreates its destination. Keep
            # custody of a stable parent instead of adopting a replacement inode
            # after a failed or cancelled clone.
            stage = staging / "checkout"
            incomplete = None
            failure = None
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

                def prepare():
                    # copytree copies the source root's mode, including a seal
                    # when adopting an already immutable checkout.
                    stage.chmod(0o700)
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
                    if actual != revision or not clean_checkout(stage):
                        raise ValueError(
                            "Only exact, clean sources may enter the shared store"
                        )
                    # Finish worktree mode changes before Git records their
                    # stat data. Re-sealing these files after status would make
                    # that newly refreshed index stale again.
                    for path in stage.rglob("*"):
                        if (
                            path.relative_to(stage).parts[0] != ".git"
                            and not path.is_symlink()
                        ):
                            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
                    # Only this unpublished copy may refresh its index, even
                    # when the existing source was already sealed. Windows also
                    # needs the copied index's read-only attribute cleared.
                    if not _index_refresh_owned(stage):
                        return  # Preserve read-only publication for other layouts.
                    git_dir = stage / ".git"
                    git_dir.chmod(stat.S_IMODE(git_dir.stat().st_mode) | 0o700)
                    index = git_dir / "index"
                    if index.exists() and not index.is_symlink():
                        index.chmod(stat.S_IMODE(index.stat().st_mode) | 0o200)
                    dirty = subprocess.check_output(
                        _with_longpaths(
                            [
                                "git",
                                "status",
                                "--porcelain",
                                "--untracked-files=no",
                            ]
                        ),
                        cwd=stage,
                        text=True,
                        env={**os.environ, "GIT_OPTIONAL_LOCKS": "1"},
                    )
                    if dirty:
                        raise ValueError(
                            "Only exact, clean sources may enter the shared store"
                        )

                # A cancelled refresh settles before cleanup, without entering
                # publication. Completed publications still survive cancellation.
                await complete_io(prepare)

                def publish():
                    nonlocal incomplete

                    # Builds must use writable build views, never modify this
                    # tree. Worktree modes are already final; seal only Git here.
                    git_dir = stage / ".git"
                    for path in git_dir.rglob("*"):
                        if not path.is_symlink():
                            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
                    git_dir.chmod(stat.S_IMODE(git_dir.stat().st_mode) & ~0o222)
                    stage_stat = stage.stat()
                    identity = (stage_stat.st_dev, stage_stat.st_ino)
                    if os.name == "nt":
                        # Windows directory attributes are not POSIX permissions.
                        # Retain its existing fully sealed rename ordering.
                        stage.chmod(0o555)
                        stage.rename(target)
                    else:
                        stage.rename(target)
                        incomplete = identity  # Only this writer's renamed inode.
                        target.chmod(0o555)
                    incomplete = None  # A completed object survives cancellation.

                await complete_io(publish)
            except BaseException as error:
                failure = error
                raise
            finally:
                cleanup_paths = [(staging, staging_identity)]
                if incomplete is not None:
                    cleanup_paths.insert(0, (target, incomplete))
                for cleanup, identity in cleanup_paths:
                    try:
                        await complete_io(_remove_owned_stage, cleanup, identity)
                    except BaseException as error:
                        if failure is None:
                            raise
                        failure.add_note(
                            f"Source cleanup failed; inspect {cleanup}: {error!r}"
                        )
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
        legacy = handler._get_cache_path(parsed, Path(cache))
        legacy.parent.mkdir(parents=True, exist_ok=True)
        # A legacy resolver may still be cloning into this directory. Its
        # sibling writer lock is distinct from our generation-binding lock.
        # Acquire both before inspecting or returning the legacy checkout.
        async with (
            AsyncFileLock(path.with_suffix(".lock")),
            AsyncFileLock(legacy.with_name(f".{legacy.name}.lock")),
        ):
            # Binding scopes coalesce bundles/skills, but legacy directories
            # retain the handler's original cache path and any edits there.
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

    def select(output):
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

    args = ["git", "ls-remote", url, *patterns]
    if os.name == "nt":
        from ._windows_git import check_output

        return select(await check_output(args, timeout=35))

    def read():
        return select(subprocess.check_output(args, text=True, timeout=35))

    return await asyncio.to_thread(read)


async def resolve_shared_source(uri, cache, *, refresh=False):
    root = os.environ.get("AMPLIFIER_SOURCE_STORE")
    if not root:
        raise ValueError("No shared source store configured")
    return await SharedSourceStore(root).resolve(uri, cache, refresh=refresh)


def _build_metadata(root):
    marker = root / ".amplifier_cache_meta.json"
    if not stat.S_ISREG(marker.lstat().st_mode):
        raise ValueError(f"Build provenance must be an ordinary file: {marker}")
    data = json.loads(marker.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Invalid build provenance: {marker}")
    return data


def _build_key(data):
    url, revision = data.get("git_url"), data.get("commit")
    if not isinstance(url, str) or not url or not isinstance(revision, str):
        raise ValueError("Build input requires exact Git provenance")
    if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", revision):
        raise ValueError("Build input requires an exact source revision")
    return source_id(url, revision)


def _build_directory(path):
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError(f"Build control path must be an ordinary directory: {path}")


def _build_links(root):
    """Do not let a writable build follow a link back into immutable inputs.

    Relative contained links are preserved literally, including dangling links.
    Absolute links would still address the original tree after copying.
    """
    def unreadable(error):
        raise error

    for parent, directories, files in os.walk(
        root, followlinks=False, onerror=unreadable
    ):
        for name in directories + files:
            path = Path(parent) / name
            if name == ".git":
                _build_directory(path)
                for redirect in (
                    "commondir", "gitdir", "objects/info/alternates",
                    "objects/info/http-alternates",
                ):
                    control = path / redirect
                    if control.exists() or control.is_symlink():
                        raise ValueError(f"Build input redirects Git metadata: {control}")
            if path.is_symlink() and (
                Path(os.readlink(path)).anchor
                or not path.resolve().is_relative_to(root)
            ):
                raise ValueError(f"Build input contains an escaping symlink: {path}")


def _walk_build_subpath(root, relative):
    """Validate a relative traversal and its literal link targets.

    Inspect only the requested traversal, including link chains. Reject a link
    or parent step that leaves custody even if later steps would reenter it.
    Full-tree link validation belongs to publication and backend acquisition.
    """
    from collections import deque

    if relative.anchor:
        raise ValueError("Build input subpath must be relative")
    path = root
    pending = deque(relative.parts)
    links = 0
    try:
        while pending:
            part = pending.popleft()
            if part == "..":
                path = path.parent
                if not path.is_relative_to(root):
                    raise ValueError(f"Build input subpath escapes its root: {relative}")
                continue
            path = path / part
            if not path.is_relative_to(root):
                raise ValueError(f"Build input subpath escapes its root: {relative}")
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                text = os.readlink(path)
                destination = Path(text)
                links += 1
                if destination.anchor or links > 40:
                    raise ValueError(f"Build input contains an escaping symlink: {path}")
                path = path.parent
                # Path.parts discards trailing separators and '.', which would
                # incorrectly accept a link to 'ordinary-file/.' or 'file/'.
                if os.altsep:
                    text = text.replace(os.altsep, os.sep)
                pending.extendleft(reversed([
                    component or "." for component in text.split(os.sep)
                ]))
            elif (pending or part == ".") and not stat.S_ISDIR(mode):
                raise ValueError(f"Build input subpath is unavailable: {root / relative}")
    except (FileNotFoundError, NotADirectoryError) as error:
        raise ValueError(f"Build input subpath is unavailable: {root / relative}") from error
    return path


def _build_subpath(root, relative):
    """Keep native file-access selection within the lexical owning root.

    POSIX follows each link before '..'. Ordinary Win32 paths normalize parent
    components before following reparse points. Do not install a POSIX-emulated
    package when Windows file access selected a different one. Inspect native
    prefixes too, so a cancelled escaping link is still rejected conservatively.
    """
    if os.name != "nt":
        return _walk_build_subpath(root, relative)
    if relative.anchor:
        raise ValueError("Build input subpath must be relative")
    raw = selected = root
    try:
        for index, part in enumerate(relative.parts):
            raw = raw / part
            if raw.is_symlink():
                # The parent uses native selection; literal target traversal
                # still rejects anchored/escaping links and file slash/dot.
                parent = raw.parent.resolve(strict=True)
                if not parent.is_relative_to(root):
                    raise ValueError(f"Build input subpath escapes its root: {relative}")
                _walk_build_subpath(root, parent.relative_to(root) / raw.name)
            selected = raw.resolve(strict=True)
            if not selected.is_relative_to(root):
                raise ValueError(f"Build input subpath escapes its root: {relative}")
            if index + 1 < len(relative.parts) and not selected.is_dir():
                raise ValueError(f"Build input subpath is unavailable: {root / relative}")
    except (FileNotFoundError, NotADirectoryError) as error:
        raise ValueError(f"Build input subpath is unavailable: {root / relative}") from error
    return selected


def _build_target_state(target, data):
    """Classify only attributable old partials; never adopt arbitrary residue."""
    _build_directory(target)
    actual = _build_metadata(target)
    if _build_key(actual) != _build_key(data):
        raise ValueError(f"Build input provenance changed; retained {target}")
    _build_directory(target / ".git")
    if actual.get("immutable") is False and actual.get("buildInput") is True:
        if actual.get("buildViewVersion", 1) != 1:
            raise ValueError(f"Unsupported build input format; retained {target}")
        if not target.stat().st_mode & 0o200:
            raise ValueError(f"Build input is not writable; retained {target}")
        return "complete"
    if actual.get("immutable") is True and "buildInput" not in actual:
        # The old direct copy could leave the source marker in a partial tree.
        # It is not completion evidence or permission to delete its contents.
        return "partial"
    raise ValueError(f"Incomplete or unowned build input; retained {target}")


def _build_ignore(directory, names):
    """Sockets/FIFOs are runtime endpoints, not build content. Never open them."""
    ignored = []
    for name in names:
        path = Path(directory) / name
        mode = path.lstat().st_mode
        if stat.S_ISSOCK(mode) or stat.S_ISFIFO(mode):
            ignored.append(name)
        elif not (
            stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)
        ):
            raise ValueError(f"Unsupported special file in build input: {path}")
    return ignored


def _publish_build(root, target, data, relative, partial_identity):
    staging = Path(tempfile.mkdtemp(prefix=".build-", dir=target.parent))
    info = staging.stat()
    identity = (info.st_dev, info.st_ino)
    stage = staging / "checkout"
    failure = None
    try:
        _build_links(root)
        shutil.copytree(root, stage, symlinks=True, ignore=_build_ignore)
        # Top-down traversal restores directory access before visiting children.
        for parent, directories, files in os.walk(stage, followlinks=False):
            directory = Path(parent)
            directory.chmod(stat.S_IMODE(directory.stat().st_mode) | 0o700)
            for name in files:
                path = directory / name
                if not path.is_symlink():
                    path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o200)
        _build_links(stage)  # Relative containment must survive relocation too.
        _build_subpath(stage, relative)
        atomic(
            stage / ".amplifier_cache_meta.json",
            {**data, "immutable": False, "buildInput": True, "buildViewVersion": 1},
        )
        if partial_identity is not None:
            current = target.lstat()
            if (current.st_dev, current.st_ino) != partial_identity:
                raise ValueError(f"Build recovery ownership changed; retained {target}")
            # Preserve all old contents, including unknown additions. This is
            # recovery, not garbage collection; consumers own eventual removal.
            quarantine = Path(
                tempfile.mkdtemp(prefix=f".{target.name}.incomplete-", dir=target.parent)
            )
            retained = quarantine / "checkout"
            mode = stat.S_IMODE(current.st_mode)
            # Moving a directory between parents can require write access to
            # the directory itself. Restore its exact mode at either location.
            target.chmod(mode | 0o700)
            try:
                target.rename(retained)
            except BaseException as error:
                try:
                    target.chmod(mode)
                except BaseException as restore:
                    error.add_note(f"Build recovery mode restore failed: {restore!r}")
                raise
            retained.chmod(mode)
        elif target.exists() or target.is_symlink():
            raise ValueError(f"Build publication target appeared; retained {target}")
        stage.rename(target)  # Completion metadata and writable modes already final.
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            _remove_owned_stage(staging, identity)
        except BaseException as error:
            if failure is None:
                raise
            failure.add_note(f"Build cleanup failed; inspect {staging}: {error!r}")


def build_view(source):
    """A stable writable build input; immutable checkout remains untouched.

    Kept outside the object store, with exact Git provenance for qualification.
    uv's wheel cache reuses artifacts; no editable package is shared by hosts.
    """
    original = Path(source)
    # Inspect lexical ancestors before resolving the requested subpath, so an
    # escaping module symlink cannot silently bypass its immutable parent.
    source = original if original.is_absolute() else Path.cwd() / original
    ancestors = (source, *source.parents)
    roots = [
        root for root in ancestors
        if root.parent.parent.name == "objects"
        and re.fullmatch(r"[a-f0-9]{64}", root.parent.name)
        and re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", root.name)
    ]
    if not roots and any(
        root.parent.parent.name == "objects"
        and re.fullmatch(r"[a-f0-9]{64}", root.parent.name)
        and re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", root.name)
        for root in (source.resolve(), *source.resolve().parents)
    ):
        raise ValueError("Build input aliases managed source storage")
    # The outer owning object wins over content that mimics a store layout.
    for root in roots[-1:] or ancestors:
        marker = root / ".amplifier_cache_meta.json"
        if not roots and not marker.exists() and not marker.is_symlink():
            continue
        data = _build_metadata(root)
        if data.get("immutable") is not True:
            if roots:
                raise ValueError(f"Immutable build input metadata changed: {root}")
            return original, False
        _build_directory(root / ".git")
        key = _build_key(data)
        store = root.parent.parent.parent
        if (
            root != root.resolve()
            or root.parent.parent.name != "objects"
            or root.parent.name != source_id(data["git_url"], "")
            or root.name != data["commit"]
        ):
            raise ValueError("Immutable build input is outside its owned source store")
        relative = _build_subpath(root, source.relative_to(root)).relative_to(root)
        target = store / "builds" / key
        from filelock import FileLock

        target.parent.mkdir(parents=True, exist_ok=True)
        _build_directory(target.parent)
        if Path(str(target) + ".lock").is_symlink():
            raise ValueError("Build input lock must not be a symlink")
        with FileLock(str(target) + ".lock"):
            partial_identity = None
            if target.exists() or target.is_symlink():
                state = _build_target_state(target, data)
                if state == "complete":
                    return _build_subpath(target, relative), True
                info = target.lstat()
                partial_identity = (info.st_dev, info.st_ino)
            _publish_build(root, target, data, relative, partial_identity)
            return _build_subpath(target, relative), True
    return original, False


class _BuildLock(FileLock):
    """Retain FileLock's identity while validating only under backend custody."""

    def __init__(self, root, relative):
        super().__init__(str(root) + ".lock")
        self.root = root
        self.relative = relative

    def acquire(self, *args, **kwargs):
        acquired = super().acquire(*args, **kwargs)
        try:
            data = _build_metadata(self.root)
            if (
                self.root != self.root.resolve()
                or self.root.name != _build_key(data)
                or _build_target_state(self.root, data) != "complete"
            ):
                raise ValueError(f"Invalid build input; retained {self.root}")
            # One full validation per acquisition, not per sibling lookup.
            # Even reentrant user code may have mutated the writable tree.
            # No validation result survives backend code or lock release.
            _build_links(self.root)
            _build_subpath(self.root, self.relative)
        except BaseException:
            self.release()
            raise
        return acquired


def build_lock(source):
    """A build backend may write metadata; serialize all users of its view."""
    from contextlib import nullcontext

    source = Path(source)
    source = source if source.is_absolute() else Path.cwd() / source
    ancestors = (source, *source.parents)
    # A nested package marker must not mask the canonical build root's lock.
    roots = [
        root for root in ancestors
        if root.parent.name == "builds" and re.fullmatch(r"[a-f0-9]{64}", root.name)
    ]
    if not roots and any(
        root.parent.name == "builds" and re.fullmatch(r"[a-f0-9]{64}", root.name)
        for root in (source.resolve(), *source.resolve().parents)
    ):
        raise ValueError("Build input aliases managed build storage")
    if roots:
        root = roots[-1]
        _build_directory(root.parent)
        if root.parent != root.parent.resolve():
            raise ValueError("Build storage must not be redirected")
        if Path(str(root) + ".lock").is_symlink():
            raise ValueError("Build input lock must not be a symlink")
        return _BuildLock(root, relative=source.relative_to(root))
    for root in ancestors:
        marker = root / ".amplifier_cache_meta.json"
        if marker.exists() or marker.is_symlink():
            data = _build_metadata(root)
            if data.get("buildInput"):
                raise ValueError(f"Invalid build input; retained {root}")
            return nullcontext()
    return nullcontext()
