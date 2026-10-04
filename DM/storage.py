"""Atomic writes and cooperating process locks for shared JSON documents."""

import hashlib
import json
import os
import shutil
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

CHUNK = 1024 * 1024
_guard = threading.Lock()
_locks = {}
_depth = threading.local()


@contextmanager
def file_lock(path, timeout=30):
    """Lock a sibling file on Windows/POSIX; nested calls in this thread are safe.

    Lock files remain on disk so another process never locks an unlinked inode.
    OS locks are released on process exit. Callers must cooperate with this lock.
    """
    key = os.path.normcase(os.path.realpath(path))
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        depths = getattr(_depth, 'paths', None)
        if depths is None:
            depths = _depth.paths = set()
        if key in depths:
            yield
            return
        Path(key).parent.mkdir(parents=True, exist_ok=True)
        with open(key + '.lock', 'a+b') as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if os.name == 'nt':
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Timed out waiting for document lock: ' + str(path))
                    time.sleep(0.02)
            depths.add(key)
            try:
                yield
            finally:
                depths.remove(key)
                if os.name == 'nt':
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def retry_sharing(action, timeout=2):
    """Retry temporary Windows sharing/access failures without dropping the lock.

    Readers, antivirus and filesystem scanners can briefly hold a file on Windows.
    Permanent permission failures still propagate after a bounded wait.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            return action()
        except PermissionError as error:
            if (
                os.name != 'nt'
                or getattr(error, 'winerror', None) not in (5, 32, 33)
                or time.monotonic() >= deadline
            ):
                raise
            time.sleep(0.02)


def atomic_replace(source, target, timeout=2):
    retry_sharing(lambda: os.replace(source, target), timeout)


def remove(path, timeout=2):
    """Delete a file if it exists, riding out the same temporary Windows failures."""

    def unlink():
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    retry_sharing(unlink, timeout)


def sync_directory(path):
    """Make a rename in this directory durable. Windows has no directory handle to flush."""
    if os.name == 'nt':
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path, value, durable=False):
    """Replace a complete JSON document. Hold file_lock for read/modify/write.

    durable flushes the new bytes, then the replacement, to disk: for records that recovery relies
    on, and for documents written before such a record is deleted.
    """
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=1), durable)


def atomic_text(path, text, durable=False):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.tmp-' + os.urandom(8).hex())
    try:
        with open(temporary, 'w', encoding='utf-8', newline='\n') as file:
            file.write(text)
            if durable:
                file.flush()
                os.fsync(file.fileno())
        atomic_replace(temporary, target)
        if durable:
            sync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def update_json(path, default, mutate, save=None):
    """Serialize the entire read/modify/write, including across forge subprocesses."""
    with file_lock(path):
        value = (
            json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else default
        )
        mutate(value)
        (save or (lambda v: atomic_json(path, v)))(value)
        return value


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def manifest_path(value):
    """Validate a manifest's relative POSIX path on every platform: no drive, backslash or dots."""
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('Invalid backup manifest path.')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('', '.', '..') for part in value.split('/')):
        raise ValueError('Invalid backup manifest path.')
    return Path(*path.parts)


def copy_and_hash(source, target):
    """Copy a regular file to a new path, hashing while copying. Fail if the source changes."""
    source, target = Path(source), Path(target)
    if source.is_symlink() or not stat.S_ISREG(source.stat(follow_symlinks=False).st_mode):
        raise ValueError(f'Source is linked or is not a regular file: {source}')
    before = source.stat()
    digest = hashlib.sha256()
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as reader, target.open('xb') as writer:
        for block in iter(lambda: reader.read(CHUNK), b''):
            writer.write(block)
            digest.update(block)
    shutil.copystat(source, target)
    after = source.stat()
    if source.is_symlink() or (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError(f'A source file changed during backup: {source}')
    return {'bytes': before.st_size, 'sha256': digest.hexdigest()}
