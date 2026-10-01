import json
import subprocess

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
