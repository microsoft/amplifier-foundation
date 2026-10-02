"""Retained standalone contract cases; host cases remain distribution acceptance."""
import json
import os
from pathlib import Path
import pytest
from amplifier_worktrees import GitWorktrees
from amplifier_worktrees.git import git


def repository(tmp_path):
    root = tmp_path / 'repo'; root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Fixture'); git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / 'a.txt').write_text('original\n')
    (root / '.gitignore').write_text('ignored.txt\n')
    git(root, 'add', '.'); git(root, 'commit', '-m', 'base')
    return root


def create(manager, source, **kwargs):
    return manager.create(source, command_id=kwargs.pop('command_id', 'create'), expected_revision=manager.inspect(source)['sourceRevision'], **kwargs)


def test_clean_and_explicit_dirty_copies_preserve_source_index_and_manifest(tmp_path):
    root = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    (root / 'a.txt').write_text('staged\n'); git(root, 'add', 'a.txt')
    (root / 'a.txt').write_text('staged\nunstaged\n')
    (root / 'new.txt').write_text('new evidence')
    (root / 'link').symlink_to('new.txt')
    before = manager.inspect(root)
    clean = create(manager, root, session_id='s')
    assert (Path(clean['path']) / 'a.txt').read_text() == 'original\n'
    assert not (Path(clean['path']) / 'new.txt').exists()
    carried = create(manager, root, command_id='carry', mode='carry_dirty', branch='task/change', session_id='s')
    target = Path(carried['path'])
    assert (target / 'a.txt').read_text() == 'staged\nunstaged\n'
    assert git(target, 'show', ':a.txt') == b'staged\n'
    assert (target / 'new.txt').read_text() == 'new evidence'
    assert os.readlink(target / 'link') == 'new.txt'
    assert manager.inspect(root)['sourceRevision'] == before['sourceRevision']
    assert len(carried['manifest']['untracked']) == 2
    assert create(manager, root, command_id='carry', mode='carry_dirty', branch='task/change', session_id='s')['duplicate']
    with pytest.raises(ValueError, match='different contents'): create(manager, root, command_id='carry', mode='clean')
    with pytest.raises(ValueError, match='changed or ignored'): manager.remove(carried['id'], carried['revision'])
    (Path(clean['path']) / 'ignored.txt').write_text('must retain')
    with pytest.raises(ValueError, match='changed or ignored'): manager.remove(clean['id'], clean['revision'])
    (Path(clean['path']) / 'ignored.txt').unlink()
    removed = manager.remove(clean['id'], clean['revision'], 'remove')
    assert removed['status'] == 'removed' and root.exists()
    assert manager.remove(clean['id'], clean['revision'], 'remove')['duplicate']


def test_stale_branch_conflict_and_attached_ownership(tmp_path):
    root = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    revision = manager.inspect(root)['sourceRevision']
    (root / 'a.txt').write_text('later')
    with pytest.raises(ValueError, match='changed'): manager.create(root, command_id='stale', expected_revision=revision)
    with pytest.raises(ValueError, match='partial checkout'): create(manager, root, command_id='branch-conflict', branch='main')
    partial = next(row for row in manager.records() if row['status'] == 'partial')
    assert partial['manifest']['sourceUnchanged']
    attached = manager.attach(root, source=root, command_id='attach', session_id='s')
    with pytest.raises(ValueError, match='Only app-created'): manager.remove(attached['id'], 1)
    with pytest.raises(ValueError, match='normal Git ref'): create(manager, root, command_id='bad-ref', ref='--exec=bad')
    assert (root / 'a.txt').read_text() == 'later'


def test_unmerged_index_refuses_carry_and_source_stays_intact(tmp_path):
    root = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    git(root, 'checkout', '-b', 'other'); (root / 'a.txt').write_text('other\n'); git(root, 'commit', '-am', 'other')
    git(root, 'checkout', 'main'); (root / 'a.txt').write_text('main\n'); git(root, 'commit', '-am', 'main')
    with pytest.raises(ValueError): git(root, 'merge', 'other')
    original = (root / 'a.txt').read_bytes()
    with pytest.raises(ValueError, match='index conflicts'): create(manager, root, mode='carry_dirty')
    assert (root / 'a.txt').read_bytes() == original and git(root, 'ls-files', '-u')


def test_unsupported_index_flags_and_nested_storage_preserve_originals(tmp_path):
    root = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    (root / 'planned.txt').write_text('not staged yet')
    git(root, 'add', '-N', 'planned.txt')
    with pytest.raises(ValueError, match='index flags'): create(manager, root, mode='carry_dirty')
    assert (root / 'planned.txt').read_text() == 'not staged yet'
    nested = GitWorktrees(root / 'app-state')
    with pytest.raises(ValueError, match='outside'): create(nested, root)


def test_cleanup_keeps_detached_commits_until_they_have_a_retained_ref(tmp_path):
    root = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    record = create(manager, root); target = Path(record['path'])
    (target / 'a.txt').write_text('committed independent work\n')
    git(target, 'commit', '-am', 'independent work')
    with pytest.raises(ValueError, match='Detached commits'): manager.remove(record['id'], record['revision'])
    git(target, 'branch', 'keep-independent-work')
    assert manager.remove(record['id'], record['revision'])['status'] == 'removed'
    assert git(root, 'show', 'keep-independent-work:a.txt') == b'committed independent work\n'
