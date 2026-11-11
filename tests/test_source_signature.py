"""Source identity includes authored bytes and links, never ignored tree work."""
import glob
import hashlib
import os
from pathlib import Path

import pytest

from amplifier_foundation.modules.preparation import source_signature


def digest(rows):
    result = hashlib.sha256()
    for name, content in sorted(rows.items(), key=lambda row: Path(row[0])):
        result.update(str(Path(name)).encode() + b"\0" + content)
    return result.hexdigest()


def test_exact_authored_bytes_and_global_path_order(tmp_path):
    rows = {"z.py": b"last\n", "nested/a.py": b"nested\n", "a.py": b"first\n", "a/inner.py": b"earlier\n",
            "nested/deeper/unicode.txt": "hello 🌱\n".encode()}
    for name, content in reversed(list(rows.items())):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    assert source_signature(tmp_path) == digest(rows)


@pytest.mark.parametrize("name", [".git", ".venv", "node_modules", "__pycache__", "build", "dist", "fixture.egg-info"])
def test_excluded_tree_is_never_entered(tmp_path, monkeypatch, name):
    excluded = tmp_path / "nested" / name
    (excluded / "deep").mkdir(parents=True)
    (excluded / "deep" / "ignored.py").write_bytes(b"not a build input")
    (tmp_path / "owned.py").write_bytes(b"authored")
    original = os.scandir

    def scan(path):
        assert not Path(path).is_relative_to(excluded), "Excluded content was traversed before filtering"
        return original(path)

    monkeypatch.setattr(os, "scandir", scan)
    # Python 3.13 glob retains its own scandir alias; older pathlib uses os.
    if globber := getattr(glob, "_StringGlobber", None):
        monkeypatch.setattr(globber, "scandir", staticmethod(scan))
    assert source_signature(tmp_path) == digest({"owned.py": b"authored"})


def test_nested_edits_additions_removal_and_rename_change_identity(tmp_path):
    source = tmp_path / "nested" / "owned.py"
    source.parent.mkdir()
    source.write_bytes(b"before")
    assert source_signature(tmp_path) == digest({"nested/owned.py": b"before"})
    source.write_bytes(b"after!")
    assert source_signature(tmp_path) == digest({"nested/owned.py": b"after!"})
    other = tmp_path / "extra.py"
    other.write_bytes(b"extra")
    assert source_signature(tmp_path) == digest({"extra.py": b"extra", "nested/owned.py": b"after!"})
    source.rename(source.with_name("renamed.py"))
    assert source_signature(tmp_path) == digest({"extra.py": b"extra", "nested/renamed.py": b"after!"})
    other.unlink()
    assert source_signature(tmp_path) == digest({"nested/renamed.py": b"after!"})


@pytest.mark.skipif(os.name != "posix", reason="Symlink fixture needs ordinary POSIX symlink creation")
def test_internal_and_broken_links_bind_link_text_without_following(tmp_path):
    root = tmp_path / "source"
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "owned.py").write_bytes(b"authored")
    (root / "file-link").symlink_to("nested/owned.py")
    (root / "directory-link").symlink_to("nested", target_is_directory=True)
    (root / "broken-link").symlink_to("missing")
    rows = {"nested/owned.py": b"authored", "file-link": b"nested/owned.py",
            "directory-link": b"nested", "broken-link": b"missing"}
    assert source_signature(root) == digest(rows)
    alias = tmp_path / "root-alias"
    alias.symlink_to(root, target_is_directory=True)
    assert source_signature(alias) == digest(rows)


@pytest.mark.skipif(os.name != "posix", reason="Symlink fixture needs ordinary POSIX symlink creation")
@pytest.mark.parametrize("directory", [False, True])
def test_outward_link_refuses_but_ignored_outward_link_is_excluded(tmp_path, directory):
    root, outside = tmp_path / "source", tmp_path / "outside"
    root.mkdir()
    if directory:
        outside.mkdir()
    else:
        outside.write_bytes(b"external")
    link = root / "declared-link"
    link.symlink_to(outside, target_is_directory=directory)
    with pytest.raises(ValueError, match="External source symlink cannot be qualified"):
        source_signature(root)
    link.rename(root / "node_modules")
    assert source_signature(root) == hashlib.sha256().hexdigest()


def test_authored_read_failure_is_not_a_reusable_digest(tmp_path, monkeypatch):
    source = tmp_path / "owned.py"
    source.write_bytes(b"authored")
    failure = OSError("fixture read failed")
    original = Path.read_bytes

    def read(path):
        if path == source:
            raise failure
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(OSError) as caught:
        source_signature(tmp_path)
    assert caught.value is failure


@pytest.mark.parametrize("kind", ["empty", "missing", "file"])
def test_non_directory_or_empty_root_keeps_empty_signature(tmp_path, kind):
    root = tmp_path / "root"
    if kind == "empty":
        root.mkdir()
    elif kind == "file":
        root.write_bytes(b"root file is not a directory input")
    assert source_signature(root) == hashlib.sha256().hexdigest()
