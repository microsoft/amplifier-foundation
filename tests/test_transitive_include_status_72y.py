"""check_bundle_status() must report transitively-included sources (recipes-72y).

The defect: ``_collect_source_uris()`` dropped ``bundle.includes`` on the
premise that "included bundles are now registered as first-class bundles and
will be checked independently". That premise is false in the case that
matters. An include reached by ``#subdirectory=`` registers with
``is_root=False``, and host enumeration that keeps only root entries therefore
never checks it -- so a repo whose *only* registry presence is a non-root
sub-bundle entry was checked by nobody, while ``check_bundle_status()``
reported "All N source(s) up to date" over a cache stuck at an old commit.

It compounds because ``GitSourceHandler.resolve()`` returns an existing cache
verbatim on a hit -- no TTL, no fetch, ever. A source the update path does not
enumerate is a source that never moves. These tests pre-seed a cache at commit
one, advance the fake remote to commit two, and assert the stale include is
reported rather than papered over.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from amplifier_foundation.paths.resolution import parse_uri
from amplifier_foundation.registry import BundleRegistry
from amplifier_foundation.sources.git import GitSourceHandler
from amplifier_foundation.updates import _cached_path_for
from amplifier_foundation.updates import check_bundle_status
from amplifier_foundation.updates import collect_transitive_source_uris


def _git(args: list[str], cwd: Path) -> str:
    """Run a git command in a fixture repo and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(["init", "--quiet", "-b", "main"], cwd=repo)
    _git(["config", "user.email", "test@example.com"], cwd=repo)
    _git(["config", "user.name", "Test"], cwd=repo)


def _commit_all(repo: Path, message: str) -> str:
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "--quiet", "-m", message], cwd=repo)
    return _git(["rev-parse", "HEAD"], cwd=repo)


def _git_file_uri(repo: Path, ref: str, subpath: str = "") -> str:
    """Build a ``git+file://`` URI for a local fixture repo.

    Uses ``Path.as_uri()`` so the drive-letter form is produced natively on
    Windows rather than hand-assembled.
    """
    uri = f"git+{repo.as_uri()}@{ref}"
    if subpath:
        uri += f"#subdirectory={subpath}"
    return uri


ROOT_BUNDLE_NAME = "ampl"
"""Name of the child repo's ROOT bundle -- deliberately collides (below) with a
name already in the registry, which is what leaves the repo with no root entry
of its own. This is the measured real-world shape: microsoft/amplifier's root
bundle name was already taken, so only its non-root ``behaviors/`` sub-bundle
entry existed, and root-only enumeration reached nothing."""


def _make_child_repo(base: Path, name: str = "cee") -> tuple[Path, str]:
    """A git repo carrying a root bundle plus a nested sub-bundle file.

    Returns ``(repo_path, first_commit_sha)``. The nested
    ``behaviors/child.yaml`` mirrors the real shape of the defect: the include
    that reaches this repo names a file *inside* it, so the entry it registers
    is a non-root one.
    """
    repo = base / f"remote-{name}"
    _init_repo(repo)
    (repo / "bundle.yaml").write_text(
        f"bundle:\n  name: {ROOT_BUNDLE_NAME}\n  version: '1.0.0'\n"
    )
    (repo / "behaviors").mkdir()
    (repo / "behaviors" / "child.yaml").write_text(
        f"bundle:\n  name: {name}\n  version: '1.0.0'\n"
    )
    (repo / "data.txt").write_text("one\n")
    first = _commit_all(repo, "first")
    return repo, first


def _advance(repo: Path) -> str:
    (repo / "data.txt").write_text("two\n")
    return _commit_all(repo, "second")


def _seed_cache(uri: str, cache_dir: Path) -> Path:
    """Clone *uri*'s repo into the cache path the handler would use.

    This is the "already cached at an older commit" starting state -- exactly
    what a real machine has after any earlier session resolved the include.
    """
    parsed = parse_uri(uri)
    handler = GitSourceHandler()
    cache_path = handler._get_cache_path(parsed, cache_dir)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    git_url = handler._build_git_url(parsed)
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--branch",
            parsed.ref or "main",
            git_url,
            str(cache_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return cache_path


def _write_parent_bundle(base: Path, includes: list[str], name: str = "bee") -> Path:
    parent = base / "parent"
    parent.mkdir(parents=True, exist_ok=True)
    lines = [f"bundle:\n  name: {name}\n  version: '1.0.0'\n", "includes:\n"]
    lines.extend(f"  - {inc}\n" for inc in includes)
    (parent / "bundle.yaml").write_text("".join(lines))
    return parent / "bundle.yaml"


@pytest.mark.asyncio
async def test_non_root_include_with_stale_cache_is_reported(tmp_path: Path) -> None:
    """A bundle reached only by a non-root include is checked, not dropped."""
    home = tmp_path / "home"
    cache = home / "cache"
    cache.mkdir(parents=True)

    child_repo, first = _make_child_repo(tmp_path)
    include_uri = _git_file_uri(child_repo, "main", "behaviors/child.yaml")
    _seed_cache(include_uri, cache)
    second = _advance(child_repo)
    assert first != second

    parent_file = _write_parent_bundle(tmp_path, [include_uri])
    registry = BundleRegistry(home=home)
    # The child repo's ROOT bundle name is already taken by an unrelated entry,
    # so loading the include cannot claim a root entry for this repo.
    registry.register({ROOT_BUNDLE_NAME: "git+https://example.invalid/other@main"})
    bundle = await registry._load_single(
        parent_file.as_uri(), auto_register=True, auto_include=True
    )

    # The premise the old comment relied on: nothing registered this repo as a
    # ROOT bundle, so root-only enumeration would never reach it.
    assert not any(
        state.is_root and state.uri.split("#")[0] == include_uri.split("#")[0]
        for state in registry._registry.values()
    )
    child_state = registry._registry.get("cee")
    assert child_state is not None and child_state.is_root is False

    # No explicit registry passed: exercise the default derived from cache_dir.
    status = await check_bundle_status(bundle, cache)

    rows = [s for s in status.sources if s.via is not None]
    assert len(rows) == 1, f"expected one transitive row, got {status.sources}"
    row = rows[0]
    assert row.cached_commit == first
    assert row.remote_commit == second
    assert row.has_update is True
    assert row.via == "bee"

    assert status.has_updates is True
    # The exact green lie this defect produced. It must not be reachable while
    # any source -- direct or transitive -- has an update.
    assert "source(s) up to date" not in status.summary
    assert status.summary.startswith("1 update(s) available")

    # Regression guard, on the same fixture: the pre-fix behaviour is still
    # reachable via include_transitive=False, and it is exactly the green lie
    # -- no row for the stale include, and has_updates False.
    old = await check_bundle_status(bundle, cache, include_transitive=False)
    assert old.has_updates is False
    assert all(s.via is None for s in old.sources)


@pytest.mark.asyncio
async def test_pinned_include_is_reported_pinned_not_updateable(
    tmp_path: Path,
) -> None:
    """An ``@<sha>`` include is pinned: reported, but never updateable."""
    home = tmp_path / "home"
    cache = home / "cache"
    cache.mkdir(parents=True)

    child_repo, first = _make_child_repo(tmp_path)
    include_uri = _git_file_uri(child_repo, first, "behaviors/child.yaml")
    _seed_cache(_git_file_uri(child_repo, "main"), cache)
    # Seed the pinned cache path too, so the row has a cached commit to show.
    _seed_cache_pinned = _git_file_uri(child_repo, first)
    handler = GitSourceHandler()
    pinned_cache = handler._get_cache_path(parse_uri(_seed_cache_pinned), cache)
    subprocess.run(
        ["git", "clone", "--quiet", str(child_repo), str(pinned_cache)],
        capture_output=True,
        text=True,
        check=True,
    )
    _advance(child_repo)

    parent_file = _write_parent_bundle(tmp_path, [include_uri])
    registry = BundleRegistry(home=home)
    bundle = await registry._load_single(
        parent_file.as_uri(), auto_register=True, auto_include=True
    )

    status = await check_bundle_status(bundle, cache, registry=registry)

    rows = [s for s in status.sources if s.via is not None]
    assert len(rows) == 1, f"expected one transitive row, got {status.sources}"
    row = rows[0]
    assert row.is_pinned is True
    assert row.has_update is False
    assert status.has_updates is False


@pytest.mark.asyncio
async def test_include_cycle_terminates(tmp_path: Path) -> None:
    """B includes C, C includes B: the walk terminates and reports both once."""
    home = tmp_path / "home"
    cache = home / "cache"
    cache.mkdir(parents=True)

    repo_b = tmp_path / "remote-bee"
    repo_c = tmp_path / "remote-cee"
    _init_repo(repo_b)
    _init_repo(repo_c)

    uri_b = _git_file_uri(repo_b, "main")
    uri_c = _git_file_uri(repo_c, "main")

    (repo_b / "bundle.yaml").write_text(
        f"bundle:\n  name: bee\n  version: '1.0.0'\nincludes:\n  - {uri_c}\n"
    )
    _commit_all(repo_b, "b")
    (repo_c / "bundle.yaml").write_text(
        f"bundle:\n  name: cee\n  version: '1.0.0'\nincludes:\n  - {uri_b}\n"
    )
    _commit_all(repo_c, "c")

    _seed_cache(uri_b, cache)
    _seed_cache(uri_c, cache)

    registry = BundleRegistry(home=home)
    bundle = await registry._load_from_path(
        GitSourceHandler()._get_cache_path(parse_uri(uri_b), cache)
    )
    bundle._source_uri = uri_b  # type: ignore[attr-defined]

    found = await collect_transitive_source_uris(
        bundle, registry=registry, cache_dir=cache, known_uris={uri_b}
    )

    # Terminated (no hang, no recursion error) and C is attributed to B.
    assert found == {uri_c: "bee"}


@pytest.mark.asyncio
async def test_deleted_cache_is_not_resolved_back_into_existence(
    tmp_path: Path,
) -> None:
    """The walk reads caches; it never resolves one, which would download.

    Resolving a missing cache would clone it, and a fresh clone reports "up to
    date" -- turning a deleted cache into a green row. The walk must instead
    read nothing and leave the disk untouched.
    """
    home = tmp_path / "home"
    cache = home / "cache"
    cache.mkdir(parents=True)

    child_repo, _first = _make_child_repo(tmp_path)
    include_uri = _git_file_uri(child_repo, "main", "behaviors/child.yaml")

    cache_path = GitSourceHandler()._get_cache_path(parse_uri(include_uri), cache)
    assert not cache_path.exists()

    assert _cached_path_for(include_uri, cache) is None
    assert not cache_path.exists(), "walk must not have cloned a missing cache"


@pytest.mark.asyncio
async def test_direct_sources_carry_no_via(tmp_path: Path) -> None:
    """``via`` stays None for a bundle's own directly-declared sources."""
    home = tmp_path / "home"
    cache = home / "cache"
    cache.mkdir(parents=True)

    parent = tmp_path / "solo"
    parent.mkdir()
    (parent / "bundle.yaml").write_text("bundle:\n  name: solo\n  version: '1.0.0'\n")

    registry = BundleRegistry(home=home)
    bundle = await registry._load_single(
        (parent / "bundle.yaml").as_uri(), auto_register=True, auto_include=True
    )

    status = await check_bundle_status(bundle, cache, registry=registry)

    assert all(s.via is None for s in status.sources)
