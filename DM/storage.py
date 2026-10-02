"""Atomic writes and cooperating process locks for shared JSON documents."""

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

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


def atomic_replace(source, target, timeout=2):
    """Retry temporary Windows sharing/access failures without dropping the lock.

    Readers and filesystem scanners can briefly prevent replacement on Windows.
    Permanent permission failures still propagate after a bounded wait.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError as error:
            if (
                os.name != 'nt'
                or getattr(error, 'winerror', None) not in (5, 32, 33)
                or time.monotonic() >= deadline
            ):
                raise
            time.sleep(0.02)


def atomic_json(path, value):
    """Replace a complete JSON document. Hold file_lock for read/modify/write."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.tmp-' + os.urandom(8).hex())
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')
        atomic_replace(temporary, target)
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
