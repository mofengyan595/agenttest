"""A cross-process execution lock for one frozen acceptance folder.

The position journal makes a case single-use, but M2 and F1 share the same loopback fixture
and port. This module adds a system-level advisory lock around the whole execution: the
fixture is started and reset only while the lock is held, and a second process is refused
immediately instead of disturbing the running attempt.

The lock is a real file descriptor lock (``msvcrt.locking`` on Windows, ``fcntl.flock`` on
POSIX), not a temporary ``*.lock`` marker: the operating system drops it when the process
dies, so a crash never leaves a permanent lock, and nothing here deletes a lock file to
"recover". A released lock does not make a consumed position runnable again; the atomic
claim directory remains the only permission to attempt a case.
"""

import contextlib
import json
import os
import time

LOCK_NAME = "execution.lock"


class RunLocked(Exception):
    """Another process owns the fixture; the caller must not start or reset it."""

    def __init__(self, path):
        self.path = str(path)
        super().__init__(f"EXECUTION_LOCKED:{path}")


def lock_path(root):
    return os.path.join(str(root), LOCK_NAME)


def _acquire(handle):
    if os.name == "nt":
        import msvcrt

        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(handle):
    if os.name == "nt":
        import msvcrt

        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle, fcntl.LOCK_UN)


@contextlib.contextmanager
def execution_lock(root, holder=None):
    """Hold the fixture for the complete run; refuse immediately when it is taken."""
    os.makedirs(str(root), exist_ok=True)
    path = lock_path(root)
    handle = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            _acquire(handle)
        except OSError:
            raise RunLocked(path) from None
        # Holder information is diagnostics only; the OS lock, not this text, is authoritative.
        try:
            text = json.dumps(dict(pid=os.getpid(), holder=holder,
                                   since=time.strftime("%Y-%m-%dT%H:%M:%S%z")),
                              ensure_ascii=False) + "\n"
            os.lseek(handle, 0, os.SEEK_SET)
            os.ftruncate(handle, 0)
            os.write(handle, text.encode("utf-8"))
            os.fsync(handle)
        except OSError:
            pass
        yield path
    finally:
        try:
            _release(handle)
        except OSError:
            pass
        os.close(handle)


def current_holder(root):
    """Read the diagnostic holder record without taking or changing the lock."""
    try:
        with open(lock_path(root), encoding="utf-8") as handle:
            return json.loads(handle.read() or "{}")
    except (OSError, ValueError):
        return None
