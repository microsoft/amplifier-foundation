"""The shared source store works where Windows long paths are off.

**The defect.** Observed on a clean Windows 11 PC with the default
``LongPathsEnabled = 0``: Git stages a repository below the store (it writes
deep files because ``core.longpaths`` is set), then the store visits every
staged file by its ordinary path to seal it. A staged file sits at
``<store>\\objects\\<64 hex>\\.source-<8>\\checkout\\<repository path>``, so an
ordinary repository path pushes it past the 259-character limit and the walk
fails with ``FileNotFoundError``. No session can start, and each attempt leaves
a staging folder that the same limit prevents cleanup from removing.

Two parts keep the store inside the limit:

* the store's own tree operations address its trees in extended-length form;
* new Windows entries use 16 digits of the 64-digit store names, so the files
  that other programs read by ordinary paths stay short.

Hosted Windows runners have long paths ON, so an ordinary run cannot see this
defect. The ``windows-long-paths-off`` CI job turns the setting off and sets
``AMPLIFIER_REQUIRE_WINDOWS_PATH_LIMIT=1``. With that variable set, the canary
and the deep tree test FAIL unless the limit is really in force.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from amplifier_foundation.sources import shared
from amplifier_foundation.sources._winpath import extended, extended_text
from amplifier_foundation.sources.git import GitSourceHandler, _with_longpaths
from amplifier_foundation.sources.shared import (
    SharedSourceStore,
    build_lock,
    build_view,
)

URL = "https://example.invalid/deep"
MODULE = Path("modules/tool-fixture")
# The length of the deep file's path in a Windows build view. It is over the
# 259-character limit; the object and staging paths are longer still.
BUILD_VIEW_LENGTH = 268


REQUIRED = os.environ.get("AMPLIFIER_REQUIRE_WINDOWS_PATH_LIMIT") == "1"
NOT_IN_FORCE = (
    "The Windows path limit is not in force (long paths are on), so this run "
    "does not prove the store works on a default Windows PC."
)


def git(root, *args):
    return subprocess.check_output(
        _with_longpaths(["git", *args]), cwd=root, text=True
    ).strip()


def test_windows_path_limit_canary(tmp_path):
    """State plainly whether this run can see the defect at all.

    One existing file, two spellings. Only the length differs, so a refusal of
    the ordinary spelling can have no other cause.
    """
    if os.name != "nt":
        assert not REQUIRED, "The Windows path limit can be required on Windows only."
        pytest.skip("The path limit exists on Windows only.")
    folder = tmp_path
    while len(str(folder)) < 280:
        folder = folder / ("d" * 30)
    extended(folder).mkdir(parents=True)
    (extended(folder) / "file.txt").write_text("reachable")
    ordinary = folder / "file.txt"
    assert (extended(ordinary)).read_text() == "reachable"
    limited = not os.path.exists(ordinary)
    if REQUIRED:
        assert limited, NOT_IN_FORCE
        with pytest.raises(OSError):
            ordinary.read_text()
    elif not limited:
        pytest.skip(NOT_IN_FORCE)


def commit(repo, message):
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def new_repository(repo):
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "bundle.md").write_text("bundle")
    (repo / MODULE).mkdir(parents=True)
    (repo / MODULE / "pyproject.toml").write_text(
        '[project]\nname="fixture"\nversion="1"\n'
    )
    return repo


def deep_relative(length):
    """A repository path of exactly ``length`` characters, in short segments."""
    parts, name = ["docs"], "evidence.txt"
    while True:
        room = length - len("/".join(parts)) - 1 - len(name) - 1
        if room <= 0:
            break
        parts.append(("lane-" + "x" * 25)[: min(30, room)])
    relative = PurePosixPath(*parts, name)
    relative = relative.with_name("e" * (length - len(str(relative.parent)) - 5) + ".txt")
    assert len(str(relative)) == length
    return Path(relative)


@pytest.fixture
def deep(tmp_path):
    """A clean repository whose fixture files fit the ordinary limit."""
    store = SharedSourceStore(tmp_path / "s")
    repo = new_repository(tmp_path / "r")
    # "\\builds\\" + 16 digits + "\\" precede the repository path in a view.
    relative = deep_relative(BUILD_VIEW_LENGTH - len(str(store.root)) - 25)
    assert len(str(repo / relative)) < 250
    (repo / relative).parent.mkdir(parents=True)
    (repo / relative).write_text("deep evidence")
    return store, repo, commit(repo, "deep fixture"), relative


def test_extended_text_contract():
    prefix = "\\\\?\\"
    assert extended_text("C:\\Users\\x\\store") == prefix + "C:\\Users\\x\\store"
    assert extended_text("C:/Users/x/store") == prefix + "C:\\Users\\x\\store"
    assert extended_text("C:\\a\\.\\b\\..\\c") == prefix + "C:\\a\\c"
    assert extended_text("\\\\server\\share\\x") == prefix + "UNC\\server\\share\\x"
    # Windows does not normalize this form. Never prefix it twice.
    assert extended_text(prefix + "C:\\a") == prefix + "C:\\a"
    assert extended_text(extended_text("\\\\server\\share\\x")) == (
        prefix + "UNC\\server\\share\\x"
    )
    for refused in ("relative\\x", "C:relative", "\\rooted", "\\\\.\\COM1", ""):
        with pytest.raises(ValueError, match="absolute"):
            extended_text(refused)


@pytest.mark.skipif(os.name == "nt", reason="identity off Windows")
def test_extended_is_the_argument_itself_off_windows(tmp_path):
    assert extended(tmp_path) is tmp_path
    text = str(tmp_path)
    assert extended(text) is text


@pytest.mark.skipif(os.name != "nt", reason="native extended-length form")
def test_extended_names_the_same_directory_on_windows(tmp_path):
    (tmp_path / "file").write_text("content")
    assert str(extended(tmp_path)).startswith("\\\\?\\")
    assert (extended(tmp_path) / "file").read_text() == "content"
    assert extended(extended(tmp_path)) == extended(tmp_path)
    assert extended(str(tmp_path) + "\\.\\x\\..") == extended(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("short", [False, True])
async def test_new_entries_use_the_selected_name_width(tmp_path, monkeypatch, short):
    monkeypatch.setattr(shared, "_short_names", lambda: short)
    repo = new_repository(tmp_path / "r")
    revision = commit(repo, "fixture")
    store = SharedSourceStore(tmp_path / "s")
    name, key = shared.source_id(URL, ""), shared.source_id(URL, revision)
    target = await store.ensure(URL, revision, existing=repo)
    assert target == store.root / "objects" / (name[:16] if short else name) / revision
    assert store.checkout(URL, revision) == target
    assert store.verify(URL, revision) == target
    build, immutable = build_view(target / MODULE)
    view = store.root / "builds" / (key[:16] if short else key)
    assert immutable and build == view / MODULE
    with build_lock(build) as lock:
        assert lock.lock_file == str(view) + ".lock"
    assert build_view(target / MODULE) == (build, True)
    assert not list(store.root.rglob(".source-*"))
    assert not list(store.root.rglob(".build-*"))


@pytest.mark.asyncio
async def test_existing_full_names_stay_in_use(tmp_path, monkeypatch):
    """An installed store keeps working; only new entries get the short name."""
    repo = new_repository(tmp_path / "r")
    first = commit(repo, "first")
    store = SharedSourceStore(tmp_path / "s")
    name = shared.source_id(URL, "")
    monkeypatch.setattr(shared, "_short_names", lambda: False)
    old = await store.ensure(URL, first, existing=repo)
    old_build, _ = build_view(old / MODULE)
    assert old.parent.name == name and old_build.parents[1].name == shared.source_id(URL, first)

    monkeypatch.setattr(shared, "_short_names", lambda: True)
    assert store.checkout(URL, first) == old
    assert await store.ensure(URL, first) == old
    assert store.verify(URL, first) == old
    assert build_view(old / MODULE) == (old_build, True)
    with build_lock(old_build):
        pass
    (repo / "bundle.md").write_text("second")
    second = commit(repo, "second")
    new = await store.ensure(URL, second, existing=repo)
    assert new == store.root / "objects" / name[:16] / second
    new_build, _ = build_view(new / MODULE)
    assert new_build.parents[1].name == shared.source_id(URL, second)[:16]
    # A short entry stays selected when a full one appears beside it later,
    # as an older library does after an update rollback.
    monkeypatch.setattr(shared, "_short_names", lambda: False)
    duplicate = await store.ensure(URL, second, existing=repo)
    assert duplicate == store.root / "objects" / name / second
    monkeypatch.setattr(shared, "_short_names", lambda: True)
    assert store.checkout(URL, second) == new
    assert store.verify(URL, second) == new


@pytest.mark.asyncio
async def test_short_name_clash_fails_and_removes_nothing(tmp_path, monkeypatch):
    """A name locates an entry. The recorded identity decides, in full."""
    monkeypatch.setattr(shared, "_short_names", lambda: True)

    def clashing(url, ref):
        digest = hashlib.sha256((url + "@" + ref).encode()).hexdigest()
        return "0" * 16 + digest[16:]

    monkeypatch.setattr(shared, "source_id", clashing)
    repo = new_repository(tmp_path / "r")
    first = commit(repo, "first")
    store = SharedSourceStore(tmp_path / "s")
    owner = await store.ensure(URL, first, existing=repo)
    with pytest.raises(ValueError, match="identity changed"):
        await store.ensure("https://example.invalid/other", first, existing=repo)
    assert store.verify(URL, first) == owner
    build, _ = build_view(owner / MODULE)
    (repo / "bundle.md").write_text("second")
    second = await store.ensure(URL, commit(repo, "second"), existing=repo)
    with pytest.raises(ValueError, match="provenance changed"):
        build_view(second / MODULE)
    assert build_view(owner / MODULE) == (build, True)
    assert (build / "pyproject.toml").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["adopt", "clone"])
async def test_deep_checkout_is_sealed_published_and_built(deep, monkeypatch, transport):
    store, repo, revision, relative = deep
    if transport == "clone":
        clone = GitSourceHandler._clone_at_commit
        monkeypatch.setattr(
            GitSourceHandler,
            "_clone_at_commit",
            lambda handler, url, sha, path: clone(handler, str(repo), sha, path),
        )
        target = await store.ensure(URL, revision)
    else:
        target = await store.ensure(URL, revision, existing=repo)
    assert store.verify(URL, revision) == target
    assert not list(target.parent.glob(".source-*"))
    sealed = extended(target / relative)
    assert sealed.read_text() == "deep evidence"
    assert not sealed.stat().st_mode & 0o222  # The seal reached the deep file.

    # The limit is in force when the same existing file cannot be reached by
    # its ordinary path. Only the form differs, so only length can explain it.
    limited = not os.path.exists(target / relative)
    if REQUIRED:
        assert limited, NOT_IN_FORCE

    build, immutable = build_view(target / MODULE)
    view = build.parents[1]
    assert immutable and build == view / MODULE
    assert (build / "pyproject.toml").is_file()  # A backend reads ordinary paths.
    copied = extended(view / relative)
    assert copied.read_text() == "deep evidence"
    assert copied.stat().st_mode & 0o200
    with build_lock(build):
        pass
    assert build_view(target / MODULE) == (build, True)
    assert not list(view.parent.glob(".build-*"))
    if os.name == "nt":
        assert len(target.parent.name) == 16 and len(view.name) == 16
        # Over the limit in the view, in the object and in staging.
        assert len(str(view / relative)) == BUILD_VIEW_LENGTH
        assert len(str(target / relative)) > BUILD_VIEW_LENGTH


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["source", "build"])
async def test_failed_deep_stage_is_removed(deep, monkeypatch, stage):
    """A failed attempt must not leave a tree that later attempts add to."""
    store, repo, revision, relative = deep
    target = store.checkout(URL, revision)
    primary = OSError("injected after the deep tree was written")
    atomic = shared.atomic

    def fail(path, value):
        if ".source-" in str(path) or ".build-" in str(path):
            raise primary
        return atomic(path, value)

    with monkeypatch.context() as patch:
        if stage == "source":
            patch.setattr(shared, "atomic", fail)
            with pytest.raises(OSError) as caught:
                await store.ensure(URL, revision, existing=repo)
        else:
            assert await store.ensure(URL, revision, existing=repo) == target
            patch.setattr(shared, "atomic", fail)
            with pytest.raises(OSError) as caught:
                build_view(target / MODULE)
    assert caught.value is primary
    assert not getattr(primary, "__notes__", [])  # Cleanup itself succeeded.
    assert not list(target.parent.glob(".source-*"))
    assert not list((store.root / "builds").glob(".build-*"))
    # A later attempt publishes; nothing partial was adopted.
    assert await store.ensure(URL, revision, existing=repo) == target
    build, immutable = build_view(target / MODULE)
    assert immutable and extended(build.parents[1] / relative).is_file()
