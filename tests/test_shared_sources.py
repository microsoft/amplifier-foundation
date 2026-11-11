import asyncio
import errno
import importlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading

import pytest

from amplifier_foundation.sources.shared import (
    SharedSourceStore,
    build_view,
    resolve_shared_source,
)


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "bundle.md").write_text("bundle")
    (repo / "skills").mkdir()
    (repo / "skills/SKILL.md").write_text("skill")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    return repo, git(repo, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_bundle_and_skill_share_exact_snapshot_and_generation_bindings(
    tmp_path, monkeypatch, repository
):
    repo, first = repository
    store = SharedSourceStore(tmp_path / "store")
    monkeypatch.setenv("AMPLIFIER_SOURCE_STORE", str(store.root))
    url = "https://example.invalid/repo"
    await store.bind(
        tmp_path / "generation-one/cache", url, "main", first, existing=repo
    )
    bundle = await resolve_shared_source(
        "git+" + url + "@main", tmp_path / "generation-one/cache"
    )
    skill = await resolve_shared_source(
        "git+" + url + "@main#subdirectory=skills",
        tmp_path / "generation-one/cache/skills",
    )
    assert bundle.source_root == skill.source_root
    assert skill.active_path == bundle.source_root / "skills"
    (repo / "bundle.md").write_text("new bundle")
    git(repo, "commit", "-am", "next")
    second = git(repo, "rev-parse", "HEAD")
    await store.bind(
        tmp_path / "generation-two/cache", url, "main", second, existing=repo
    )
    newer = await resolve_shared_source(
        "git+" + url + "@main", tmp_path / "generation-two/cache"
    )
    assert newer.source_root != bundle.source_root
    assert (bundle.source_root / "bundle.md").read_text() == "bundle"
    assert (newer.source_root / "bundle.md").read_text() == "new bundle"
    assert not (bundle.source_root / "bundle.md").stat().st_mode & 0o222
    build, immutable = build_view(bundle.source_root)
    assert immutable
    (build / "bundle.md").write_text("generated build data")
    assert (bundle.source_root / "bundle.md").read_text() == "bundle"
    assert not json.loads((build / ".amplifier_cache_meta.json").read_text())[
        "immutable"
    ]


@pytest.mark.asyncio
async def test_dirty_sources_and_credentials_do_not_enter_store(tmp_path, repository):
    repo, revision = repository
    store = SharedSourceStore(tmp_path / "store")
    (repo / "bundle.md").write_text("local edit")
    with pytest.raises(ValueError, match="clean"):
        await store.ensure("https://example.invalid/repo", revision, existing=repo)
    with pytest.raises(ValueError, match="credential-free"):
        store.checkout("https://token@example.invalid/repo", revision)
    assert not list((store.root / "objects").rglob(revision))


@pytest.mark.asyncio
async def test_shared_snapshot_rejects_missing_subpath(
    tmp_path, monkeypatch, repository
):
    repo, revision = repository
    store = SharedSourceStore(tmp_path / "store")
    monkeypatch.setenv("AMPLIFIER_SOURCE_STORE", str(store.root))
    await store.bind(
        tmp_path / "cache",
        "https://example.invalid/repo",
        "main",
        revision,
        existing=repo,
    )
    with pytest.raises(ValueError, match="subpath"):
        await resolve_shared_source(
            "git+https://example.invalid/repo@main#subdirectory=missing",
            tmp_path / "cache",
        )


@pytest.mark.asyncio
async def test_ignored_local_files_and_linked_worktrees_are_preserved(
    tmp_path, repository
):
    repo, revision = repository
    (repo / ".git/info/exclude").write_text("local-notes.txt\n")
    (repo / "local-notes.txt").write_text("retain private work")
    store = SharedSourceStore(tmp_path / "store")
    with pytest.raises(ValueError, match="clean"):
        await store.ensure("https://example.invalid/repo", revision, existing=repo)
    assert (repo / "local-notes.txt").read_text() == "retain private work"


@pytest.mark.asyncio
async def test_equivalent_urls_share_snapshot_and_refresh_never_mutates_reader(
    tmp_path, monkeypatch, repository
):
    repo, first = repository
    store = SharedSourceStore(tmp_path / "store")
    monkeypatch.setenv("AMPLIFIER_SOURCE_STORE", str(store.root))
    url = "https://example.invalid/repo"
    old = await store.bind(tmp_path / "cache", url, "main", first, existing=repo)
    assert await store.ensure(url + ".git", first) == old
    (repo / "bundle.md").write_text("new bundle")
    git(repo, "commit", "-am", "new")
    second = git(repo, "rev-parse", "HEAD")
    await store.ensure(url, second, existing=repo)
    from amplifier_foundation.sources import shared

    async def remote(url, ref):
        return second

    monkeypatch.setattr(shared, "remote_revision", remote)
    result = await store.resolve(
        "git+" + url + "@main", tmp_path / "cache", refresh=True
    )
    assert result.source_root != old
    assert (old / "bundle.md").read_text() == "bundle"
    assert (result.source_root / "bundle.md").read_text() == "new bundle"


def test_object_symlink_cannot_redirect_shared_storage(tmp_path, repository):
    repo, revision = repository
    store = SharedSourceStore(tmp_path / "store")
    target = store.checkout("https://example.invalid/repo", revision)
    target.parent.mkdir(parents=True)
    target.symlink_to(repo, target_is_directory=True)
    with pytest.raises(ValueError, match="owned store"):
        store.verify("https://example.invalid/repo", revision)
    assert (repo / "bundle.md").read_text() == "bundle"


@pytest.mark.asyncio
async def test_nested_legacy_bundle_edits_remain_authoritative(tmp_path, repository):
    import shutil

    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler

    repo, revision = repository
    store = SharedSourceStore(tmp_path / "store")
    cache = tmp_path / "generation/cache/bundles"
    uri = "git+https://example.invalid/repo@main"
    await store.bind(
        cache, "https://example.invalid/repo", "main", revision, existing=repo
    )
    legacy = GitSourceHandler()._get_cache_path(parse_uri(uri), cache)
    shutil.copytree(repo, legacy)
    (legacy / "bundle.md").write_text("local bundle edit")
    result = await store.resolve(uri, cache)
    assert result.source_root == legacy
    assert (result.active_path / "bundle.md").read_text() == "local bundle edit"
    with pytest.raises(ValueError, match="Local changes"):
        await store.resolve(uri, cache, refresh=True)


@pytest.mark.asyncio
async def test_shared_status_reads_binding_without_clone_or_mutation(
    tmp_path, monkeypatch, repository
):
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources import shared
    from amplifier_foundation.sources.git import GitSourceHandler

    repo, revision = repository
    store = SharedSourceStore(tmp_path / "store")
    cache = tmp_path / "generation/cache/bundles"
    await store.bind(
        cache, "https://example.invalid/repo", "main", revision, existing=repo
    )
    monkeypatch.setenv("AMPLIFIER_SOURCE_STORE", str(store.root))

    async def remote(url, ref):
        assert ref == "main"
        return revision

    monkeypatch.setattr(shared, "remote_revision", remote)
    before = list(store.root.rglob("*"))
    # Equivalent .git spellings retain the same binding and status contract.
    uri = "git+https://example.invalid/repo.git@main"
    status = await GitSourceHandler().get_status(parse_uri(uri), cache)
    assert (
        status.is_cached
        and status.cached_commit == revision
        and status.has_update is False
    )
    assert list(store.root.rglob("*")) == before
    assert (await store.resolve(uri, cache)).source_root == store.checkout(
        "https://example.invalid/repo", revision
    )


@pytest.mark.asyncio
async def test_bundle_root_package_recognition_uses_generation_binding(
    tmp_path, monkeypatch, repository
):
    from amplifier_foundation.modules.activator import bundle_root_declares_module

    repo, revision = repository
    store = SharedSourceStore(tmp_path / "store")
    monkeypatch.setenv("AMPLIFIER_SOURCE_STORE", str(store.root))
    cache = tmp_path / "generation/cache"
    url = "https://example.invalid/repo"
    root = await store.bind(cache, url, "main", revision, existing=repo)
    module = "git+" + url + "@main#subdirectory=modules/example"
    assert bundle_root_declares_module(root, [module], cache_dir=cache)
    assert not bundle_root_declares_module(
        root, [module.replace("@main", "@other")], cache_dir=cache
    )
    assert not bundle_root_declares_module(
        root, [module.replace("/repo@", "/unrelated@")], cache_dir=cache
    )
    (repo / "bundle.md").write_text("changed")
    git(repo, "commit", "-am", "next")
    second = git(repo, "rev-parse", "HEAD")
    newer = await store.bind(cache, url, "main", second, existing=repo)
    assert bundle_root_declares_module(newer, [module], cache_dir=cache)
    assert not bundle_root_declares_module(root, [module], cache_dir=cache)


@pytest.mark.asyncio
@pytest.mark.parametrize('subpath', ['', '#subdirectory=bundle.md'])
async def test_shared_reader_waits_for_legacy_cache_writer(tmp_path, subpath):
    import asyncio

    from filelock import FileLock
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler

    store = SharedSourceStore(tmp_path / 'store')
    cache = tmp_path / 'cache'
    uri = 'git+https://example.invalid/repo@main' + subpath
    legacy = GitSourceHandler()._get_cache_path(parse_uri(uri), cache)
    cache.mkdir()
    # The legacy Git handler holds this sibling lock while a clone populates
    # the cache. The directory exists before its bundle has been checked out.
    with FileLock(legacy.with_name(f'.{legacy.name}.lock')):
        legacy.mkdir()
        pending = asyncio.create_task(store.resolve(uri, cache))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(pending), timeout=0.1)
        finally:
            (legacy / 'bundle.md').write_text('complete bundle')
    result = await asyncio.wait_for(pending, timeout=5)
    assert result.source_root == legacy
    assert (result.source_root / 'bundle.md').read_text() == 'complete bundle'
    assert result.active_path == (legacy / 'bundle.md' if subpath else legacy)


@pytest.mark.skipif(sys.platform != "darwin", reason="Actual macOS rename observation")
def test_darwin_write_disabled_directory_rename(tmp_path):
    """Keep native reproduction separate from the injected contract regression.

    macOS 26.6.2 allowed this on both its default volume and a native HFS+ image.
    Do not assert every Mac/filesystem has the macOS 13 reporter's behavior.
    """
    writable = tmp_path / "writable"
    writable.mkdir()
    writable.rename(tmp_path / "success")
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    sealed.chmod(0o555)
    destination = tmp_path / "renamed"
    try:
        try:
            sealed.rename(destination)
        except PermissionError as error:
            assert error.errno == errno.EACCES
            assert sealed.exists() and not destination.exists()
        else:
            assert destination.is_dir() and not sealed.exists()
            pytest.skip("Native write-disabled rename rejection not reproduced on this volume")
    finally:
        for path in (sealed, destination):
            if path.exists():
                path.chmod(0o700)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX mode/rename contract")
async def test_publication_obeys_write_enabled_rename_contract(
    tmp_path, monkeypatch, repository
):
    """The pre-fix chmod-before-rename implementation fails this discriminator."""
    repo, revision = repository
    store = SharedSourceStore(tmp_path / "store")
    rename = Path.rename
    renamed = []

    def darwin_rename(path, target):
        if path.name.startswith(".source-") or path.name == "checkout":
            assert path.stat().st_mode & stat.S_IWUSR
            assert not (path / "skills").stat().st_mode & 0o222
            renamed.append(path)
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", darwin_rename)
    target = await store.ensure("https://example.invalid/repo", revision, existing=repo)
    assert len(renamed) == 1
    assert git(target, "rev-parse", "HEAD") == revision
    assert (target / "skills/SKILL.md").read_text() == "skill"
    for path in (target, *target.rglob("*")):
        if not path.is_symlink():
            assert not path.stat().st_mode & 0o222
    assert await store.ensure("https://example.invalid/repo", revision) == target
    # An immutable existing source copies its root seal onto the new stage.
    other = SharedSourceStore(tmp_path / "other")
    adopted = await other.ensure(
        "https://example.invalid/repo", revision, existing=target
    )
    assert other.verify("https://example.invalid/repo", revision) == adopted
    assert not target.stat().st_mode & 0o222
    assert not list(target.parent.glob(".source-*"))


async def wait_for_thread(event):
    assert await asyncio.wait_for(asyncio.to_thread(event.wait, 5), timeout=6)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX index stat data and final file modes")
@pytest.mark.parametrize("sealed_source", [False, True])
async def test_published_index_matches_final_modes_without_mutating_source(
    tmp_path, monkeypatch, repository, sealed_source
):
    repo, _ = repository
    outside = tmp_path / "outside"
    outside.write_text("external")
    (repo / "external-link").symlink_to(outside)
    executable = repo / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "mode and symlink fixture")
    revision = git(repo, "rev-parse", "HEAD")
    url = "https://example.invalid/repo"
    if sealed_source:
        repo = await SharedSourceStore(tmp_path / "first").ensure(
            url, revision, existing=repo
        )
    original_index = (repo / ".git/index").read_bytes()
    original_modes = {
        str(path.relative_to(repo)): path.lstat().st_mode
        for path in (repo, *repo.rglob("*"))
    }
    original_mapping = git(repo, "ls-files", "--stage")
    original_outside = outside.stat()
    # A read-only caller environment must not disable the private preparation's
    # index write. Published/existing source inspections still request no writes.
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "0")
    store = SharedSourceStore(tmp_path / "second")
    target = await store.ensure(url, revision, existing=repo)
    assert git(target, "rev-parse", "HEAD") == revision
    assert git(target, "ls-files", "--stage") == original_mapping
    assert os.readlink(target / "external-link") == str(outside)
    assert outside.stat() == original_outside
    assert stat.S_IMODE((target / "run.sh").stat().st_mode) == 0o555
    for relative in ("bundle.md", "skills/SKILL.md", "run.sh"):
        path = target / relative
        actual = path.stat()
        debug = git(target, "ls-files", "--debug", "--", relative)
        for field in ("ctime", "mtime"):
            seconds, nanos = re.search(rf"{field}: (\d+):(\d+)", debug).groups()
            assert int(seconds) * 1_000_000_000 + int(nanos) == getattr(actual, f"st_{field}_ns")
        assert int(re.search(r"ino: (\d+)", debug).group(1)) == actual.st_ino & 0xFFFFFFFF
        assert path.read_bytes() == (repo / relative).read_bytes()
    assert store.verify(url, revision) == target
    assert (repo / ".git/index").read_bytes() == original_index
    assert {
        str(path.relative_to(repo)): path.lstat().st_mode
        for path in (repo, *repo.rglob("*"))
    } == original_modes
    for path in (target, *target.rglob("*")):
        if not path.is_symlink():
            assert not path.stat().st_mode & 0o222
    assert not list(target.parent.glob(".source-*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["worktree", "staged-restored", "head"])
async def test_preparation_rejects_changed_stage_without_repairing_authority(
    tmp_path, monkeypatch, repository, mutation
):
    from amplifier_foundation.sources import shared

    repo, revision = repository
    original_index = (repo / ".git/index").read_bytes()
    original_mapping = git(repo, "ls-files", "--stage")
    copytree = shared.shutil.copytree

    def mutate_copy(source, destination, *args, **kwargs):
        result = copytree(source, destination, *args, **kwargs)
        if Path(destination).name == "checkout":
            if mutation == "head":
                git(destination, "commit", "--allow-empty", "-m", "different HEAD")
            else:
                (Path(destination) / "bundle.md").write_text("local change")
                if mutation == "staged-restored":
                    git(destination, "add", "bundle.md")
                    (Path(destination) / "bundle.md").write_text("bundle")
        return result

    monkeypatch.setattr(shared.shutil, "copytree", mutate_copy)
    store = SharedSourceStore(tmp_path / "store")
    url = "https://example.invalid/repo"
    target = store.checkout(url, revision)
    with pytest.raises(ValueError, match="exact, clean"):
        await store.ensure(url, revision, existing=repo)
    assert not target.exists()
    assert not list(target.parent.glob(".source-*"))
    assert git(repo, "rev-parse", "HEAD") == revision
    assert git(repo, "ls-files", "--stage") == original_mapping
    assert (repo / ".git/index").read_bytes() == original_index
    assert (repo / "bundle.md").read_text() == "bundle"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "redirect", ["index-environment", "git-environment", "worktree-config", "alternates", "index-symlink"]
)
async def test_redirected_metadata_keeps_read_only_publication(
    tmp_path, monkeypatch, repository, redirect
):
    repo, revision = repository
    external_index = tmp_path / "external-index"
    external_index.write_bytes((repo / ".git/index").read_bytes())
    if redirect == "index-environment":
        monkeypatch.setenv("GIT_INDEX_FILE", str(external_index))
    elif redirect == "git-environment":
        monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    elif redirect == "worktree-config":
        git(repo, "config", "core.worktree", str(repo))
    elif redirect == "index-symlink":
        if os.name == "nt":
            pytest.skip("POSIX symbolic index fixture")
        (repo / ".git/index").unlink()
        (repo / ".git/index").symlink_to(external_index)
    else:
        objects = tmp_path / "external-objects"
        objects.mkdir()
        (repo / ".git/objects/info/alternates").write_text(str(objects) + "\n")
    original_index = (repo / ".git/index").read_bytes()
    original_external = external_index.read_bytes()
    check_output = subprocess.check_output

    def only_read_status(args, **kwargs):
        if "status" in args:
            assert "--no-optional-locks" in args
        return check_output(args, **kwargs)

    monkeypatch.setattr(subprocess, "check_output", only_read_status)
    store = SharedSourceStore(tmp_path / "store")
    url = "https://example.invalid/repo"
    target = await store.ensure(url, revision, existing=repo)
    assert store.verify(url, revision) == target
    assert (target / "bundle.md").read_text() == "bundle"
    assert (target / ".git/index").read_bytes() == original_index
    assert (repo / ".git/index").read_bytes() == original_index
    assert external_index.read_bytes() == original_external


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX final root seal")
async def test_unsealed_publication_rejects_readers_and_serializes_ensure(
    tmp_path, monkeypatch, repository
):
    from amplifier_foundation.sources.shared import atomic, source_id

    repo, revision = repository
    store = SharedSourceStore(tmp_path / "store")
    url = "https://example.invalid/repo"
    target = store.checkout(url, revision)
    cache = tmp_path / "cache"
    # Exercise a cached reader too, even if an application already holds a
    # binding to an object that is still being populated.
    atomic(
        cache / ".source-bindings" / (source_id(url, "main") + ".json"),
        {"git_url": url, "ref": "main", "commit": revision},
    )
    reached = threading.Event()
    release = threading.Event()
    chmod = Path.chmod

    def hold_seal(path, mode, *args, **kwargs):
        if path == target and mode == 0o555:
            reached.set()
            assert release.wait(5)
        return chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", hold_seal)
    first = asyncio.create_task(store.ensure(url, revision, existing=repo))
    second = None
    try:
        await wait_for_thread(reached)
        with pytest.raises(ValueError, match="incomplete"):
            store.verify(url, revision)
        with pytest.raises(ValueError, match="incomplete"):
            store.cached("git+" + url + "@main", cache)
        second = asyncio.create_task(store.ensure(url, revision))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(second), timeout=0.1)
    finally:
        release.set()
        results = await asyncio.gather(
            *[task for task in (first, second) if task is not None]
        )
    assert results == [target, target]
    assert store.cached("git+" + url + "@main", cache) == target
    assert not target.stat().st_mode & 0o222


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["refresh", "rename", "descendant", "seal"])
async def test_publication_failure_removes_only_owned_tree(
    tmp_path, monkeypatch, repository, point
):
    if point == "seal" and os.name == "nt":
        pytest.skip("POSIX post-rename root seal")
    repo, revision = repository
    store = SharedSourceStore(tmp_path / "store")
    url = "https://example.invalid/repo"
    target = store.checkout(url, revision)
    cache = tmp_path / "cache"
    original = PermissionError(errno.EACCES, "injected " + point)
    rename, chmod = Path.rename, Path.chmod
    check_output = subprocess.check_output

    def fail_refresh(args, **kwargs):
        if point == "refresh" and "status" in args and "--no-optional-locks" not in args:
            raise original
        return check_output(args, **kwargs)

    def fail_rename(path, destination):
        if point == "rename" and (
            path.name.startswith(".source-") or path.name == "checkout"
        ):
            raise original
        return rename(path, destination)

    def fail_chmod(path, mode, *args, **kwargs):
        if not mode & 0o222 and (
            (point == "descendant" and path.name == "skills" and path != repo / "skills")
            or (point == "seal" and path == target)
        ):
            raise original
        return chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "rename", fail_rename)
    monkeypatch.setattr(Path, "chmod", fail_chmod)
    monkeypatch.setattr(subprocess, "check_output", fail_refresh)
    with pytest.raises(PermissionError) as caught:
        await store.bind(cache, url, "main", revision, existing=repo)
    assert caught.value is original
    assert not target.exists()
    assert not list(target.parent.glob(".source-*"))
    assert not list(cache.rglob("*.json"))
    assert (repo / "bundle.md").read_text() == "bundle"
    # A subsequent independent attempt can publish; no stale partial was adopted.
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(Path, "chmod", chmod)
    monkeypatch.setattr(subprocess, "check_output", check_output)
    assert await store.ensure(url, revision, existing=repo) == target


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_mask_publication_exception(
    tmp_path, monkeypatch, repository
):
    from amplifier_foundation.sources import _rmtree, shared

    repo, revision = repository
    store = SharedSourceStore(tmp_path / "store")
    original = PermissionError(errno.EACCES, "original rename failure")

    def fail_rename(path, target):
        raise original

    def fail_cleanup(path):
        raise OSError("separate cleanup failure")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", fail_rename)
        patch.setattr(_rmtree, "rmtree_robust", fail_cleanup)
        with pytest.raises(PermissionError) as caught:
            await store.ensure("https://example.invalid/repo", revision, existing=repo)
    assert caught.value is original
    assert any("separate cleanup failure" in note for note in original.__notes__)
    stages = list(store.root.rglob(".source-*"))
    assert len(stages) == 1
    info = stages[0].stat()
    shared._remove_owned_stage(stages[0], (info.st_dev, info.st_ino))
    assert not stages[0].exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and directory mode checks")
def test_owned_cleanup_restores_parents_without_following_symlinks(tmp_path):
    from amplifier_foundation.sources.shared import _remove_owned_stage

    stage = tmp_path / "stage"
    child = stage / "child"
    child.mkdir(parents=True)
    (child / "payload").write_text("owned")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "retained").write_text("external")
    (stage / "link").symlink_to(outside, target_is_directory=True)
    outside.chmod(0o555)
    child.chmod(0o555)
    stage.chmod(0o555)
    identity = stage.stat()
    try:
        _remove_owned_stage(stage, (identity.st_dev, identity.st_ino))
        assert not stage.exists()
        assert (outside / "retained").read_text() == "external"
        assert stat.S_IMODE(outside.stat().st_mode) == 0o555
    finally:
        outside.chmod(0o700)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX post-rename root seal")
async def test_failed_seal_never_deletes_replaced_target(tmp_path, monkeypatch, repository):
    repo, revision = repository
    store = SharedSourceStore(tmp_path / "store")
    url = "https://example.invalid/repo"
    target = store.checkout(url, revision)
    displaced = target.with_name("displaced")
    chmod = Path.chmod
    original = PermissionError("seal failed after external replacement")

    def replace_before_seal(path, mode, *args, **kwargs):
        if path == target and mode == 0o555:
            path.rename(displaced)
            path.mkdir()
            (path / "retain").write_text("not our inode")
            raise original
        return chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", replace_before_seal)
    with pytest.raises(PermissionError) as caught:
        await store.ensure(url, revision, existing=repo)
    assert caught.value is original
    assert any("ownership changed" in note for note in original.__notes__)
    assert (target / "retain").read_text() == "not our inode"
    assert (displaced / "bundle.md").read_text() == "bundle"
    with pytest.raises(ValueError, match="incomplete"):
        await store.ensure(url, revision, existing=repo)
    assert (target / "retain").read_text() == "not our inode"


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX final root seal")
@pytest.mark.parametrize("point", ["copy", "refresh", "seal", "cleanup"])
async def test_repeated_cancellation_keeps_writer_lock_until_io_settles(
    tmp_path, monkeypatch, repository, point
):
    from amplifier_foundation.sources import shared

    repo, revision = repository
    store = SharedSourceStore(tmp_path / "store")
    url = "https://example.invalid/repo"
    target = store.checkout(url, revision)
    reached, release = threading.Event(), threading.Event()
    copytree, chmod, remove = shared.shutil.copytree, Path.chmod, shared._remove_owned_stage
    check_output, rename = subprocess.check_output, Path.rename
    refreshed, renamed = [], []
    original = PermissionError("original publication failure")
    held = False

    def hold_once():
        nonlocal held
        if not held:
            held = True
            reached.set()
            assert release.wait(5)

    def hold_copy(*args, **kwargs):
        if point == "copy":
            hold_once()
        return copytree(*args, **kwargs)

    def hold_seal(path, mode, *args, **kwargs):
        if path == target and mode == 0o555:
            if point == "seal":
                hold_once()
            elif point == "cleanup" and not held:
                raise original
        return chmod(path, mode, *args, **kwargs)

    def hold_cleanup(*args):
        if point == "cleanup":
            hold_once()
        return remove(*args)

    def hold_refresh(args, **kwargs):
        if "status" in args and "--no-optional-locks" not in args:
            refreshed.append(Path(kwargs["cwd"]))
            if point == "refresh":
                hold_once()
        return check_output(args, **kwargs)

    def record_rename(path, destination):
        renamed.append(path)
        return rename(path, destination)

    monkeypatch.setattr(shared.shutil, "copytree", hold_copy)
    monkeypatch.setattr(Path, "chmod", hold_seal)
    monkeypatch.setattr(shared, "_remove_owned_stage", hold_cleanup)
    monkeypatch.setattr(subprocess, "check_output", hold_refresh)
    monkeypatch.setattr(Path, "rename", record_rename)
    writer = asyncio.create_task(store.ensure(url, revision, existing=repo))
    waiter = None
    try:
        await wait_for_thread(reached)
        writer.cancel()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(writer), timeout=0.05)
        writer.cancel()
        waiter = asyncio.create_task(store.ensure(url, revision, existing=repo))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(waiter), timeout=0.1)
        assert not writer.done()
    finally:
        release.set()
        results = await asyncio.gather(
            *[task for task in (writer, waiter) if task is not None],
            return_exceptions=True,
        )
    if point == "cleanup":
        assert results[0] is original
        assert any("cleanup failed" in note for note in original.__notes__)
    else:
        assert isinstance(results[0], asyncio.CancelledError)
    assert results[1] == target
    if point == "refresh":
        assert len(refreshed) == 2
        assert refreshed[0] not in renamed  # Cancelled preparation never publishes.
        assert refreshed[1] in renamed
    assert store.verify(url, revision) == target
    assert not list(target.parent.glob(".source-*"))


@pytest.mark.asyncio
async def test_cancelled_io_retains_thread_failure():
    from amplifier_foundation.sources.shared import complete_io

    reached, release = threading.Event(), threading.Event()
    original = OSError("thread failed")

    def fail():
        reached.set()
        assert release.wait(5)
        raise original

    task = asyncio.create_task(complete_io(fail))
    try:
        await wait_for_thread(reached)
        task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.__cause__ is original
    assert any("thread failed" in note for note in caught.value.__notes__)


@pytest.mark.asyncio
async def test_shared_source_module_activation_cold_and_reused(
    tmp_path, monkeypatch, repository
):
    """Real Git clone + strict activation/import, without providers or installers."""
    from amplifier_foundation.modules.activator import ModuleActivator
    from amplifier_foundation.sources.git import GitSourceHandler

    repo, _ = repository
    (repo / "shared_cache_fixture.py").write_text("VALUE = 'activated'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "module fixture")
    revision = git(repo, "rev-parse", "HEAD")
    url = "https://example.invalid/repo"
    store = SharedSourceStore(tmp_path / "store")
    monkeypatch.setenv("AMPLIFIER_SOURCE_STORE", str(store.root))
    monkeypatch.setattr(sys, "path", list(sys.path))
    # Substitute only the transport endpoint, not clone, publication or activation.
    clone = GitSourceHandler._clone_at_commit
    clones = []

    def local_clone(handler, git_url, sha, path):
        assert git_url == url and sha == revision
        clones.append(path)
        return clone(handler, str(repo), sha, path)

    monkeypatch.setattr(GitSourceHandler, "_clone_at_commit", local_clone)
    modules = [{"module": "shared-cache-fixture", "source": "git+" + url + "@" + revision}]
    try:
        for _ in range(2):
            activator = ModuleActivator(
                cache_dir=tmp_path / "generation/cache", install_deps=False, strict=True
            )
            result = await activator.activate_all(modules)
            target = result["shared-cache-fixture"]
            assert target == store.verify(url, revision)
            monkeypatch.setattr(sys, "dont_write_bytecode", True)
            module = importlib.import_module("shared_cache_fixture")
            assert module.VALUE == "activated"
            assert Path(module.__file__) == target / "shared_cache_fixture.py"
            sys.modules.pop("shared_cache_fixture")
        assert len(clones) == 1
    finally:
        sys.modules.pop("shared_cache_fixture", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["clone-failure", "cancel", "seal", "success"])
async def test_full_clone_fallback_replaces_checkout_not_cleanup_custody(
    tmp_path, monkeypatch, repository, point
):
    from amplifier_foundation.sources import git as git_source

    if point == "seal" and os.name == "nt":
        pytest.skip("POSIX post-rename root seal")
    repo, revision = repository
    url = repo.as_uri()
    store = SharedSourceStore(tmp_path / "store")
    target = store.checkout(url, revision)
    network = git_source._run_git_network_op
    git_subprocess = git_source._run_git_subprocess
    remove = git_source.rmtree_robust
    chmod = Path.chmod
    reached, release = threading.Event(), threading.Event()
    identities = []
    original = OSError("injected fallback " + point)

    def refuse_shallow(args, *positional, **kwargs):
        if "fetch" in args:
            raise subprocess.CalledProcessError(128, args, stderr="SHA unavailable")
        return git_subprocess(args, *positional, **kwargs)

    def retain_first_inode(path, **kwargs):
        if path.name == "checkout":
            info = path.stat()
            identities.append((info.st_dev, info.st_ino))
            # Keep the old inode allocated so recreation must use a different
            # inode. Git still sees the same absent destination as after rmtree.
            path.rename(path.with_name("discarded-first-attempt"))
            return
        return remove(path, **kwargs)

    def full_clone(args, **kwargs):
        assert "clone" in args
        path = Path(args[-1])
        network(args, **kwargs)  # Real local full clone, then real checkout.
        info = path.stat()
        identities.append((info.st_dev, info.st_ino))
        if point == "clone-failure":
            raise original
        if point == "cancel":
            reached.set()
            assert release.wait(5)

    def fail_seal(path, mode, *args, **kwargs):
        if point == "seal" and path == target and mode == 0o555:
            raise original
        return chmod(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(git_source, "_run_git_subprocess", refuse_shallow)
        patch.setattr(git_source, "_run_git_network_op", full_clone)
        patch.setattr(git_source, "rmtree_robust", retain_first_inode)
        patch.setattr(Path, "chmod", fail_seal)
        if point == "cancel":
            task = asyncio.create_task(store.ensure(url, revision))
            try:
                await wait_for_thread(reached)
                task.cancel()
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.05)
                task.cancel()
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.05)
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif point == "success":
            assert await store.ensure(url, revision) == target
        else:
            with pytest.raises(OSError) as caught:
                await store.ensure(url, revision)
            assert caught.value is original
            assert not getattr(original, "__notes__", [])
    assert len(identities) == 2 and identities[0] != identities[1]
    assert not list(target.parent.glob(".source-*"))
    if point != "success":
        assert not target.exists()
    # Fresh ensure succeeds after failure/cancellation, never adopts a residue.
    assert await store.ensure(url, revision) == target
    assert store.verify(url, revision) == target
    assert not list(target.parent.glob(".source-*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize(
    "ref,rows,expected",
    [
        ("HEAD", [("a" * 40, "HEAD")], "a" * 40),
        ("refs/heads/main", [("b" * 64, "refs/heads/main")], "b" * 64),
        ("v1", [("a" * 40, "refs/tags/v1"), ("b" * 40, "refs/tags/v1^{}")], "b" * 40),
        ("main", [("a" * 40, "refs/heads/main")], "a" * 40),
        ("main", [("bad", "refs/heads/main"), ("a" * 40, "refs/heads/other")], None),
        ("v1", [("a" * 40, "refs/heads/v1"), ("b" * 40, "refs/tags/v1")], ValueError),
    ],
)
async def test_remote_revision_inert_routes_keep_ref_semantics(monkeypatch, windows, ref, rows, expected):
    """Actual resolver body; all process/thread boundaries are inert doubles."""
    from types import SimpleNamespace
    from amplifier_foundation.sources import shared, _windows_git

    calls = []
    output = "".join(f"{revision}\t{name}\n" for revision, name in rows)
    monkeypatch.setattr(shared, "os", SimpleNamespace(name="nt" if windows else "posix"))

    def old_output(args, **kwargs):
        calls.append(("posix", args, kwargs))
        return output

    async def native_output(args, **kwargs):
        calls.append(("windows", args, kwargs))
        return output

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(shared.subprocess, "check_output", old_output)
    monkeypatch.setattr(_windows_git, "check_output", native_output)
    monkeypatch.setattr(shared.asyncio, "to_thread", inline)
    if expected is ValueError:
        with pytest.raises(ValueError, match="Ambiguous"):
            await shared.remote_revision("https://example.invalid/repo", ref)
    else:
        assert await shared.remote_revision("https://example.invalid/repo", ref) == expected
    patterns = [ref] if ref == "HEAD" or ref.startswith("refs/") else [
        "refs/heads/" + ref, "refs/tags/" + ref, "refs/tags/" + ref + "^{}",
    ]
    assert calls == [(
        "windows" if windows else "posix",
        ["git", "ls-remote", "https://example.invalid/repo", *patterns],
        {"timeout": 35} if windows else {"text": True, "timeout": 35},
    )]


@pytest.mark.asyncio
async def test_remote_revision_inert_failure_cannot_select_partial_output(monkeypatch):
    from types import SimpleNamespace
    from amplifier_foundation.sources import shared, _windows_git

    failure = subprocess.TimeoutExpired(["synthetic"], 35, output=b"a" * 40 + b"\tHEAD\n")

    async def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(shared, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(_windows_git, "check_output", fail)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        await shared.remote_revision("https://example.invalid/repo", "HEAD")
    assert caught.value is failure
