"""Opt-in lifetime custody for newly allocated versioned sessions.

Foundation remains the sole transcript writer. This adapter allocates a private
unpublished namespace, initializes both legacy-entry fences, then publishes the
new ID atomically without replacement. Its lease coordinates only this enrolled
prospective cohort; it is NOT an old-writer quiescence or migration mechanism.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import copy
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import weakref
from uuid import uuid4

_LEASE = '.versioned-owner'
_ENROLLMENT = '.versioned-enrollment.json'
_COHORT = 'prospective-versioned-session-v1'
_OWNERS = weakref.WeakSet()


def _after_fork():
    for owner in list(_OWNERS):
        owner._retire_inherited_operation_locks()
        owner._release()  # Do not acquire inherited Python mutexes or LOCK_UN.


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork)


class VersionedSessionConflict(ValueError):
    """Custody or exact intent is unavailable; originals remain unchanged."""


def _id(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,179}', value):
        raise VersionedSessionConflict('Invalid session or operation identity.')
    return value


def _identity(info):
    return info.st_dev, info.st_ino


def _stamp(info):
    return (*_identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _publish(parent, source, target):
    """Linux atomic RENAME_NOREPLACE; never downgrade to check-then-rename."""
    if sys.platform != 'linux':
        raise VersionedSessionConflict('Atomic prospective publication is qualified on Linux only.')
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, 'renameat2', None)
    if rename is None:
        raise VersionedSessionConflict('Atomic no-replace directory publication is unavailable.')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(source), parent, os.fsencode(target), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


class VersionedSessionBinding:
    """One process-owned persistent lease, held through idle and physical I/O.

    Methods are synchronous and serialized. ``close`` joins an in-progress
    method before releasing custody. Async users use ``call``/``aclose`` to join
    the physical thread even when their waiter is cancelled. A copied/inherited
    binding cannot act in a forked process. No automatic replay follows failure.
    """

    @classmethod
    def create_new(cls, session_dir, *, session_id, operation_id, admit):
        final = cls._path(session_dir, session_id)
        _id(operation_id)
        if not callable(admit):
            raise TypeError('Explicit asset admission is required.')
        binding = cls()
        stage = '.versioned-new-' + uuid4().hex
        try:
            binding._pin_parent(final.parent)
            try:
                os.stat(final.name, dir_fd=binding._parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(f'The selected session ID already exists: {final}')
            # A final ID must not be visible with openable legacy filenames.
            os.mkdir(stage, 0o700, dir_fd=binding._parent)
            os.fsync(binding._parent)
            binding.directory = final.parent / stage
            binding.session_id = session_id
            binding._open_root()
            binding._lease = os.open(_LEASE, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=binding._root)
            binding._lock_lease()
            token = uuid4().hex
            os.write(binding._lease, token.encode())
            os.fsync(binding._lease)
            binding._lease_stamp = _stamp(os.fstat(binding._lease))
            binding._enrollment = {
                'cohort': _COHORT, 'session_id': session_id, 'path': str(final),
                'root': list(_identity(os.fstat(binding._root))),
                'lease': list(_identity(os.fstat(binding._lease))), 'token': token,
            }
            binding._write_enrollment()
            binding._active = True
            from amplifier_foundation.session.transcript_writer import TranscriptWriter
            writer = TranscriptWriter(binding.directory, fence=binding._fence)
            ack = writer.initialize(session_id=session_id, operation_id=operation_id,
                                    admit=binding._admission(admit, (), None))
            binding.check()
            # Publication is the first appearance at the selected canonical ID.
            # Both nonempty directory fences already exist; no older legacy
            # writer can have opened either transcript filename at that ID.
            _publish(binding._parent, stage, final.name)
            binding.directory = final
            os.fsync(binding._parent)
            binding._writer = TranscriptWriter(final, fence=binding._fence)
            binding.expected = ack.commit
            binding.last_result = ack
            binding.check()
            from amplifier_foundation.session.transcript_source import open_committed_source
            with open_committed_source(final, expected=ack.commit) as source:
                if source.commit.session_id != session_id:
                    raise VersionedSessionConflict('Published source identity changed.')
            binding.check()
            return binding
        except BaseException as error:
            retained = getattr(binding, 'directory', final.parent / stage)
            binding.close()
            error.add_note(f'Prospective session preparation was not replayed. Inspect retained namespace {retained}.')
            raise

    @classmethod
    def resume(cls, session_dir, *, session_id, expected):
        final = cls._path(session_dir, session_id)
        from amplifier_foundation.session.transcript_v2 import CommittedPrefix
        if type(expected) is not CommittedPrefix or expected.session_id != session_id:
            raise VersionedSessionConflict('Resume requires an exact session-bound committed prefix.')
        binding = cls()
        try:
            binding._pin_parent(final.parent)
            binding.directory, binding.session_id = final, session_id
            binding._open_root()
            binding._lease = os.open(_LEASE, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=binding._root)
            binding._lock_lease()
            binding._lease_stamp = _stamp(os.fstat(binding._lease))
            binding._read_enrollment()
            binding._active = True
            binding.check()
            from amplifier_foundation.session.transcript_source import open_committed_source
            from amplifier_foundation.session.transcript_writer import TranscriptWriter
            with open_committed_source(final, expected=expected) as source:
                if source.commit.session_id != session_id:
                    raise VersionedSessionConflict('The selected source belongs to another session.')
                binding.expected = source.commit
            binding._writer = TranscriptWriter(final, fence=binding._fence)
            binding.check()
            return binding
        except BaseException:
            binding.close()
            raise

    def __init__(self):
        self._mutex = threading.RLock()
        self._pid = os.getpid()
        self._parents = []
        self._parent = self._root = self._lease = None
        self._active = False
        self._uncertain = False
        self._busy = False
        self._closing = threading.Event()
        self.last_result = self.last_receipt = None
        _OWNERS.add(self)

    @staticmethod
    def _path(directory, session_id):
        _id(session_id)
        path = Path(directory)
        if not path.is_absolute() or '..' in path.parts or path.name != session_id:
            raise VersionedSessionConflict('An absolute canonical directory matching the session ID is required.')
        if sys.platform != 'linux':
            raise VersionedSessionConflict('Prospective versioned custody is qualified on Linux only.')
        return path

    def _pin_parent(self, path):
        current = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self._parents.append((path.anchor, current))
        for part in path.parts[1:]:
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            self._parents.append((part, current))
        self._parent = current
        info = os.fstat(current)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise VersionedSessionConflict('The selected sessions parent must be private and owned.')

    def _open_root(self):
        self._root = os.open(self.directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             dir_fd=self._parent)
        info = os.fstat(self._root)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise VersionedSessionConflict('Prospective source must be a private owned directory.')
        self._root_identity = _identity(info)

    def _lock_lease(self):
        import fcntl
        info = os.fstat(self._lease)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise VersionedSessionConflict('The source-lifetime lease is not a private unaliased regular file.')
        try:
            fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise VersionedSessionConflict('Another prospective session owner retains the source lease.') from None

    def _write_enrollment(self):
        raw = _bytes(self._enrollment)
        fd = os.open(_ENROLLMENT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self._root)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(self._root)
        self._read_enrollment()

    def _read_enrollment(self):
        fd = os.open(_ENROLLMENT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._root)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise VersionedSessionConflict('Invalid prospective enrollment file.')
            raw = stream.read(4097)
            if len(raw) > 4096:
                raise VersionedSessionConflict('Oversized prospective enrollment file.')
            value = json.loads(raw)
            if (type(value) is not dict or set(value) != {'cohort', 'session_id', 'path', 'root', 'lease', 'token'}
                    or value['cohort'] != _COHORT or value['session_id'] != self.session_id
                    or Path(value['path']).name != self.session_id
                    or value['root'] != list(self._root_identity)
                    or value['lease'] != list(_identity(os.fstat(self._lease)))
                    or type(value['token']) is not str or not re.fullmatch('[0-9a-f]{32}', value['token'])):
                raise VersionedSessionConflict('This is not the selected prospective cohort.')
            if os.pread(self._lease, 64, 0) != value['token'].encode():
                raise VersionedSessionConflict('Prospective lease enrollment changed.')
            self._enrollment = value
            self._enrollment_raw, self._enrollment_stamp = raw, _stamp(info)

    def check(self):
        if self._pid != os.getpid() or not self._active:
            raise VersionedSessionConflict('The prospective owner is closed or belongs to another process.')
        for index, (name, descriptor) in enumerate(self._parents[1:], 1):
            linked = os.stat(name, dir_fd=self._parents[index - 1][1], follow_symlinks=False)
            if _identity(linked) != _identity(os.fstat(descriptor)):
                raise VersionedSessionConflict('A canonical parent directory was replaced.')
        linked = os.stat(self.directory.name, dir_fd=self._parent, follow_symlinks=False)
        if _identity(linked) != self._root_identity or not stat.S_ISDIR(linked.st_mode):
            raise VersionedSessionConflict('The canonical session directory was replaced.')
        if (_stamp(os.fstat(self._lease)) != self._lease_stamp
                or _stamp(os.stat(_LEASE, dir_fd=self._root, follow_symlinks=False)) != self._lease_stamp):
            raise VersionedSessionConflict('The source-lifetime lease was replaced or changed.')
        info = os.stat(_ENROLLMENT, dir_fd=self._root, follow_symlinks=False)
        if _stamp(info) != self._enrollment_stamp:
            raise VersionedSessionConflict('The prospective enrollment was changed.')
        # Staging is permitted only before the first publication; resume binds
        # exactly the final lexical path, never an arbitrary moved cohort.
        if not self.directory.name.startswith('.versioned-new-') and self._enrollment['path'] != str(self.directory):
            raise VersionedSessionConflict('The prospective source moved from its chosen canonical path.')

    @contextmanager
    def _fence(self, path):
        self._process()
        with self._mutex:
            if Path(path) != self.directory:
                raise VersionedSessionConflict('Writer requested a different source.')
            self.check()
            yield self.check
            self.check()

    def _process(self):
        if self._pid != os.getpid():
            raise VersionedSessionConflict('An inherited prospective owner cannot act in another process.')

    @contextmanager
    def publication(self):
        """Hold this real owner through a host's dependent CAS/metadata publish.

        This does not authorize another writer, create a commit, or turn a
        callback promise into exclusion. Only this binding's methods can write
        T2. Callers must settle their auxiliary publication before leaving.
        """
        self._process()
        with self._mutex:
            if self._busy or self._closing.is_set() or getattr(self, '_publication_thread', None) is not None:
                raise VersionedSessionConflict('The prospective owner is busy or closing.')
            self.check()
            self._publication_thread = threading.get_ident()
            try:
                yield self.check
                self.check()
            finally:
                self._publication_thread = None

    @contextmanager
    def _operation(self):
        self._process()  # Never wait on a Python mutex inherited locked by fork.
        with self._mutex:
            if self._busy or self._closing.is_set():
                raise VersionedSessionConflict('The prospective owner is busy or closing.')
            self.check()
            self._busy = True
            self._busy_thread = threading.get_ident()
            try:
                yield
            finally:
                self._busy = False
                self._busy_thread = None

    def _admission(self, admit, messages, media_store):
        def bound():
            self.check()
            if media_store is not None:
                self._media(media_store)
            admit()
            if media_store is not None:
                self._media(media_store)
            for row in messages:
                metadata = row.get('metadata')
                # Absent/null metadata is ordinary history. Present false,
                # array, scalar or null media namespace is not absence.
                if metadata is None:
                    continue
                if type(metadata) is not dict:
                    raise VersionedSessionConflict('Canonical metadata must be an object or null.')
                if 'experimental.media.v1' not in metadata:
                    continue
                carrier = metadata['experimental.media.v1']
                if (type(carrier) is not dict or set(carrier) - {'refs', 'v'}
                        or 'refs' not in carrier
                        or ('v' in carrier and (type(carrier['v']) is not int or carrier['v'] != 1))
                        or type(carrier['refs']) is not list or media_store is None
                        or media_store.session_id != self.session_id):
                    raise VersionedSessionConflict('Exact session-bound asset admission is required.')
                for ref in carrier['refs']:
                    self._durable_ref(media_store, ref)
            self.check()
            if media_store is not None:
                self._media(media_store)
        return bound

    def _durable_ref(self, store, ref):
        """Precommit join to the same media owner's actual retained record.

        Publication alone includes request-only observations. Match the owning
        store's checkpoint eligibility and physical asset rules BEFORE making a
        canonical ref reachable; typed ACK afterward is not an admission check.
        This does not grant model access or renew the original expiry.
        """
        from amplifier_foundation.session.media import _key, _date
        with store._lock:
            store._writable()
            normalized = store.validate_published_ref(ref, check_expiry=False)
            name = _key(normalized) + '.json'
            record = store._read(store._dirs['records'], name)
            if (type(record) is not dict or record.get('ref') != normalized
                    or record.get('state') not in {'published', 'committed'}
                    or record.get('retention') != 'durable'):
                raise VersionedSessionConflict('Canonical media requires an owned durable publication.')
            expiry = normalized['provenance']['expires_at']
            expired = expiry is not None and _date(expiry) <= datetime.now(timezone.utc)
            metadata_only = record.get('metadata_only') is True
            if metadata_only and not expired:
                raise VersionedSessionConflict('Metadata-only canonical media requires an expired original.')
            # Preserve the owner's accepted expired committed/metadata-only
            # history boundary. Newly published non-metadata-only refs always
            # need their original physical asset, even when already expired.
            if not ((record['state'] == 'committed' or metadata_only) and expired):
                with store._open_verified(normalized, store._dirs['assets'], normalized['sha256']):
                    pass
            if store._read(store._dirs['records'], name) != record:
                raise VersionedSessionConflict('Canonical media admission changed during verification.')

    def _media(self, store):
        from amplifier_foundation.session.media import SessionMediaStore
        if (type(store) is not SessionMediaStore or store.session_id != self.session_id
                or store.root != self.directory / 'media'):
            raise VersionedSessionConflict('An exact source-bound session asset store is required.')
        # Same-package physical join to the actual store's owned descriptors.
        # A lexical root alone would accept a detached, still-open media tree.
        store._writable()
        if not callable(store.checkpoint_verifier):
            raise VersionedSessionConflict('Canonical media admission requires a checkpoint verifier.')
        media = os.stat('media', dir_fd=self._root, follow_symlinks=False)
        if not stat.S_ISDIR(media.st_mode) or _identity(media) != _identity(os.fstat(store._root)):
            raise VersionedSessionConflict('The selected media namespace was replaced.')
        for name, descriptor in store._dirs.items():
            linked = os.stat(name, dir_fd=store._root, follow_symlinks=False)
            if not stat.S_ISDIR(linked.st_mode) or _identity(linked) != _identity(os.fstat(descriptor)):
                raise VersionedSessionConflict('A selected media directory was replaced.')

    def _selected(self, expected):
        self.check()
        if expected != self.expected or expected.session_id != self.session_id:
            raise VersionedSessionConflict('Expected prospective source commit changed.')
        if self._uncertain:
            raise VersionedSessionConflict('An earlier operation needs exact reconciliation before another write.')

    def rewrite(self, messages, *, expected, operation_id, admit, media_store=None):
        return self._write('rewrite', messages, expected, operation_id, admit, media_store)

    commit = rewrite

    def append(self, settled_suffix, *, expected, operation_id, admit, media_store=None):
        return self._write('append', settled_suffix, expected, operation_id, admit, media_store)

    def _write(self, kind, messages, expected, operation_id, admit, media_store):
        _id(operation_id)
        if not callable(admit):
            raise TypeError('Exact asset admission is required.')
        with self._operation():
            self._selected(expected)
            # Explicit caller-owned rows, never a guessed suffix of a snapshot.
            rows = copy.deepcopy(list(messages))
            from amplifier_foundation.session.transcript_encoding import canonical_bytes
            digest = hashlib.sha256()
            for row in rows:
                for chunk in canonical_bytes(row):
                    digest.update(chunk)
                digest.update(b'\n')
            self._pending = (operation_id, expected, kind, digest.hexdigest())
            self._pending_media = media_store is not None
            self._uncertain = True
            ack = getattr(self._writer, kind)(rows, expected=expected, operation_id=operation_id,
                                            admit=self._admission(admit, rows, media_store))
            self.check()
            if media_store is not None:
                self._checkpoint(ack, media_store)
            self.check()
            self.expected, self.last_result = ack.commit, ack
            self._uncertain = False
            self._pending = None
            self._pending_media = False
            return ack

    def _checkpoint(self, expected, media_store):
        from amplifier_foundation.session.versioned_checkpoint import acknowledge_versioned_checkpoint
        self._media(media_store)
        with acknowledge_versioned_checkpoint(self._writer, session_id=self.session_id, expected=expected) as receipt:
            media_store.acknowledge_checkpoint(receipt)
            self._media(media_store)
        self.check()
        self._media(media_store)
        self.last_receipt = receipt
        return receipt

    def checkpoint(self, *, expected, media_store):
        with self._operation():
            if self._uncertain:
                if getattr(self, '_pending_checkpoint', None) != expected or expected != self.expected:
                    raise VersionedSessionConflict('A different uncertain intent requires exact recovery.')
            else:
                self._selected(expected)
            self._uncertain = True
            self._pending_media = True
            self._pending_checkpoint = expected
            result = self._checkpoint(expected, media_store)
            self._uncertain = False
            self._pending_checkpoint = None
            self._pending_media = False
            return result

    def reconcile(self, *, operation_id, expected, suffix_sha256, kind, media_store=None):
        with self._operation():
            self.check()
            if getattr(self, '_pending_checkpoint', None) is not None:
                raise VersionedSessionConflict('An exact standalone checkpoint requires checkpoint recovery.')
            pending = getattr(self, '_pending', None)
            if pending is not None and pending != (operation_id, expected, kind, suffix_sha256):
                raise VersionedSessionConflict('Reconcile must select the original uncertain intent.')
            if getattr(self, '_pending_media', False) and media_store is None:
                raise VersionedSessionConflict('The original intent requires exact media acknowledgement recovery.')
            was_uncertain = self._uncertain
            self._pending = (operation_id, expected, kind, suffix_sha256)
            self._pending_media = getattr(self, '_pending_media', False) or media_store is not None
            self._uncertain = True
            ack = self._writer.reconcile(operation_id=operation_id, expected=expected,
                                        suffix_sha256=suffix_sha256, kind=kind)
            if ack is not None:
                from amplifier_foundation.session.transcript_source import open_committed_source
                with open_committed_source(self.directory) as source:
                    current = source.commit
                if ack.commit != current:
                    # Foundation can legitimately reconcile a historical intent;
                    # that does not replace this binding's selected current CAS.
                    if was_uncertain:
                        raise VersionedSessionConflict('The uncertain intent has a later successor; explicit diagnosis required.')
                    self.check()
                    self._uncertain = False
                    self._pending = None
                    self._pending_media = False
                    return ack
                if media_store is not None:
                    self._checkpoint(ack, media_store)
                self.expected, self.last_result = ack.commit, ack
            self.check()
            self._uncertain = False
            self._pending = None
            self._pending_media = False
            return ack

    def load_messages(self):
        with self._operation():
            self.check()
            from amplifier_foundation.session.transcript_source import open_committed_source
            with open_committed_source(self.directory, expected=self.expected) as source:
                value = []
                for index in range(source.commit.message_count):
                    span = source.message_range(index)
                    value.append(json.loads(b''.join(source.iter_bytes(span.start, span.end))))
            self.check()
            return value

    def close(self):
        if self._pid != os.getpid():
            self._release()
            return
        if self._busy and getattr(self, '_busy_thread', None) == threading.get_ident():
            raise VersionedSessionConflict('A callback cannot close its active prospective writer.')
        if getattr(self, '_publication_thread', None) == threading.get_ident():
            raise VersionedSessionConflict('A publication cannot close its active prospective owner.')
        self._closing.set()
        with self._mutex:
            if self._busy:
                raise VersionedSessionConflict('A callback cannot close its active prospective writer.')
            self._release()

    def _retire_inherited_operation_locks(self):
        """Linux child: retire copies of Foundation's operation-local flock FDs.

        No LOCK_UN: these descriptions are shared with the surviving parent.
        Replace the underlying open description with a reserved /dev/null FD.
        Raw close is unsafe if fork occurred inside admission: the surviving
        Foundation file wrapper could later close an unrelated reused number.
        dup2 drops the inherited lock but reserves that number until its wrapper
        naturally unwinds. No inherited Python mutex or file wrapper is touched.
        """
        if self._root is None:
            return
        try:
            selected = _identity(os.stat('.transcript-v2.lock', dir_fd=self._root, follow_symlinks=False))
            replacement = os.open('/dev/null', os.O_RDWR | os.O_CLOEXEC)
            try:
                for name in os.listdir('/proc/self/fd'):
                    try:
                        descriptor = int(name)
                        if _identity(os.fstat(descriptor)) == selected:
                            os.dup2(replacement, descriptor, inheritable=False)
                    except (ValueError, OSError):
                        continue
            finally:
                os.close(replacement)
        except OSError:
            pass

    def _release(self):
        self._active = False
        # Never LOCK_UN: a forked child's close must not unlock its parent.
        for descriptor in (self._lease, self._root, *(fd for _, fd in reversed(self._parents))):
            if descriptor is not None:
                os.close(descriptor)
        self._lease = self._root = self._parent = None
        self._parents.clear()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    async def call(self, method, *args, **kwargs):
        if method not in {'rewrite', 'append', 'checkpoint', 'reconcile', 'load_messages'}:
            raise ValueError('Unsupported prospective owner operation.')
        task = asyncio.create_task(asyncio.to_thread(getattr(self, method), *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except (asyncio.CancelledError, Exception):
                    pass
            if not task.cancelled():
                task.exception()
            raise

    async def aclose(self):
        task = asyncio.create_task(asyncio.to_thread(self.close))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except (asyncio.CancelledError, Exception):
                    pass
            if not task.cancelled():
                task.exception()
            raise
