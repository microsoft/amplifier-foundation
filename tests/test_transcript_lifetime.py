"""Real Foundation writer tests; no legacy migration or live host activation."""
import asyncio
from contextlib import closing, contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from amplifier_foundation.session import transcript_lifetime as lifetime
from amplifier_foundation.session.transcript_lifetime import (
    VersionedSessionBinding as Binding, VersionedSessionConflict as Conflict,
)
from amplifier_foundation.session.transcript_encoding import canonical_bytes
from amplifier_foundation.session.transcript_source import open_committed_source

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Prospective lifetime positive cases qualify Linux renameat2 only.')


def create(parent, sid='new-session', admit=lambda: None):
    return Binding.create_new(parent / sid, session_id=sid, operation_id='init', admit=admit)


def rows(text='hello'):
    return [{'role': 'user', 'content': text}]


def digest(value):
    result = hashlib.sha256()
    for row in value:
        for chunk in canonical_bytes(row):
            result.update(chunk)
        result.update(b'\n')
    return result.hexdigest()


def test_publish_owns_complete_fenced_namespace(tmp_path, monkeypatch):
    original = lifetime._publish
    seen = []
    def publish(fd, source, target):
        root = tmp_path / source
        assert not (tmp_path / target).exists()
        for name in ('transcript.jsonl', 'transcript.jsonl.backup'):
            assert (root / name).is_dir() and list((root / name).iterdir())
        seen.append(source)
        original(fd, source, target)
    monkeypatch.setattr(lifetime, '_publish', publish)
    with closing(create(tmp_path)) as binding:
        assert binding.directory == tmp_path / 'new-session'
        assert binding.load_messages() == []
        with open_committed_source(binding.directory, expected=binding.expected) as source:
            assert source.commit.session_id == binding.session_id
        for name in ('transcript.jsonl', 'transcript.jsonl.backup'):
            with pytest.raises(IsADirectoryError):
                open(binding.directory / name, 'ab')
    assert len(seen) == 1


@pytest.mark.parametrize('kind', ['directory', 'file', 'symlink'])
def test_existing_target_is_never_overwritten(tmp_path, kind):
    target = tmp_path / 'new-session'
    if kind == 'directory':
        target.mkdir()
        (target / 'original').write_bytes(b'unchanged')
    elif kind == 'file':
        target.write_bytes(b'unchanged')
    else:
        target.symlink_to(tmp_path / 'absent')
    with pytest.raises(FileExistsError):
        create(tmp_path)
    if kind == 'directory':
        assert (target / 'original').read_bytes() == b'unchanged'
    elif kind == 'file':
        assert target.read_bytes() == b'unchanged'
    else:
        assert target.is_symlink()


def test_contention_then_explicit_retire_resume_and_cas(tmp_path):
    binding = create(tmp_path)
    expected = binding.expected
    with pytest.raises(Conflict, match='retains'):
        Binding.resume(binding.directory, session_id=binding.session_id, expected=expected)
    changed = binding.append(rows(), expected=expected, operation_id='append', admit=lambda: None)
    with pytest.raises(Conflict):
        binding.rewrite(rows('wrong'), expected=expected, operation_id='stale', admit=lambda: None)
    binding.close()
    with closing(Binding.resume(tmp_path / 'new-session', session_id='new-session', expected=changed.commit)) as resumed:
        assert resumed.load_messages() == rows()
        rewritten = resumed.rewrite(rows('edited'), expected=resumed.expected,
                                    operation_id='edit', admit=lambda: None)
        assert rewritten.commit.generation != changed.commit.generation
        assert resumed.load_messages() == rows('edited')


def test_failed_init_retains_unpublished_namespace_and_no_adoption(tmp_path):
    def fail():
        raise RuntimeError('admission refused')
    with pytest.raises(RuntimeError):
        create(tmp_path, admit=fail)
    assert not (tmp_path / 'new-session').exists()
    retained = list(tmp_path.glob('.versioned-new-*'))
    assert len(retained) == 1
    assert (retained[0] / lifetime._ENROLLMENT).is_file()
    with pytest.raises(Conflict):
        Binding.resume(retained[0], session_id='new-session', expected=None)


@pytest.mark.parametrize('name', [lifetime._LEASE, lifetime._ENROLLMENT])
def test_replaced_ownership_files_refuse_without_writing(tmp_path, name):
    with closing(create(tmp_path)) as binding:
        before = binding.expected
        original = binding.directory / name
        original.rename(original.with_suffix('.old'))
        original.write_bytes(b'not authority')
        with pytest.raises(Conflict):
            binding.append(rows(), expected=before, operation_id='refuse', admit=lambda: None)
        with open_committed_source(binding.directory, expected=before) as source:
            assert source.commit == before


def test_parent_and_source_rebinding_refuse(tmp_path):
    parent = tmp_path / 'private'
    parent.mkdir(mode=0o700)
    with closing(create(parent)) as binding:
        parent.rename(tmp_path / 'old')
        parent.mkdir(mode=0o700)
        with pytest.raises(Conflict):
            binding.check()


def test_nested_callback_refuses_without_deadlock_and_reconcile_is_exact(tmp_path):
    with closing(create(tmp_path)) as binding:
        before = binding.expected
        def nested():
            binding.rewrite(rows('nested'), expected=before, operation_id='nested', admit=lambda: None)
        with pytest.raises(Conflict, match='busy'):
            binding.rewrite(rows(), expected=before, operation_id='outer', admit=nested)
        with pytest.raises(Conflict):
            binding.rewrite(rows(), expected=before, operation_id='replay', admit=lambda: None)
        with pytest.raises(Conflict):
            binding.reconcile(operation_id='outer', expected=before, suffix_sha256='0' * 64, kind='rewrite')
        assert binding.reconcile(operation_id='outer', expected=before,
                                 suffix_sha256=digest(rows()), kind='rewrite') is None
        assert binding.load_messages() == []
        binding.rewrite(rows('new intent'), expected=before, operation_id='new-intent', admit=lambda: None)


def test_lost_ack_reconcile_does_not_rewrite_or_replay(tmp_path, monkeypatch):
    with closing(create(tmp_path)) as binding:
        before = binding.expected
        original = binding._writer.append
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('lost local acknowledgement')
        monkeypatch.setattr(binding._writer, 'append', lost)
        with pytest.raises(OSError):
            binding.append(rows(), expected=before, operation_id='once', admit=lambda: None)
        monkeypatch.setattr(binding._writer, 'append', lambda *a, **kw: pytest.fail('replayed'))
        result = binding.reconcile(operation_id='once', expected=before, kind='append',
                                   suffix_sha256=digest(rows()))
        assert result.reconciled
        assert binding.load_messages() == rows()


@pytest.mark.asyncio
async def test_cancel_and_close_join_physical_write(tmp_path):
    binding = create(tmp_path)
    started, release = threading.Event(), threading.Event()
    before = binding.expected
    def admission():
        started.set()
        assert release.wait(10)
    task = asyncio.create_task(binding.call('rewrite', rows(), expected=before,
                                           operation_id='physical', admit=admission))
    assert await asyncio.to_thread(started.wait, 10)
    task.cancel()
    closing_task = asyncio.create_task(binding.aclose())
    await asyncio.sleep(0.05)
    assert not task.done() and not closing_task.done()
    with pytest.raises(Conflict, match='retains'):
        Binding.resume(binding.directory, session_id=binding.session_id, expected=before)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await closing_task
    with closing(Binding.resume(binding.directory, session_id=binding.session_id,
                                expected=binding.expected)) as resumed:
        assert resumed.load_messages() == rows()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='POSIX fork qualification')
def test_fork_child_invalidates_inherited_mutex_and_never_unlocks_parent(tmp_path):
    with closing(create(tmp_path)) as binding:
        pid = os.fork()
        if pid == 0:
            try:
                with pytest.raises(Conflict):
                    binding.load_messages()
                binding.close()
                os._exit(0)
            except BaseException:
                os._exit(4)
        assert os.waitpid(pid, 0)[1] == 0
        with pytest.raises(Conflict, match='retains'):
            Binding.resume(binding.directory, session_id=binding.session_id, expected=binding.expected)
        binding.append(rows(), expected=binding.expected, operation_id='parent-still-owned', admit=lambda: None)


def test_real_assets_first_typed_ack_and_resume(tmp_path):
    from PIL import Image
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    image = io.BytesIO()
    Image.new('RGB', (3, 4), (12, 54, 92)).save(image, 'PNG')
    with closing(create(tmp_path)) as binding:
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            origin = {'kind': 'tool', 'session_id': binding.session_id, 'id': 'fixture'}
            ref = store.ingest_sync(image.getvalue(), source_format='image-bytes', origin=origin,
                                    policy=None)['staged_refs'][0]
            publication = store.publish_sync([ref], origin=origin, disposition='retain')
            ref = publication['refs'][0]
            value = [{'role': 'tool', 'content': 'pixels',
                      'metadata': {'experimental.media.v1': {'refs': [ref]}}}]
            ack = binding.append(value, expected=binding.expected, operation_id='media',
                                 admit=lambda: None, media_store=store)
            assert binding.last_receipt.verify()['session_id'] == binding.session_id
            assert ack.commit == binding.expected
            with store.resolve_ref(ref) as stream:
                assert stream.read() == image.getvalue()
            with pytest.raises((ValueError, TypeError)):
                store.acknowledge_checkpoint(ack)
            assert binding.load_messages() == value
        expected, directory = binding.expected, binding.directory
    with closing(Binding.resume(directory, session_id='new-session', expected=expected)) as resumed:
        with closing(SessionMediaStore(directory / 'media', resumed.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            with pytest.raises(ValueError):
                store.resolve_ref(ref)
            receipt = resumed.checkpoint(expected=resumed.expected, media_store=store)
            assert receipt.verify()['session_id'] == resumed.session_id
            with store.resolve_ref(ref) as stream:
                assert stream.read() == image.getvalue()


def test_two_processes_contend_and_exec_does_not_inherit_lease(tmp_path):
    with closing(create(tmp_path)) as binding:
        script = """
import fcntl, os, sys
fd=os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(7)
else:
    sys.exit(0)
"""
        result = subprocess.run([sys.executable, '-c', script, str(binding.directory / lifetime._LEASE)],
                                check=False, timeout=10)
        assert result.returncode == 7
        # A running exec child cannot retain the owner's lease after close.
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)'], close_fds=False)
        try:
            binding.close()
            result = subprocess.run([sys.executable, '-c', script, str(binding.directory / lifetime._LEASE)],
                                    check=False, timeout=10)
            assert result.returncode == 0
        finally:
            child.terminate()
            child.wait(timeout=10)


def test_publication_race_never_opens_an_old_transcript_fd(tmp_path, monkeypatch):
    original = lifetime._publish
    retained = []
    def race(fd, source, target):
        legacy = tmp_path / target
        legacy.mkdir(mode=0o700)
        # A legacy producer wins the selected ID before our publication.
        with open(legacy / 'transcript.jsonl', 'wb') as stream:
            stream.write(b'legacy-original\n')
            stream.flush()
            retained.append(tmp_path / source)
            original(fd, source, target)
    monkeypatch.setattr(lifetime, '_publish', race)
    with pytest.raises(FileExistsError):
        create(tmp_path)
    assert (tmp_path / 'new-session' / 'transcript.jsonl').read_bytes() == b'legacy-original\n'
    assert (retained[0] / 'transcript.jsonl').is_dir()
    assert (retained[0] / 'transcript.jsonl.backup').is_dir()


@pytest.mark.parametrize('name', ['transcript.jsonl', 'transcript.jsonl.backup'])
def test_resume_requires_both_original_fences(tmp_path, name):
    binding = create(tmp_path)
    root, expected = binding.directory, binding.expected
    binding.close()
    (root / name).rename(root / (name + '.retained'))
    with pytest.raises(ValueError):
        Binding.resume(root, session_id='new-session', expected=expected)
    assert (root / (name + '.retained')).is_dir()


def test_foreign_real_t2_is_not_prospective_enrollment(tmp_path):
    from amplifier_foundation.session.transcript_writer import TranscriptWriter
    root = tmp_path / 'foreign'
    root.mkdir(mode=0o700)
    @contextmanager
    def fixture(path):
        yield lambda: None
    ack = TranscriptWriter(root, fence=fixture).initialize(session_id='foreign', operation_id='init',
                                                          admit=lambda: None)
    with pytest.raises(FileNotFoundError):
        Binding.resume(root, session_id='foreign', expected=ack.commit)
    assert not (root / lifetime._LEASE).exists()


def test_ack_late_exit_failure_revokes_and_keeps_unknown_not_replayed(tmp_path, monkeypatch):
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    import amplifier_foundation.session.transcript_writer as writer_module
    with closing(create(tmp_path)) as binding:
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            actual = store.acknowledge_checkpoint
            receipts = []
            def accept(receipt):
                receipts.append(receipt)
                actual(receipt)
                # Test-only late ownership failure after store effects but
                # before acknowledgement context exits and releases its fence.
                monkeypatch.setattr(binding, 'check', lambda: (_ for _ in ()).throw(ValueError('late owner loss')))
            monkeypatch.setattr(store, 'acknowledge_checkpoint', accept)
            with pytest.raises(ValueError, match='checkpoint refused'):
                binding.checkpoint(expected=binding.expected, media_store=store)
            assert len(receipts) == 1 and binding.last_receipt is None and binding._uncertain
            with pytest.raises(ValueError):
                receipts[0].verify()


def test_assets_must_be_published_before_exact_rows_commit(tmp_path):
    from PIL import Image
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    image = io.BytesIO()
    Image.new('RGB', (2, 2)).save(image, 'PNG')
    with closing(create(tmp_path)) as binding:
        before = binding.expected
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            origin = {'kind': 'tool', 'session_id': binding.session_id, 'id': 'unpublished'}
            ref = store.ingest_sync(image.getvalue(), source_format='image-bytes', origin=origin,
                                    policy=None)['staged_refs'][0]
            value = [{'role': 'tool', 'content': 'pixels',
                      'metadata': {'experimental.media.v1': {'refs': [ref]}}}]
            with pytest.raises(ValueError):
                binding.rewrite(value, expected=before, operation_id='refuse', admit=lambda: None,
                                 media_store=store)
            with open_committed_source(binding.directory, expected=before) as source:
                assert source.commit == before


def test_resume_none_is_not_an_exact_selector(tmp_path):
    binding = create(tmp_path)
    binding.append(rows(), expected=binding.expected, operation_id='first', admit=lambda: None)
    binding.close()
    with pytest.raises(Conflict, match='exact'):
        Binding.resume(binding.directory, session_id=binding.session_id, expected=None)


def test_historical_reconcile_keeps_current_cursor(tmp_path):
    binding = create(tmp_path)
    predecessor = binding.expected
    first = binding.append(rows('A'), expected=predecessor, operation_id='a', admit=lambda: None)
    second = binding.append(rows('B'), expected=first.commit, operation_id='b', admit=lambda: None)
    binding.close()
    with closing(Binding.resume(binding.directory, session_id=binding.session_id,
                                expected=second.commit)) as resumed:
        result = resumed.reconcile(operation_id='a', expected=predecessor,
                                    suffix_sha256=digest(rows('A')), kind='append')
        assert result.commit == first.commit
        assert resumed.expected == second.commit
        assert resumed.load_messages() == rows('A') + rows('B')
        current = resumed.reconcile(operation_id='b', expected=first.commit,
                                    suffix_sha256=digest(rows('B')), kind='append')
        assert current.commit == second.commit and resumed.expected == second.commit
        resumed.append(rows('C'), expected=second.commit, operation_id='c', admit=lambda: None)


@pytest.mark.parametrize('caught', [True, False])
def test_nested_close_refusal_does_not_change_lifecycle(tmp_path, caught):
    with closing(create(tmp_path)) as binding:
        before = binding.expected
        def admission():
            if caught:
                with pytest.raises(Conflict, match='callback'):
                    binding.close()
            else:
                binding.close()
        if caught:
            binding.rewrite(rows(), expected=before, operation_id='outer', admit=admission)
            assert binding.load_messages() == rows()
        else:
            with pytest.raises(Conflict, match='callback'):
                binding.rewrite(rows(), expected=before, operation_id='outer', admit=admission)
            assert binding.reconcile(operation_id='outer', expected=before, kind='rewrite',
                                     suffix_sha256=digest(rows())) is None
            assert binding.load_messages() == []
        binding.rewrite(rows('next'), expected=binding.expected, operation_id='next', admit=lambda: None)


@pytest.mark.parametrize('part', ['media', 'assets', 'records'])
def test_detached_media_store_cannot_publish_canonical_commit(tmp_path, part):
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    with closing(create(tmp_path)) as binding:
        before = binding.expected
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            path = binding.directory / 'media'
            if part != 'media':
                path /= part
            path.rename(path.with_name(path.name + '-detached'))
            path.mkdir(mode=0o700)
            with pytest.raises(Conflict, match='replaced'):
                binding.rewrite(rows(), expected=before, operation_id='refuse',
                                 admit=lambda: None, media_store=store)
            with open_committed_source(binding.directory, expected=before) as source:
                assert source.commit == before
            assert binding.last_receipt is None


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='POSIX fork qualification')
def test_active_fork_child_does_not_keep_foundation_operation_lock(tmp_path):
    binding = create(tmp_path)
    started, release = threading.Event(), threading.Event()
    errors = []
    def admission():
        started.set()
        assert release.wait(10)
    def writing():
        try:
            binding.append(rows('A'), expected=binding.expected, operation_id='a', admit=admission)
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=writing)
    thread.start()
    assert started.wait(10)
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(write_fd)
        os.read(read_fd, 1)  # Stay alive across parent retirement and next write.
        os._exit(0)
    os.close(read_fd)
    try:
        release.set()
        thread.join(10)
        assert not thread.is_alive() and not errors
        binding.close()
        with closing(Binding.resume(binding.directory, session_id=binding.session_id,
                                    expected=binding.expected)) as resumed:
            done = threading.Event()
            def next_write():
                try:
                    resumed.append(rows('B'), expected=resumed.expected, operation_id='b', admit=lambda: None)
                finally:
                    done.set()
            next_thread = threading.Thread(target=next_write)
            next_thread.start()
            # If the child retained Foundation's flock, release child so a
            # failing test still settles its physical writer before returning.
            if not done.wait(5):
                os.write(write_fd, b'x')
                next_thread.join(5)
                pytest.fail('Fork child retained Foundation operation lock.')
            next_thread.join()
            assert resumed.load_messages() == rows('A') + rows('B')
    finally:
        os.close(write_fd)
        os.waitpid(pid, 0)
        binding.close()


@pytest.mark.asyncio
async def test_prospective_typed_ack_private_transport_preserves_canonical_refs(tmp_path):
    import base64
    from PIL import Image
    from amplifier_core import AmplifierSession
    from amplifier_core.message_models import ChatRequest, Message, ImageBlock
    from amplifier_foundation.session.media import SessionMediaStore, bind_session_media
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    output = io.BytesIO()
    Image.new('RGB', (3, 4), (4, 90, 170)).save(output, format='PNG')
    raw = output.getvalue()
    with closing(create(tmp_path)) as binding:
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            origin = {'kind': 'tool', 'session_id': binding.session_id, 'id': 'tool-1'}
            ref = store.ingest_sync(raw, source_format='image-bytes', origin=origin, policy=None)['value']
            suffix = [
                {'role': 'assistant', 'content': '', 'tool_calls': [
                    {'id': 'tool-1', 'type': 'function', 'function': {'name': 'fixture', 'arguments': '{}'}}]},
                {'role': 'tool', 'content': 'Image', 'tool_call_id': 'tool-1', 'name': 'fixture',
                 'metadata': {'experimental.media.v1': {'refs': [ref]}}},
            ]
            binding.append(suffix, expected=binding.expected, operation_id='pixels',
                           admit=lambda: store.publish_sync([ref], origin=origin, disposition='retain'),
                           media_store=store)
            native = AmplifierSession({'session': {'orchestrator': 'unused', 'context': 'unused'}},
                                      session_id=binding.session_id)
            capability = bind_session_media(native.coordinator, store=store, enabled=True)
            request = ChatRequest(messages=[Message(**row) for row in suffix])
            before = request.model_dump_json()
            prepared = await capability.prepare_request(request, provider_identity={
                'provider': 'synthetic', 'account': 'fixture', 'generation': 'prospective',
                'media_support': {'v': 1, 'positions': ['tool'], 'mime_types': ['image/png']}},
                model='fixture', purpose='model', execution_scope={}, request_options=None)
            try:
                projected = prepared.request
                pixels = next(block for message in projected.messages
                              if isinstance(message.content, list) for block in message.content
                              if isinstance(block, ImageBlock))
                assert base64.b64decode(pixels.source['data']) == raw
                capability.validate(projected, purpose='transport', request_options=None)
                assert request.model_dump_json() == before
                assert binding.load_messages() == suffix
            finally:
                prepared.release()
            assert projected.messages == []


def test_reconcile_ack_failure_keeps_exact_media_obligation(tmp_path, monkeypatch):
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    binding = create(tmp_path)
    before = binding.expected
    result = binding.append(rows(), expected=before, operation_id='a', admit=lambda: None)
    binding.close()
    with closing(Binding.resume(binding.directory, session_id=binding.session_id,
                                expected=result.commit)) as resumed:
        with closing(SessionMediaStore(resumed.directory / 'media', resumed.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            actual = store.acknowledge_checkpoint
            receipts = []
            def failing(receipt):
                receipts.append(receipt)
                actual(receipt)
                raise OSError('late store failure')
            monkeypatch.setattr(store, 'acknowledge_checkpoint', failing)
            with pytest.raises(ValueError):
                resumed.reconcile(operation_id='a', expected=before, kind='append',
                                   suffix_sha256=digest(rows()), media_store=store)
            assert resumed._uncertain and resumed.last_receipt is None
            with pytest.raises(ValueError):
                receipts[0].verify()
            with pytest.raises(Conflict):
                resumed.append(rows('not yet'), expected=resumed.expected,
                                operation_id='successor', admit=lambda: None)
            with pytest.raises(Conflict, match='media'):
                resumed.reconcile(operation_id='a', expected=before, kind='append',
                                   suffix_sha256=digest(rows()))
            monkeypatch.setattr(store, 'acknowledge_checkpoint', actual)
            recovered = resumed.reconcile(operation_id='a', expected=before, kind='append',
                                           suffix_sha256=digest(rows()), media_store=store)
            assert recovered.commit == result.commit
            assert not resumed._uncertain


def test_original_write_cannot_recover_without_its_required_ack(tmp_path, monkeypatch):
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    with closing(create(tmp_path)) as binding:
        before = binding.expected
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            actual = store.acknowledge_checkpoint
            def failing(receipt):
                actual(receipt)
                raise OSError('late store failure')
            monkeypatch.setattr(store, 'acknowledge_checkpoint', failing)
            with pytest.raises(ValueError):
                binding.rewrite(rows(), expected=before, operation_id='a', admit=lambda: None,
                                media_store=store)
            with pytest.raises(Conflict, match='media'):
                binding.reconcile(operation_id='a', expected=before, kind='rewrite',
                                   suffix_sha256=digest(rows()))
            monkeypatch.setattr(store, 'acknowledge_checkpoint', actual)
            binding.reconcile(operation_id='a', expected=before, kind='rewrite',
                              suffix_sha256=digest(rows()), media_store=store)
            assert binding.load_messages() == rows()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='POSIX fork qualification')
def test_fork_inside_admission_cannot_close_a_reused_child_descriptor(tmp_path):
    with closing(create(tmp_path)) as binding:
        creator = os.getpid()
        child = None
        descriptors = []
        def admission():
            nonlocal child
            child = os.fork()
            if child == 0:
                # Inherited Foundation wrappers still exist on this thread.
                # Populate low free numbers before they unwind on PID refusal.
                descriptors.extend(os.open('/dev/null', os.O_RDWR) for _ in range(80))
        try:
            binding.append(rows(), expected=binding.expected, operation_id='fork', admit=admission)
        except BaseException:
            if os.getpid() == creator:
                raise
        if os.getpid() != creator:
            try:
                for descriptor in descriptors:
                    os.fstat(descriptor)
                os._exit(0)
            except BaseException:
                os._exit(9)
        assert child is not None
        assert os.waitpid(child, 0)[1] == 0
        assert binding.load_messages() == rows()


def test_publication_holds_lease_through_dependent_host_effects(tmp_path):
    binding = create(tmp_path)
    started, release, closed = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def publishing():
        try:
            with binding.publication() as check:
                binding.rewrite(rows(), expected=binding.expected, operation_id='host',
                                admit=lambda: None)
                with pytest.raises(Conflict, match='publication'):
                    binding.close()
                started.set()
                assert release.wait(10)
                check()
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=publishing)
    thread.start()
    assert started.wait(10)
    def retiring():
        binding.close()
        closed.set()
    retire = threading.Thread(target=retiring)
    retire.start()
    assert not closed.wait(0.05)
    with pytest.raises(Conflict, match='retains'):
        Binding.resume(binding.directory, session_id=binding.session_id, expected=binding.expected)
    release.set()
    thread.join(10)
    retire.join(10)
    assert not errors and closed.is_set()
    with closing(Binding.resume(binding.directory, session_id=binding.session_id,
                                expected=binding.expected)) as resumed:
        assert resumed.load_messages() == rows()


def test_failed_standalone_checkpoint_requires_exact_checkpoint_recovery(tmp_path, monkeypatch):
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    with closing(create(tmp_path)) as binding:
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            actual = store.acknowledge_checkpoint
            def failed(receipt):
                actual(receipt)
                raise OSError('lost checkpoint exit')
            monkeypatch.setattr(store, 'acknowledge_checkpoint', failed)
            with pytest.raises(ValueError):
                binding.checkpoint(expected=binding.expected, media_store=store)
            assert binding._uncertain
            with pytest.raises(Conflict, match='checkpoint'):
                binding.reconcile(operation_id='never-issued', expected=binding.expected,
                                   suffix_sha256=digest(rows()), kind='append', media_store=store)
            assert binding._uncertain
            monkeypatch.setattr(store, 'acknowledge_checkpoint', actual)
            binding.checkpoint(expected=binding.expected, media_store=store)
            assert not binding._uncertain
            binding.append(rows(), expected=binding.expected, operation_id='after', admit=lambda: None)


@pytest.mark.parametrize('kind', ['append', 'rewrite'])
def test_precommit_request_only_publication_refuses_without_canonical_effect(tmp_path, kind):
    from PIL import Image
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    image = io.BytesIO()
    Image.new('RGB', (3, 4), (12, 54, 92)).save(image, 'PNG')
    with closing(create(tmp_path)) as binding:
        before = binding.expected
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            origin = {'kind': 'tool', 'session_id': binding.session_id, 'id': 'request-only'}
            ref = store.ingest_sync(image.getvalue(), source_format='image-bytes', origin=origin,
                                    policy={'retention': 'request-only'})['value']
            store.publish_sync([ref], origin=origin, disposition='retain')
            # A real legitimate publication is not necessarily durable-eligible.
            assert store.validate_published_ref(ref) == ref
            value = [{'role': 'tool', 'content': 'pixels',
                      'metadata': {'experimental.media.v1': {'refs': [ref]}}}]
            with pytest.raises(ValueError):
                getattr(binding, kind)(value, expected=before, operation_id='request-only',
                                       admit=lambda: None, media_store=store)
            # Do not let a late typed-ACK refusal conceal a canonical commit.
            with open_committed_source(binding.directory, expected=before) as source:
                assert source.commit == before and source.commit.message_count == 0
            assert binding.last_receipt is None


@pytest.mark.parametrize('kind', ['append', 'rewrite'])
@pytest.mark.parametrize('with_store', [False, True])
@pytest.mark.parametrize('metadata', [
    False, 0, '', [],
    {'experimental.media.v1': None},
    {'experimental.media.v1': {}},
    {'experimental.media.v1': {'refs': None}},
    {'experimental.media.v1': {'refs': False}},
    {'experimental.media.v1': {'refs': {}}},
    {'experimental.media.v1': {'refs': [], 'unknown': True}},
    {'experimental.media.v1': {'refs': [], 'v': True}},
    {'experimental.media.v1': {'refs': [{}]}},
])
def test_precommit_malformed_carrier_refuses_before_canonical_publication(tmp_path, kind, with_store, metadata):
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    with closing(create(tmp_path)) as binding:
        before = binding.expected
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            selected = store if with_store else None
            value = [{'role': 'user', 'content': 'unchanged', 'metadata': metadata}]
            # Deliberate controlled refusal, not raw KeyError/AttributeError.
            with pytest.raises(ValueError):
                getattr(binding, kind)(value, expected=before, operation_id='malformed',
                                       admit=lambda: None, media_store=selected)
            with open_committed_source(binding.directory, expected=before) as source:
                assert source.commit == before and source.commit.message_count == 0
            assert binding.last_receipt is None


@pytest.mark.parametrize('kind', ['append', 'rewrite'])
@pytest.mark.parametrize('metadata', [None, {}, {'not-media': None}])
def test_precommit_absent_or_null_metadata_remains_ordinary_history(tmp_path, kind, metadata):
    with closing(create(tmp_path)) as binding:
        value = [{'role': 'user', 'content': 'ordinary', 'metadata': metadata}]
        getattr(binding, kind)(value, expected=binding.expected, operation_id='ordinary',
                               admit=lambda: None)
        assert binding.load_messages() == value


@pytest.mark.parametrize('kind', ['append', 'rewrite'])
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_precommit_physical_durable_asset_required_before_canonical_effect(tmp_path, kind, damage):
    from PIL import Image
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    image = io.BytesIO()
    Image.new('RGB', (2, 2), (4, 90, 170)).save(image, 'PNG')
    with closing(create(tmp_path)) as binding:
        before = binding.expected
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            origin = {'kind': 'tool', 'session_id': binding.session_id, 'id': 'damaged'}
            ref = store.ingest_sync(image.getvalue(), source_format='image-bytes',
                                    origin=origin, policy=None)['value']
            store.publish_sync([ref], origin=origin, disposition='retain')
            asset = store.root / 'assets' / ref['sha256']
            if damage == 'missing':
                asset.unlink()
            else:
                asset.write_bytes(b'x' * ref['bytes'])
            value = [{'role': 'tool', 'content': 'pixels',
                      'metadata': {'experimental.media.v1': {'refs': [ref]}}}]
            with pytest.raises((ValueError, OSError)):
                getattr(binding, kind)(value, expected=before, operation_id='damaged',
                                       admit=lambda: None, media_store=store)
            with open_committed_source(binding.directory, expected=before) as source:
                assert source.commit == before and source.commit.message_count == 0
            assert binding.last_receipt is None


def test_precommit_expired_committed_history_and_metadata_only_adoption_preserved(tmp_path, monkeypatch):
    from datetime import datetime
    from PIL import Image
    from amplifier_foundation.session import media as media_module
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    image = io.BytesIO()
    Image.new('RGB', (2, 2)).save(image, 'PNG')
    with closing(create(tmp_path)) as binding:
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            origin = {'kind': 'tool', 'session_id': binding.session_id, 'id': 'expired',
                      'created_at': '2000-01-01T00:00:00+00:00',
                      'expires_at': '2000-01-02T00:00:00+00:00'}
            class BeforeExpiry(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime(2000, 1, 1, 12, tzinfo=tz)
            # Use the real producer/publication before expiry, then advance
            # ordinary wall time. Never forge record state or renew provenance.
            with monkeypatch.context() as clock:
                clock.setattr(media_module, 'datetime', BeforeExpiry)
                ref = store.ingest_sync(image.getvalue(), source_format='image-bytes',
                                        origin=origin, policy=None)['value']
                store.publish_sync([ref], origin=origin, disposition='retain')
            value = [{'role': 'tool', 'content': 'expired history',
                      'metadata': {'experimental.media.v1': {'refs': [ref]}}}]
            # An expired but newly published ordinary record still needs bytes.
            binding.append(value, expected=binding.expected, operation_id='original',
                           admit=lambda: None, media_store=store)
            (store.root / 'assets' / ref['sha256']).unlink()
            # Once genuinely committed, expired history can remain metadata only.
            binding.rewrite(value, expected=binding.expected, operation_id='retain-history',
                            admit=lambda: None, media_store=store)
            assert binding.load_messages() == value
            with closing(create(tmp_path, 'adopted')) as target:
                with closing(SessionMediaStore(target.directory / 'media', target.session_id,
                                               checkpoint_verifier=verified_media_checkpoint_refs)) as target_store:
                    copied = target_store.adopt_sync([ref], store)[0]
                    assert not (target_store.root / 'assets' / copied['sha256']).exists()
                    adopted = [{'role': 'tool', 'content': 'expired adopted history',
                                'metadata': {'experimental.media.v1': {'refs': [copied]}}}]
                    target.rewrite(adopted, expected=target.expected, operation_id='adopt',
                                   admit=lambda: None, media_store=target_store)
                    assert target.last_receipt.verify()['session_id'] == target.session_id
                    assert target.load_messages() == adopted


@pytest.mark.parametrize('kind', ['append', 'rewrite'])
def test_precommit_empty_media_envelope_is_valid_with_owned_store(tmp_path, kind):
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    with closing(create(tmp_path)) as binding:
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            value = [{'role': 'user', 'content': 'ordinary',
                      'metadata': {'experimental.media.v1': {'refs': [], 'v': 1}}}]
            getattr(binding, kind)(value, expected=binding.expected, operation_id='empty',
                                   admit=lambda: None, media_store=store)
            assert binding.load_messages() == value


def test_precommit_missing_checkpoint_verifier_refuses_before_write(tmp_path):
    from amplifier_foundation.session.media import SessionMediaStore
    with closing(create(tmp_path)) as binding:
        before = binding.expected
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id)) as store:
            with pytest.raises(ValueError):
                binding.rewrite(rows(), expected=before, operation_id='no-verifier',
                                admit=lambda: None, media_store=store)
            with open_committed_source(binding.directory, expected=before) as source:
                assert source.commit == before