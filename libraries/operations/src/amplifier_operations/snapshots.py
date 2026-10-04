"""Explicit held-writer SQLite images. No discovery, authentication or activation."""
from __future__ import annotations
import hashlib,json,os,re,sqlite3,stat,time
from pathlib import Path

VERSION=1
MAX_STORES=16
MAX_MANIFEST=16384
DEFAULT_MAX_BYTES=64*1024*1024
IDENTITY=re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z')

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def checked(value):
    path=Path(value)
    if not path.is_absolute():raise ValueError('Explicit absolute snapshot path required')
    for part in (path,*path.parents):
        if part.is_symlink():raise ValueError('Snapshot paths cannot traverse symbolic links')
    return path

def stamp(path):
    path=checked(path)
    if not path.exists():return None
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('Snapshot source must be an unlinked regular file')
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)

def revision(path):
    journal=checked(Path(str(path)+'-journal'));wal=Path(str(path)+'-wal')
    stamp(Path(str(path)+'-shm'))
    if journal.exists():raise ValueError('Unresolved SQLite journal; preserve original')
    database=stamp(path);side=stamp(wal)
    if database is None and side is not None:raise ValueError('Orphan SQLite WAL; preserve original')
    return database,side

def guard(assert_held,seconds=30):
    if not callable(assert_held):raise ValueError('Caller-held writer guard required')
    deadline=time.monotonic()+seconds
    def bounded():
        assert_held()
        if time.monotonic()>deadline:raise ValueError('Snapshot deadline exceeded; inspect original outcome')
    bounded();return bounded

def budget(max_bytes):
    if type(max_bytes) is not int or not 1024<=max_bytes<=1024*1024*1024:raise ValueError('Bounded snapshot byte budget required')
    return max_bytes

def sync(path):
    with path.open('rb') as stream:os.fsync(stream.fileno())

def sync_directory(path):
    if os.name=='nt':return
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def file_digest(path,maximum,bounded):
    before=stamp(path)
    if before is None or before[2]>maximum:raise ValueError('Snapshot image exceeds byte budget or is missing')
    result=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(65536):bounded();result.update(block)
    if stamp(path)!=before:raise ValueError('Snapshot image changed during verification')
    return result.hexdigest(),before[2]

def integrity(path,bounded):
    db=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    try:
        db.set_progress_handler(lambda:(bounded(),0)[1],1000)
        if db.execute('PRAGMA quick_check').fetchone()!=('ok',):raise ValueError('Snapshot image integrity failed')
    finally:db.close()

def capture_snapshot(sources,directory,*,assert_held,max_bytes=DEFAULT_MAX_BYTES):
    """Caller supplies the complete explicit store census and held writer guard.

    Missing stores are explicit. The caller owns lifetime locking, authorization,
    pending outcomes and namespace completeness. A partial destination is retained
    after failure; an existing destination never permits capture replay.
    """
    remaining=budget(max_bytes);bounded=guard(assert_held)
    if not isinstance(sources,dict) or not 1<=len(sources)<=MAX_STORES or any(not isinstance(key,str) or not IDENTITY.fullmatch(key) for key in sources):raise ValueError('At most 16 explicit distinct store identities required')
    paths={key:checked(value) for key,value in sources.items()}
    if len(set(paths.values()))!=len(paths):raise ValueError('Source stores must be distinct')
    directory=checked(directory)
    if any(directory==path or path in directory.parents for path in paths.values()):raise ValueError('Snapshot destination cannot replace source authority')
    before={key:revision(path) for key,path in paths.items()}
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    entries=[]
    for key,path in sorted(paths.items()):
        bounded()
        if before[key][0] is None:entries.append({'id':key,'status':'missing'});continue
        with path.open('rb') as stream:
            if stream.read(16)!=b'SQLite format 3\x00':raise ValueError('Declared store is not SQLite')
        image=directory/(key+'.sqlite3')
        uri=path.as_uri()+'?mode=ro'+('&immutable=1' if before[key][1] is None else '')
        source=sqlite3.connect(uri,uri=True,timeout=0,isolation_level=None)
        target=sqlite3.connect(image,timeout=0,isolation_level=None);image.chmod(0o600)
        try:
            page_size=source.execute('PRAGMA page_size').fetchone()[0]
            def progress(status,pages,total):
                bounded()
                if total*page_size>remaining:raise ValueError('Snapshot images exceed byte budget')
            source.backup(target,pages=64,progress=progress,sleep=.01)
            target.execute('PRAGMA journal_mode=DELETE');target.execute('PRAGMA synchronous=FULL')
        finally:target.close();source.close()
        integrity(image,bounded);sha,size=file_digest(image,remaining,bounded);remaining-=size;sync(image)
        if revision(path)!=before[key]:raise ValueError('Source store changed while writers were held')
        entries.append({'id':key,'status':'captured','file':image.name,'sha256':sha,'bytes':size})
    bounded()
    if any(revision(path)!=before[key] for key,path in paths.items()):raise ValueError('Source store set changed during capture')
    manifest={'schema':'amplifier-sqlite-snapshot-set','version':VERSION,'stores':entries,'activationProvided':False}
    raw=canonical(manifest)
    if len(raw)>MAX_MANIFEST:raise ValueError('Snapshot manifest exceeds bound')
    path=directory/'manifest.json'
    with path.open('xb') as stream:os.chmod(path,0o600);stream.write(raw);stream.flush();os.fsync(stream.fileno())
    sync_directory(directory);sync_directory(directory.parent)
    return {**manifest,'manifestSha256':hashlib.sha256(raw).hexdigest()}

def restore_snapshot(directory,destination,*,manifest_sha256,assert_held,max_bytes=DEFAULT_MAX_BYTES):
    """Restore exact reviewed images into a NEW inactive destination.

    The manifest digest comes from authenticated caller evidence, not the archive.
    No process starts, fence clears, receipt rewrites or operation replays.
    """
    remaining=budget(max_bytes);bounded=guard(assert_held);directory=checked(directory);destination=checked(destination)
    if not isinstance(manifest_sha256,str) or not re.fullmatch('[a-f0-9]{64}',manifest_sha256):raise ValueError('Trusted exact manifest digest required')
    manifest_path=directory/'manifest.json';info=stamp(manifest_path)
    if info is None or info[2]>MAX_MANIFEST:raise ValueError('Bounded snapshot manifest required')
    raw=manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest_sha256:raise ValueError('Reviewed manifest digest differs')
    manifest=json.loads(raw)
    if not isinstance(manifest,dict) or set(manifest)!={'schema','version','stores','activationProvided'} or manifest['schema']!='amplifier-sqlite-snapshot-set' or type(manifest['version']) is not int or manifest['version']!=VERSION or manifest['activationProvided'] is not False:raise ValueError('Unsupported exact snapshot manifest')
    stores=manifest['stores']
    if not isinstance(stores,list) or not 1<=len(stores)<=MAX_STORES:raise ValueError('Bounded store census required')
    seen=set();images=[]
    for row in stores:
        if not isinstance(row,dict):raise ValueError('Exact store descriptor required')
        key=row.get('id')
        if not isinstance(key,str) or not IDENTITY.fullmatch(key) or key in seen:raise ValueError('Invalid distinct snapshot store')
        seen.add(key)
        if row.get('status')=='missing' and set(row)=={'id','status'}:continue
        if set(row)!={'id','status','file','sha256','bytes'} or row['status']!='captured' or row['file']!=key+'.sqlite3' or type(row['bytes']) is not int or row['bytes']<0:raise ValueError('Exact captured store descriptor required')
        image=checked(directory/row['file']);sha,size=file_digest(image,remaining,bounded)
        if sha!=row['sha256'] or size!=row['bytes']:raise ValueError('Snapshot image differs from reviewed manifest')
        remaining-=size;integrity(image,bounded);images.append((row,image))
    bounded();destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    for row,image in images:
        target=destination/row['file']
        with image.open('rb') as source,target.open('xb') as output:
            os.chmod(target,0o600)
            copied=0
            while block:=source.read(65536):
                bounded();copied+=len(block)
                if copied>row['bytes']:raise ValueError('Source image grew during restore')
                output.write(block)
            output.flush();os.fsync(output.fileno())
        sha,size=file_digest(target,row['bytes'],bounded)
        if sha!=row['sha256'] or size!=row['bytes']:raise ValueError('Restored image differs; destination remains inactive')
    bounded();sync_directory(destination);sync_directory(destination.parent)
    return {'restored':True,'activationProvided':False,'manifestSha256':manifest_sha256,'stores':[row['id'] for row,_ in images]}
