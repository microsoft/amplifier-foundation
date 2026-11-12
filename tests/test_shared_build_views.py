"""Build publication exercises the real bind/resolve path, never live caches."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from filelock import FileLock, Timeout

from amplifier_foundation.sources import shared


URL = "https://example.invalid/build-input"
MODULE = Path("modules/tool-fixture")


def git(root, *args):
    return subprocess.check_output(
        ["git", "-c", "core.longpaths=true", *args], cwd=root, text=True
    ).strip()


@pytest.fixture
def source(tmp_path, monkeypatch):
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / MODULE).mkdir(parents=True)
    (repo / MODULE / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="1"\n')
    (repo / MODULE / "regular.sock").write_text("ordinary build input")
    (repo / MODULE / "fsmonitor--daemon.ipc").write_text("not a socket")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    revision = git(repo, "rev-parse", "HEAD")
    store = shared.SharedSourceStore(tmp_path / "store")
    cache = tmp_path / "generation/cache"
    monkeypatch.setenv("AMPLIFIER_SOURCE_STORE", str(store.root))

    async def bind_resolve():
        await store.bind(cache, URL, "main", revision, existing=repo)
        return await shared.resolve_shared_source(
            "git+" + URL + "@main#subdirectory=" + MODULE.as_posix(), cache
        )

    result = asyncio.run(bind_resolve())
    return result.source_root, result.active_path


def target_for(root):
    data = json.loads((root / ".amplifier_cache_meta.json").read_text())
    return root.parents[2] / "builds" / shared.source_id(data["git_url"], data["commit"])


def snapshot(root):
    """Bytes, link text, modes and index are unchanged, not just package content."""
    result = {}
    for path in (root, *root.rglob("*")):
        mode = path.lstat().st_mode
        value = None
        if stat.S_ISLNK(mode):
            value = os.readlink(path)
        elif stat.S_ISREG(mode):
            value = hashlib.sha256(path.read_bytes()).hexdigest()
        result[str(path.relative_to(root))] = (mode, value)
    return result


def add_link(root, name, destination):
    module = root / MODULE
    old = module.stat().st_mode
    module.chmod(0o700)
    try:
        (module / name).symlink_to(destination)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    finally:
        module.chmod(stat.S_IMODE(old))


def symlink(path, destination, **kwargs):
    try:
        path.symlink_to(destination, **kwargs)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")


def test_publish_writable_completed_view_and_preserve_source(source):
    root, module = source
    before = snapshot(root)
    build, immutable = shared.build_view(module)
    target = target_for(root)
    assert immutable and build == target / MODULE
    data = json.loads((target / ".amplifier_cache_meta.json").read_text())
    assert data["immutable"] is False
    assert data["buildInput"] is True and data["buildViewVersion"] == 1
    assert data["commit"] == root.name
    assert (build / "regular.sock").read_text() == "ordinary build input"
    assert (build / "fsmonitor--daemon.ipc").read_text() == "not a socket"
    for path in (target, *target.rglob("*")):
        assert path.stat().st_mode & 0o200
    with shared.build_lock(build):
        (build / "generated.txt").write_text("backend output")
    assert shared.build_view(module) == (build, True)
    assert (build / "generated.txt").read_text() == "backend output"
    assert shared.build_lock(build).lock_file == str(target) + ".lock"
    assert snapshot(root) == before
    assert not list(target.parent.glob(".build-*"))


@pytest.mark.skipif(os.name == "nt", reason="actual Unix socket/FIFO policy")
def test_original_socket_failure_then_quarantined_partial_positive(source):
    root, module = source
    # Reproduce the nested local Git runtime endpoint, not a mocked filename.
    module.chmod(0o700)
    nested = module / ".git"
    nested.mkdir()
    git(module, "init", "-b", "main")
    endpoint = socket.socket(socket.AF_UNIX)
    try:
        # AF_UNIX pathname limits are smaller than commit-addressed store paths.
        cwd = Path.cwd()
        try:
            os.chdir(nested)
            endpoint.bind("fsmonitor--daemon.ipc")
        finally:
            os.chdir(cwd)
        os.mkfifo(nested / "runtime.fifo")
        (nested / "endpoint-link").symlink_to("fsmonitor--daemon.ipc")
        module.chmod(0o555)
        before = snapshot(root)
        target = target_for(root)
        target.parent.mkdir()
        with pytest.raises(shutil.Error) as original:
            shutil.copytree(root, target, symlinks=True)
        # Linux commonly returns ENXIO(6); this is not macOS ENOTSUP(102).
        assert "fsmonitor--daemon.ipc" in str(original.value)
        partial = json.loads((target / ".amplifier_cache_meta.json").read_text())
        assert partial["immutable"] is True and "buildInput" not in partial
        target.chmod(0o700)
        (target / "unknown-local-note").write_text("preserve this too")
        old_inode = target.stat().st_ino
        build, immutable = shared.build_view(module)
        assert immutable and not (build / ".git/fsmonitor--daemon.ipc").exists()
        assert not (build / ".git/runtime.fifo").exists()
        assert (build / ".git/endpoint-link").is_symlink()
        assert os.readlink(build / ".git/endpoint-link") == "fsmonitor--daemon.ipc"
        assert not (target / "unknown-local-note").exists()
        retained = list(target.parent.glob(f".{target.name}.incomplete-*/checkout"))
        assert len(retained) == 1 and retained[0].stat().st_ino == old_inode
        assert (retained[0] / "unknown-local-note").read_text() == "preserve this too"
        assert shared.build_view(module) == (build, True)
        assert isinstance(shared.build_lock(build), FileLock)
        assert snapshot(root) == before
    finally:
        endpoint.close()


@pytest.mark.parametrize("marked", [False, True])
def test_local_mutable_or_unmarked_source_bypasses_copy(tmp_path, marked):
    if marked:
        (tmp_path / ".amplifier_cache_meta.json").write_text('{"immutable": false}')
    assert shared.build_view(tmp_path) == (tmp_path, False)
    with shared.build_lock(tmp_path):
        pass
    assert not (tmp_path.parent / "builds").exists()


@pytest.mark.parametrize("kind", ["unmarked", "malformed", "mismatch", "flags", "version"])
def test_invalid_target_retained_not_reused_or_deleted(source, kind):
    root, module = source
    target = target_for(root)
    target.mkdir(parents=True)
    (target / ".git").mkdir()
    (target / "sentinel").write_text("unowned work")
    data = json.loads((root / ".amplifier_cache_meta.json").read_text())
    if kind == "mismatch":
        data["commit"] = "f" * 40
    elif kind == "flags":
        data["immutable"] = False
    elif kind == "version":
        data.update(immutable=False, buildInput=True, buildViewVersion=99)
    if kind != "unmarked":
        (target / ".amplifier_cache_meta.json").write_text(
            "not JSON" if kind == "malformed" else json.dumps(data)
        )
    before = snapshot(target)
    with pytest.raises((ValueError, FileNotFoundError)):
        shared.build_view(module)
    assert snapshot(target) == before
    assert not list(target.parent.glob(f".{target.name}.incomplete-*"))


def test_legacy_completed_target_is_validated_and_reused(source):
    root, module = source
    build, _ = shared.build_view(module)
    marker = target_for(root) / ".amplifier_cache_meta.json"
    data = json.loads(marker.read_text())
    del data["buildViewVersion"]
    marker.write_text(json.dumps(data))
    assert shared.build_view(module) == (build, True)
    assert isinstance(shared.build_lock(build), FileLock)


@pytest.mark.parametrize("phase", ["copy", "chmod", "metadata", "rename"])
def test_failed_preparation_never_publishes_and_retry_succeeds(source, monkeypatch, phase):
    root, module = source
    before = snapshot(root)
    target = target_for(root)
    primary = OSError("injected " + phase)
    with monkeypatch.context() as patch:
        if phase == "copy":
            original = shared.shutil.copytree

            def copy(src, dst, *args, **kwargs):
                result = original(src, dst, *args, **kwargs)
                if src == root:
                    raise primary
                return result

            patch.setattr(shared.shutil, "copytree", copy)
        elif phase == "chmod":
            original = Path.chmod

            def chmod(path, mode, **kwargs):
                if ".build-" in str(path) and path.name == "regular.sock":
                    raise primary
                return original(path, mode, **kwargs)

            patch.setattr(Path, "chmod", chmod)
        elif phase == "metadata":
            patch.setattr(shared, "atomic", lambda *a, **kw: (_ for _ in ()).throw(primary))
        else:
            original = Path.rename

            def rename(path, destination):
                if destination == target:
                    assert not target.exists()
                    record = json.loads((path / ".amplifier_cache_meta.json").read_text())
                    assert record["buildInput"] and record["immutable"] is False
                    assert (path / MODULE / "regular.sock").stat().st_mode & 0o200
                    raise primary
                return original(path, destination)

            patch.setattr(Path, "rename", rename)
        with pytest.raises(OSError) as caught:
            shared.build_view(module)
        assert caught.value is primary
    assert not target.exists()
    assert not list(target.parent.glob(".build-*"))
    assert shared.build_view(module)[1]
    assert snapshot(root) == before


def test_cleanup_failure_preserves_original_error_and_owned_residue(source, monkeypatch):
    root, module = source
    primary = OSError("copy primary")
    original_copy = shared.shutil.copytree

    def copy(src, dst, *args, **kwargs):
        result = original_copy(src, dst, *args, **kwargs)
        if src == root:
            raise primary
        return result

    with monkeypatch.context() as patch:
        patch.setattr(shared.shutil, "copytree", copy)
        patch.setattr(shared, "_remove_owned_stage", lambda *a: (_ for _ in ()).throw(OSError("cleanup secondary")))
        with pytest.raises(OSError) as caught:
            shared.build_view(module)
    assert caught.value is primary
    assert "cleanup secondary" in " ".join(primary.__notes__)
    assert not target_for(root).exists()
    residue = list(target_for(root).parent.glob(".build-*"))
    assert len(residue) == 1
    before = snapshot(residue[0])
    assert shared.build_view(module)[1]
    assert snapshot(residue[0]) == before  # Never collect another invocation's stage.


def test_successful_publication_survives_cleanup_failure(source, monkeypatch):
    root, module = source
    with monkeypatch.context() as patch:
        patch.setattr(shared, "_remove_owned_stage", lambda *a: (_ for _ in ()).throw(OSError("cleanup")))
        with pytest.raises(OSError, match="cleanup"):
            shared.build_view(module)
    assert shared.build_view(module) == (target_for(root) / MODULE, True)


def test_competing_creators_serialize_completed_publication_and_backend_lock(source, monkeypatch):
    root, module = source
    target = target_for(root)
    entered, release = threading.Event(), threading.Event()
    copies = []
    original = shared.shutil.copytree

    def copy(src, dst, *args, **kwargs):
        if src == root:
            copies.append(dst)
            entered.set()
            assert release.wait(10)
        return original(src, dst, *args, **kwargs)

    monkeypatch.setattr(shared.shutil, "copytree", copy)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(shared.build_view, module)
        assert entered.wait(10)
        second = pool.submit(shared.build_view, module)
        assert not target.exists()
        with pytest.raises(Timeout):
            FileLock(str(target) + ".lock", timeout=0).acquire()
        release.set()
        assert first.result(timeout=10) == second.result(timeout=10)
    assert len(copies) == 1
    with shared.build_lock(target / MODULE):
        with pytest.raises(Timeout):
            FileLock(str(target) + ".lock", timeout=0).acquire()


@pytest.mark.parametrize("destination", ["../tool-fixture/regular.sock", "missing.sock"])
def test_contained_relative_links_preserved_literally(source, destination):
    root, module = source
    add_link(root, "content-link", destination)
    before = snapshot(root)
    build, _ = shared.build_view(module)
    assert (build / "content-link").is_symlink()
    assert os.readlink(build / "content-link") == destination
    assert snapshot(root) == before


@pytest.mark.parametrize("kind", ["outside", "absolute-source", "relative-outside"])
def test_escaping_content_links_cannot_reach_immutable_or_external_paths(source, tmp_path, kind):
    root, module = source
    outside = tmp_path / "external"
    outside.write_text("untouched")
    destination = str(outside)
    if kind == "absolute-source":
        destination = str(module / "regular.sock")
    elif kind == "relative-outside":
        destination = "../../../../../../external"
    add_link(root, "escape", destination)
    before = snapshot(root)
    with pytest.raises(ValueError, match="escaping symlink"):
        shared.build_view(module)
    assert not target_for(root).exists()
    assert snapshot(root) == before and outside.read_text() == "untouched"
    with pytest.raises(ValueError, match="subpath|escaping symlink"):
        shared.build_view(module / "escape")


@pytest.mark.parametrize("control", ["builds", "target", "marker", "git", "lock"])
def test_symlink_control_paths_rejected_without_external_changes(source, tmp_path, control):
    root, module = source
    target = target_for(root)
    outside = tmp_path / "external"
    outside.mkdir()
    (outside / "sentinel").write_text("untouched")
    target.parent.mkdir()
    try:
        if control == "builds":
            target.parent.rmdir()
            target.parent.symlink_to(outside, target_is_directory=True)
        elif control == "target":
            target.symlink_to(outside, target_is_directory=True)
        elif control == "lock":
            Path(str(target) + ".lock").symlink_to(outside / "sentinel")
        else:
            target.mkdir()
            data = json.loads((root / ".amplifier_cache_meta.json").read_text())
            if control == "marker":
                (outside / "meta").write_text(json.dumps(data))
                (target / ".amplifier_cache_meta.json").symlink_to(outside / "meta")
            else:
                (target / ".amplifier_cache_meta.json").write_text(json.dumps(data))
                (target / ".git").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    before = snapshot(outside)
    with pytest.raises(ValueError):
        shared.build_view(module)
    assert snapshot(outside) == before


@pytest.mark.parametrize("damage", ["missing", "partial", "provenance", "escape"])
def test_build_lock_fails_closed_for_damaged_build_view(source, tmp_path, damage):
    root, module = source
    build, _ = shared.build_view(module)
    marker = target_for(root) / ".amplifier_cache_meta.json"
    data = json.loads(marker.read_text())
    if damage == "missing":
        marker.unlink()
    elif damage == "partial":
        data.pop("buildInput")
        data["immutable"] = True
        marker.write_text(json.dumps(data))
    elif damage == "provenance":
        data["commit"] = "f" * 40
        marker.write_text(json.dumps(data))
    else:
        symlink(build / "escape", tmp_path, target_is_directory=True)
    with pytest.raises((ValueError, FileNotFoundError)):
        with shared.build_lock(build):
            pytest.fail("damaged build must not enter backend")


def test_forged_immutable_marker_outside_object_namespace_is_not_authority(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".amplifier_cache_meta.json").write_text(json.dumps({
        "git_url": URL, "commit": "a" * 40, "immutable": True,
    }))
    with pytest.raises(ValueError, match="owned source store"):
        shared.build_view(tmp_path)


@pytest.mark.parametrize("redirect", ["commondir", "objects/info/alternates"])
def test_build_rejects_git_metadata_redirects(source, tmp_path, redirect):
    root, module = source
    control = root / ".git" / redirect
    parent_mode = control.parent.stat().st_mode
    control.parent.chmod(0o700)
    control.write_text(str(tmp_path / "external-git"))
    control.parent.chmod(stat.S_IMODE(parent_mode))
    before = snapshot(root)
    with pytest.raises(ValueError, match="redirects Git metadata"):
        shared.build_view(module)
    assert snapshot(root) == before and not target_for(root).exists()


def test_cleanup_identity_change_does_not_delete_replacement(source, monkeypatch):
    root, module = source
    target = target_for(root)
    original = shared._remove_owned_stage
    displaced = []
    primary = OSError("metadata primary")

    def cleanup(path, identity):
        # Model another owner replacing a staging path; custody stays with inode.
        moved = path.with_name(path.name + "-retained")
        path.rename(moved)
        displaced.append(moved)
        path.mkdir()
        (path / "sentinel").write_text("not ours")
        return original(path, identity)

    with monkeypatch.context() as patch:
        patch.setattr(shared, "atomic", lambda *a, **kw: (_ for _ in ()).throw(primary))
        patch.setattr(shared, "_remove_owned_stage", cleanup)
        with pytest.raises(OSError) as caught:
            shared.build_view(module)
    assert caught.value is primary
    assert "ownership changed" in " ".join(primary.__notes__)
    assert (displaced[0].with_name(displaced[0].name.removesuffix("-retained")) / "sentinel").read_text() == "not ours"
    assert not target.exists()


def test_recovery_rename_failure_preserves_old_partial(source, monkeypatch):
    root, module = source
    target = target_for(root)
    target.parent.mkdir()
    shutil.copytree(root, target, symlinks=True)
    before = snapshot(target)
    original = Path.rename

    def rename(path, destination):
        if path == target:
            raise OSError("quarantine rename failed")
        return original(path, destination)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", rename)
        with pytest.raises(OSError, match="quarantine rename failed"):
            shared.build_view(module)
    assert snapshot(target) == before
    assert not list(target.parent.glob(".build-*"))
    assert shared.build_view(module)[1]


def test_nested_package_metadata_does_not_mask_provenance_or_lock(source):
    root, module = source
    old = module.stat().st_mode
    module.chmod(0o700)
    (module / ".amplifier_cache_meta.json").write_text('{"immutable": false}')
    module.chmod(stat.S_IMODE(old))
    before = snapshot(root)
    build, immutable = shared.build_view(module)
    assert immutable and build == target_for(root) / MODULE
    assert shared.build_lock(build).lock_file == str(target_for(root)) + ".lock"
    assert snapshot(root) == before


def test_source_contained_exit_reentry_link_rejected_after_relocation(source):
    root, module = source
    mode = root.stat().st_mode
    root.chmod(0o700)
    try:
        symlink(root / "alias", f"../{root.name}/modules", target_is_directory=True)
    finally:
        root.chmod(stat.S_IMODE(mode))
    assert (root / "alias").resolve().is_relative_to(root)
    before = snapshot(root)
    with pytest.raises(ValueError, match="escaping symlink"):
        shared.build_view(module)
    assert not target_for(root).exists() and snapshot(root) == before


def test_backend_validation_waits_for_current_backend_writer(source, monkeypatch):
    root, module = source
    build, _ = shared.build_view(module)
    target = target_for(root)
    inspected = threading.Event()
    original = shared._build_links

    def inspect(path):
        if path == target:
            inspected.set()
        return original(path)

    monkeypatch.setattr(shared, "_build_links", inspect)
    # A cooperative backend may leave a transient link while it holds custody.
    with ThreadPoolExecutor(1) as pool:
        with shared.build_lock(build):
            inspected.clear()
            symlink(build / "transient", root, target_is_directory=True)

            def backend():
                with shared.build_lock(build):
                    return True

            pending = pool.submit(backend)
            assert not inspected.wait(0.1)
            (build / "transient").unlink()
        assert pending.result(timeout=10)
    assert inspected.is_set()


@pytest.mark.parametrize("damage", ["missing", "mutable"])
def test_object_namespace_cannot_bypass_materialization_with_damaged_marker(source, damage):
    root, module = source
    marker = root / ".amplifier_cache_meta.json"
    root.chmod(0o700)
    marker.chmod(0o600)
    if damage == "missing":
        marker.unlink()
    else:
        marker.write_text('{"immutable": false}')
    root.chmod(0o555)
    before = snapshot(root)
    with pytest.raises((ValueError, FileNotFoundError)):
        shared.build_view(module)
    assert snapshot(root) == before


def test_unsupported_special_type_is_not_silently_omitted(tmp_path, monkeypatch):
    path = tmp_path / "device"
    path.write_text("fixture")
    original = Path.lstat

    class Device:
        st_mode = stat.S_IFCHR

    def lstat(value, **kwargs):
        return Device() if value == path else original(value, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError, match="Unsupported special file"):
        shared._build_ignore(tmp_path, ["device"])


def test_nested_store_lookalikes_cannot_mask_outer_object_or_build_lock(source):
    root, _ = source
    root.chmod(0o700)
    nested = root / "vendor/objects" / ("a" * 64) / ("b" * 40)
    package = nested / "builds" / ("c" * 64) / "package"
    package.mkdir(parents=True)
    (nested / ".amplifier_cache_meta.json").write_text('{"immutable": false}')
    (package.parent / ".amplifier_cache_meta.json").write_text('{"buildInput": true}')
    root.chmod(0o555)
    before = snapshot(root)
    build, immutable = shared.build_view(package)
    target = target_for(root)
    assert immutable and build == target / package.relative_to(root)
    with shared.build_lock(build) as lock:
        assert lock.lock_file == str(target) + ".lock"
    assert snapshot(root) == before


@pytest.mark.parametrize("usage", ["direct", "proxy", "context"])
def test_all_lock_acquisition_forms_validate_and_release_on_failure(source, usage):
    root, module = source
    build, _ = shared.build_view(module)
    target = target_for(root)
    (target / ".amplifier_cache_meta.json").unlink()
    lock = shared.build_lock(build)
    with pytest.raises(FileNotFoundError):
        if usage == "direct":
            lock.acquire()
        elif usage == "proxy":
            with lock.acquire():
                pytest.fail("invalid build entered")
        else:
            with lock:
                pytest.fail("invalid build entered")
    assert not lock.is_locked
    with FileLock(str(target) + ".lock", timeout=0):
        pass


def baseline_build_views():
    """Optional exact pre-correction source, supplied by the evidence runner."""
    path = os.environ.get("AMPLIFIER_BUILD_VIEW_BASELINE")
    if not path:
        return None
    spec = importlib.util.spec_from_file_location(
        "amplifier_foundation.sources.baseline_build_views", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def retain_measurement(tmp_path, name, data):
    folder = Path(os.environ.get("AMPLIFIER_TEST_ARTIFACTS", tmp_path))
    (folder / name).write_text(json.dumps(data, indent=2))
    print(name, json.dumps(data, sort_keys=True))


@pytest.mark.parametrize("relative_input", [False, True])
def test_real_resolve_symlink_then_parent_selects_same_package_and_backend(
    tmp_path, monkeypatch, relative_input
):
    repo = tmp_path / "selection-repository"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    for name in ("a", "b"):
        package = repo / name / "pkg"
        package.mkdir(parents=True)
        (package / "pyproject.toml").write_text(
            f'[project]\nname="package-{name}"\nversion="1"\n'
        )
    (repo / "b/sub").mkdir()
    (repo / "b/sub/.keep").write_text("tracked directory")
    expected = "a" if os.name == "nt" else "b"
    unselected = "b" if os.name == "nt" else "a"
    (repo / unselected / "missing").mkdir()
    (repo / unselected / "missing/sentinel").write_text("must not select this")
    # Windows stores link target text literally; forward slashes can create a
    # reparse point that lstat recognizes but traversal rejects with EINVAL.
    symlink(repo / "a/link", str(Path("..") / "b/sub"), target_is_directory=True)
    assert (repo / "a/link/.keep").read_text() == "tracked directory"
    git(repo, "add", ".")
    git(repo, "commit", "-m", "distinguishable packages and contained alias")
    revision = git(repo, "rev-parse", "HEAD")
    store = shared.SharedSourceStore(tmp_path / "store")
    cache = tmp_path / "generation/cache"
    monkeypatch.setenv("AMPLIFIER_SOURCE_STORE", str(store.root))

    async def resolve():
        await store.bind(cache, URL, "main", revision, existing=repo)
        return await shared.resolve_shared_source(
            "git+" + URL + "@main#subdirectory=a/link/../pkg", cache
        )

    result = asyncio.run(resolve())
    root = result.source_root
    links = {}
    for label, base in (("repository", repo), ("published", root)):
        link = base / "a/link"
        info = link.lstat()
        links[label] = {
            "mode": info.st_mode, "is_symlink": link.is_symlink(),
            "reparse_tag": getattr(info, "st_reparse_tag", None),
            "target": os.readlink(link) if link.is_symlink() else None,
        }
        try:
            links[label]["standalone_resolve"] = link.resolve(strict=True).relative_to(base).as_posix()
        except OSError as error:
            # Diagnostic only: raw child access and full requested selection
            # below must still succeed. Never swallow a selected-path error.
            links[label]["standalone_resolve_error"] = {
                "errno": error.errno, "winerror": getattr(error, "winerror", None),
            }
        assert link.is_symlink(), links
        assert os.readlink(link).replace("\\", "/") == "../b/sub"
        assert (link / ".keep").read_text() == "tracked directory"
    config = subprocess.run(
        ["git", "config", "--get", "core.symlinks"], cwd=repo,
        text=True, capture_output=True, check=False,
    )
    native = result.active_path.resolve(strict=True)
    access = result.active_path / "pyproject.toml"
    direct_name = "package-a" if "package-a" in access.read_text() else "package-b"
    identity = {
        name: access.samefile(root / name / "pkg/pyproject.toml")
        for name in ("a", "b")
    }
    observation = {
        "os_name": os.name, "python": sys.version, "links": links,
        "core_symlinks": config.stdout.strip() if config.returncode == 0 else "unset",
        "git_index_mode": git(repo, "ls-files", "--stage", "a/link").split()[0],
        "native_resolved": native.relative_to(root).as_posix(),
        "direct_access_package": direct_name, "direct_samefile": identity,
    }
    print("native-selection-observation", json.dumps(observation, sort_keys=True))
    assert native == root / expected / "pkg", observation
    assert direct_name == f"package-{expected}" and identity[expected], observation
    before = snapshot(root)
    selected = result.active_path
    if relative_input:
        monkeypatch.chdir(tmp_path)
        selected = selected.relative_to(tmp_path)
    build, immutable = shared.build_view(selected)
    target = target_for(root)
    assert immutable and build == target / expected / "pkg"
    assert f"package-{expected}" in (build / "pyproject.toml").read_text()
    assert shared.build_view(selected) == (build, True)
    with pytest.raises(ValueError, match="subpath"):
        shared.build_view(root / "a/link/../missing")
    with pytest.raises(ValueError, match="subpath"):
        with shared.build_lock(target / "a/link/../missing"):
            pytest.fail("missing native selection cannot use the other existing package")
    with pytest.raises(ValueError, match="subpath"):
        asyncio.run(shared.resolve_shared_source(
            "git+" + URL + "@main#subdirectory=a/link/../missing", cache
        ))
    # Direct backend path validation follows the same non-normalized traversal.
    alias = target / "a/link/../pkg"
    with shared.build_lock(alias) as lock:
        assert lock.lock_file == str(target) + ".lock"
        assert lock.relative == Path("a/link/../pkg")
        assert shared._build_subpath(target, lock.relative) == build
    baseline = baseline_build_views()
    negative = None
    if baseline is not None:
        wrong, _ = baseline.build_view(selected)
        assert wrong == target / "a/pkg"
        assert "package-a" in (wrong / "pyproject.toml").read_text()
        negative = {
            "selected": "a/pkg", "package": "package-a",
            "wrong_on_this_platform": os.name != "nt",
        }
    assert snapshot(root) == before
    retain_measurement(tmp_path, f"foundation1292-selection-{relative_input}.json", {
        "requested": "a/link/../pkg", "link": "../b/sub",
        "relative_input": relative_input, "resolved": f"{expected}/pkg",
        "candidate_build": f"{expected}/pkg", "candidate_package": f"package-{expected}",
        "native_observation": observation,
        "baseline_b124": negative, "immutable_snapshot_unchanged": True,
    })


@pytest.mark.parametrize("modules,outputs", [(1, 0), (4, 64), (16, 256)])
def test_sibling_reuse_work_is_not_full_tree_per_module(
    source, tmp_path, monkeypatch, modules, outputs
):
    root, _ = source
    siblings = [root / f"module-{index}" for index in range(modules)]
    root.chmod(0o700)
    for sibling in siblings:
        sibling.mkdir()
        (sibling / "pyproject.toml").write_text(
            f"[project]\nname='{sibling.name}'\nversion='1'\n"
        )
    root.chmod(0o555)
    shared.build_view(siblings[0])
    target = target_for(root)
    with shared.build_lock(target):
        generated = target / "backend-output"
        generated.mkdir()
        for index in range(outputs):
            (generated / f"output-{index}").write_text("accumulated backend output")
    before = snapshot(root)
    original_walk = os.walk
    counts = {"walks": 0, "entries": 0}

    def walk(path, *args, **kwargs):
        observed = path == target
        if observed:
            counts["walks"] += 1
        for item in original_walk(path, *args, **kwargs):
            if observed:
                counts["entries"] += len(item[1]) + len(item[2])
            yield item

    monkeypatch.setattr(os, "walk", walk)

    def batch(api):
        counts.update(walks=0, entries=0)
        views = [api.build_view(path)[0] for path in siblings]
        locks = {}
        for path in views:
            lock = api.build_lock(path)
            locks.setdefault(lock.lock_file, lock)
        assert len(locks) == 1
        for lock in locks.values():
            with lock:
                pass
        return dict(counts)

    baseline = baseline_build_views()
    negative = batch(baseline) if baseline is not None else None
    candidate = batch(shared)
    assert candidate["walks"] == 1
    if negative is not None:
        assert negative["walks"] == modules + 1
        assert negative["entries"] == candidate["entries"] * (modules + 1)
    # Exercise the production batch integration, replacing only installer I/O.
    from amplifier_foundation.modules.batch import DependencyBatch

    counts.update(walks=0, entries=0)
    installer = Mock()
    monkeypatch.setattr(subprocess, "run", installer)
    owner = SimpleNamespace(
        refresh_dependencies=True, install_python=sys.executable,
        install_constraints=None, install_overrides=None,
        _needs_python_install=lambda p: True,
        _install_state=SimpleNamespace(mark_installed=Mock()), finalize=Mock(),
    )
    actual = DependencyBatch()
    for sibling in siblings:
        actual.add(owner, sibling)
    asyncio.run(actual.install())
    actual_counts = dict(counts)
    assert installer.call_count == 1 and actual_counts == candidate
    assert all(str(target / sibling.name) in installer.call_args.args[0] for sibling in siblings)
    counts.update(walks=0, entries=0)
    lock = shared.build_lock(target)
    with lock:
        assert counts["walks"] == 1  # Context entry does not double-validate.
        for _ in range(modules):
            with lock:
                pass
        assert counts["walks"] == modules + 1
    with lock:
        pass
    assert counts["walks"] == modules + 2  # No validation cache survives release.
    assert snapshot(root) == before
    retain_measurement(tmp_path, f"foundation1292-work-{modules}-{outputs}.json", {
        "sibling_modules": modules, "backend_output_files": outputs,
        "operation": "materialize/reuse each sibling, deduplicate lock_file, acquire backend once",
        "candidate": candidate, "actual_DependencyBatch": actual_counts,
        "baseline_b124": negative,
        "walks_per_acquisition": 1, "reentrant_acquisitions_revalidate": True,
        "timing_gate": None, "immutable_snapshot_unchanged": True,
    })


@pytest.mark.parametrize("damage", ["escape", "git-redirect", "provenance", "partial"])
def test_backend_reacquisition_revalidates_mutations_after_release(source, tmp_path, damage):
    root, module = source
    build, _ = shared.build_view(module)
    target = target_for(root)
    lock = shared.build_lock(build)
    with lock:
        if damage == "escape":
            symlink(build / "escape", tmp_path, target_is_directory=True)
        elif damage == "git-redirect":
            (target / ".git/commondir").write_text(str(tmp_path))
        else:
            marker = target / ".amplifier_cache_meta.json"
            data = json.loads(marker.read_text())
            data.update({"commit": "f" * 40} if damage == "provenance" else {"buildInput": False})
            marker.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        with lock:
            pytest.fail("previous custody cannot authorize a mutated tree")
    assert not lock.is_locked


def test_subpath_file_parent_is_not_a_directory(source):
    root, module = source
    with pytest.raises(ValueError, match="subpath"):
        shared.build_view(module / "regular.sock/../pyproject.toml")


def test_escaping_traversal_then_parent_cannot_be_collapsed_to_safe_path(source, tmp_path):
    root, module = source
    outside = tmp_path / "external"
    outside.mkdir()
    add_link(root, "escape", str(outside))
    before = snapshot(root)
    with pytest.raises(ValueError, match="escaping symlink"):
        shared.build_view(module / "escape/../regular.sock")
    assert snapshot(root) == before


def test_reentrant_mutation_is_not_hidden_by_uninterrupted_lock(source, tmp_path):
    root, module = source
    build, _ = shared.build_view(module)
    lock = shared.build_lock(build)
    with lock:
        symlink(build / "escape", tmp_path, target_is_directory=True)
        with pytest.raises(ValueError, match="escaping symlink"):
            with lock:
                pytest.fail("reentrant code may mutate the tree too")
        assert lock.is_locked  # Failed nested acquire releases only its level.
        (build / "escape").unlink()
    assert not lock.is_locked


@pytest.mark.parametrize("damage", ["escape", "git-redirect"])
def test_cheap_sibling_reuse_does_not_authorize_mutated_backend_tree(
    source, tmp_path, damage
):
    root, module = source
    build, _ = shared.build_view(module)
    target = target_for(root)
    unrelated = target / "backend-generated/unrelated"
    unrelated.mkdir(parents=True)
    with shared.build_lock(build):
        if damage == "escape":
            symlink(unrelated / "escape", tmp_path, target_is_directory=True)
        else:
            control = unrelated / ".git"
            control.mkdir()
            (control / "commondir").write_text(str(tmp_path))
    before = snapshot(root)
    # Metadata/subpath reuse intentionally does not walk unrelated output.
    assert shared.build_view(module) == (build, True)
    with pytest.raises(ValueError, match="escaping symlink|redirects Git metadata"):
        with shared.build_lock(build):
            pytest.fail("lookup is not backend authorization")
    assert snapshot(root) == before


def test_external_alias_cannot_bypass_managed_input_or_backend_lock(source, tmp_path):
    root, module = source
    alias = tmp_path / "module-alias"
    symlink(alias, module, target_is_directory=True)
    with pytest.raises(ValueError, match="aliases managed"):
        shared.build_view(alias)
    build, _ = shared.build_view(module)
    backend_alias = tmp_path / "backend-alias"
    symlink(backend_alias, build, target_is_directory=True)
    with pytest.raises(ValueError, match="aliases managed"):
        shared.build_lock(backend_alias)


@pytest.mark.parametrize("text", ["regular.sock/.", "regular.sock/"])
def test_symlink_target_retains_required_directory_semantics(source, text):
    root, module = source
    add_link(root, "needs-directory", text)
    before = snapshot(root)
    assert not (module / "needs-directory").exists()
    with pytest.raises(ValueError, match="subpath"):
        shared.build_view(module / "needs-directory")
    assert snapshot(root) == before
    build, _ = shared.build_view(module)
    with pytest.raises(ValueError, match="subpath"):
        with shared.build_lock(build / "needs-directory"):
            pytest.fail("file slash or dot is not a valid selected package")


@pytest.mark.skipif(os.name != "nt", reason="native Windows rooted link syntax")
def test_windows_rooted_link_cannot_reset_managed_subpath(source, tmp_path):
    root, module = source
    build, _ = shared.build_view(module)
    target = target_for(root)
    # A drive-less rooted target is anchored but is_absolute() is false.
    text = str(tmp_path)[len(tmp_path.drive):]
    assert Path(text).anchor and not Path(text).is_absolute()
    add_link(root, "rooted", text)
    symlink(build / "rooted", text, target_is_directory=True)
    with pytest.raises(ValueError, match="escaping symlink"):
        shared.build_view(module / "rooted")
    with pytest.raises(ValueError, match="escaping symlink"):
        with shared.build_lock(build / "rooted"):
            pytest.fail("rooted target must never reach backend")
    assert target.is_dir()
