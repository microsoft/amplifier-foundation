"""Private checked source/data join; the injected host producer owns permission.

No receipt, issuer, registry or consent is created here. Original producer policy
and occurrence truth cannot be reconstructed from matching transcript bytes.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import tempfile
from types import MappingProxyType

from ._media_conversion_parser import Scanner, Span, Unsupported, copy_span


def _exact(value, keys, description):
    if type(value) is not dict or set(value) != set(keys):
        raise Unsupported(f"Invalid cohort {description}")


def _index(value):
    if type(value) is not int or value < 0:
        raise Unsupported("Cohort position must be an exact nonnegative integer")
    return value


class _BoundCohort:
    def __init__(self, source, owner, producer, store, db, directory):
        self.source, self.owner, self.producer, self.store, self.db = source, owner, producer, store, db
        self.directory = directory
        self.active = True
        self._nested = False

    def _live(self):
        if not self.active:
            raise Unsupported("Committed media cohort context is closed")
        self.source.read_range(0, 0)
        if self.store.policy.retention != 'durable':
            raise Unsupported("Current media retention no longer permits durable conversion")

    @contextmanager
    def fence(self, owner):
        """Reuse ONLY this already-held owner fence for the existing builder."""
        if owner != self.owner or self._nested:
            raise Unsupported("Cohort builder fence owner/reentrancy differs")
        self._live()
        self._nested = True
        try:
            yield
            self.check()
        finally:
            self._nested = False

    def _row(self, row):
        _index(row)
        result = self.db.execute('select facts from rows where row=?', (row,)).fetchone()
        if result is None:
            raise Unsupported("Cohort row is outside the selected committed source")
        return json.loads(result[0])

    def _images(self, row):
        for (raw,) in self.db.execute('select facts from images where row=? order by block', (row,)):
            self._live()
            yield MappingProxyType(json.loads(raw))
        self._live()

    def _mapping(self, row, *, initial):
        from . import media_conversion as c
        from .media import validate_blob_ref
        self._live()
        facts = self._row(row)
        value = self.producer(commit=self.source.commit, rowfacts=deepcopy(facts), images=self._images(row))
        self._live()
        if value is None:
            return None
        _exact(value, ('source_commit', 'row', 'projection', 'groups'), 'row binding')
        # JSON byte equality rejects bool/float selector equivalence too.
        if (c._json(value['source_commit']) != c._json(asdict(self.source.commit))
                or c._json(value['row']) != c._json(facts)):
            raise Unsupported("Producer cohort source prefix/physical row differs")
        if value['projection'] != 'canonical-original':
            return None  # Echo/unknown projection does not authorize a raw copy.
        fingerprint = c._HashWriter()
        self.db.execute('delete from pending where row=?', (row,))
        held = False
        groups = 0
        for group in value['groups']:
            self._live()
            _exact(group, ('origin', 'retention', 'members'), 'group')
            origin = group['origin']
            _exact(origin, ('kind', 'session_id', 'id', 'created_at', 'expires_at'), 'original provenance')
            if group['retention'] != 'durable':
                held = True
            refs = []  # Existing F1 API costs O(one complete true-origin group).
            first_block = -1
            for member in group['members']:
                _exact(member, ('block_index', 'occurrence_id', 'ref'), 'member')
                block = _index(member['block_index'])
                occurrence = member['occurrence_id']
                if type(occurrence) is not str or not occurrence or len(occurrence.encode()) > 4096:
                    raise Unsupported("Invalid producer occurrence identity")
                ref = validate_blob_ref(member['ref'])
                image = self.db.execute('select facts from images where row=? and block=?', (row, block)).fetchone()
                if image is None or block <= first_block:
                    raise Unsupported("Producer group order/physical image block differs")
                image = json.loads(image[0])
                if (ref['provenance'] != origin or ref['session_id'] != self.store.session_id
                        or ref['mime'] != image['mime']):
                    raise Unsupported("Producer original ref/MIME/owner differs from source image")
                if c._expired(ref):
                    held = True
                try:
                    self.db.execute('insert into pending values(?,?,?,?,?,?)',
                                    (row, block, occurrence, c._json(origin), c._json(ref), groups))
                except sqlite3.IntegrityError as exc:
                    raise Unsupported("Duplicate cohort block or occurrence identity") from exc
                refs.append(ref)
                first_block = block
                limit = c._host_image_limit(self.store)
                if limit is not None and len(refs) > limit:
                    raise Unsupported("Producer group exceeds actual host image limit")
            if not refs:
                held = True
            else:
                try:
                    self.store.validate_published_group(refs, origin=origin)
                except ValueError:
                    if not initial:
                        raise
                    held = True  # Partial/post-hook publication cannot be re-ingested whole.
            fingerprint.write(c._json({'origin': origin, 'retention': group['retention'], 'members': [
                {'block_index': item[0], 'occurrence_id': item[1], 'ref': json.loads(item[2])}
                for item in self.db.execute('select block,occurrence,ref from pending where row=? and grp=? order by block',
                                            (row, groups))
            ]}))
            groups += 1
        self._live()  # Includes checks after exhaustion of producer group generators.
        observed = self.db.execute('select count(*) from pending where row=?', (row,)).fetchone()[0]
        expected = self.db.execute('select count(*) from images where row=?', (row,)).fetchone()[0]
        if held or observed != expected or not observed:
            return None
        return fingerprint.result()

    def _inventory(self):
        from . import media_conversion as c
        stream = c._CommittedView(self.source, 0)
        scanner = Scanner(stream)
        for row, record in enumerate(c._records(scanner, asdict(self.source.commit))):
            self._live()
            fields = c._message(scanner, record)
            bounds = self.source.message_range(row)
            if (record.start, record.end) != (bounds.start, bounds.end):
                raise Unsupported("Committed row offsets differ from canonical scanner")
            digest = c._HashWriter()
            copy_span(stream, digest, record.start, record.end)
            facts = dict(index=row, start=record.start, end=record.end,
                         sha256=digest.result()[0], role=scanner.small(fields['role']))
            self.db.execute('insert into rows values(?,?,NULL,0)', (row, c._json(facts)))
            for block, image, data, mime in c._images(scanner, fields):
                facts = dict(block_index=block, block_start=image.start, block_end=image.end,
                             data_start=data.start, data_end=data.end, mime=mime)
                self.db.execute('insert into images values(?,?,?)', (row, block, c._json(facts)))
        self._live()
        # Classify the complete source before any base64 decoding or store effects.
        # A true origin repeated in another row is unsupported for this first slice.
        for (row,) in self.db.execute('select distinct row from images order by row'):
            result = self._mapping(row, initial=True)
            if result is not None:
                self.db.execute('update rows set witness=?,eligible=1 where row=?', (c._json(result), row))
            self.db.execute('insert into members select * from pending where row=?', (row,))
        self.db.execute('update rows set eligible=0 where row in (select row from members where origin in '
                        '(select origin from members group by origin having count(distinct row)>1))')
        # Decode only after all source/schema/policy/group coverage is settled.
        for row, block, image_raw, ref_raw in self.db.execute(
            'select i.row,i.block,i.facts,m.ref from images i join members m using(row,block) '
            'join rows r using(row) where r.eligible=1 order by i.row,i.block'
        ):
            self._live()
            image, ref = json.loads(image_raw), json.loads(ref_raw)
            scanner = Scanner(c._CommittedView(self.source, 0))
            span = Span(image['data_start'], image['data_end'], 34)
            with tempfile.TemporaryFile(mode='w+b') as decoded:
                digest, size = c._decode(scanner, span, decoded)
            if (digest, size) != (ref['sha256'], ref['bytes']):
                raise Unsupported("Producer decoded image identity differs from committed inline bytes")
            self._live()
        self.db.commit()
        self.check()

    def _check_row(self, row):
        from . import media_conversion as c
        self._live()
        saved = self.db.execute('select witness,eligible from rows where row=?', (row,)).fetchone()
        if saved is None:
            raise Unsupported("Unknown cohort row")
        if not saved[1]:
            return False
        result = self._mapping(row, initial=False)
        if result is None or c._json(result) != saved[0]:
            raise Unsupported("Producer cohort current permission/mapping changed")
        return True

    def check(self):
        self._live()
        for (row,) in self.db.execute('select row from rows where eligible=1 order by row'):
            self._check_row(row)
        self._live()

    def eligible_record(self, row):
        return self._check_row(_index(row))

    def origin_for(self, row, block):
        if not self._check_row(_index(row)):
            raise Unsupported("Held row has no permitted original provenance")
        item = self.db.execute('select origin from members where row=? and block=?', (row, _index(block))).fetchone()
        if item is None:
            raise Unsupported("Unknown cohort image position")
        return json.loads(item[0])

    def check_builder(self, plan, store):
        from . import media_conversion as c
        if (store is not self.store or plan.owner != self.owner
                or plan.committed_session_dir != str(self.directory)
                or c._json(plan.committed_prefix) != c._json(asdict(self.source.commit))
                or plan.target_prefix is not None or plan.portable_directory is not None):
            raise Unsupported("Cohort callbacks belong to a different source/builder/store")
        self.check()


@contextmanager
def bind(session_dir, *, expected, owner, fence, producer, store):
    from . import media_conversion as c
    from .media import SessionMediaStore
    from .transcript_source import open_committed_source
    c._prefix_types(expected)
    if (not callable(producer) or type(store) is not SessionMediaStore or store.read_only
            or type(owner) is not str or not owner or store.session_id != expected.session_id
            or store.root != Path(session_dir) / 'media' or store.policy.retention != 'durable'):
        raise Unsupported("Actual producer, writable source store and durable owner context required")
    with fence(owner), open_committed_source(session_dir, expected=expected) as source:
        # Anonymous disk database, no persistent registry or authority artifact.
        with closing(sqlite3.connect('')) as db:
            db.execute('pragma cache_size=-512')
            db.execute('pragma temp_store=FILE')
            db.execute('create table rows(row integer primary key,facts blob,witness blob,eligible integer)')
            db.execute('create table images(row integer,block integer,facts blob,primary key(row,block))')
            db.execute('create table pending(row integer,block integer,occurrence text,origin blob,ref blob,grp integer,primary key(row,block),unique(row,occurrence))')
            db.execute('create table members(row integer,block integer,occurrence text,origin blob,ref blob,grp integer,primary key(row,block))')
            cohort = _BoundCohort(source, owner, producer, store, db, Path(session_dir))
            try:
                cohort._inventory()
                yield cohort
                cohort.check()
            finally:
                cohort.active = False
