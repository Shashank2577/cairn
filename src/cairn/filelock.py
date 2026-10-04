"""Advisory cross-process file locks on every OS: ``fcntl.flock`` on POSIX, an ``msvcrt`` byte-range lock on
Windows (which has no flock). Standard library only, so every engine can import it."""
from __future__ import annotations

import os
import time

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]


def try_lock(fh) -> bool:
    """Take an exclusive lock on the open file without waiting; False when another holder has it."""
    if fcntl is not None:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True
    import msvcrt
    try:
        os.lseek(fh.fileno(), 0, os.SEEK_SET)  # the lock covers one byte from the file position
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:  # contended (EACCES / EDEADLOCK)
        return False
    return True


def lock(fh, timeout: float | None = 0.0, poll: float = 0.01) -> bool:
    """Take the lock, polling for up to ``timeout`` seconds (None: until it is free). False on timeout."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while not try_lock(fh):
        if deadline is not None and time.monotonic() > deadline:
            return False
        time.sleep(poll)
    return True


def unlock(fh) -> None:
    """Release a lock taken with :func:`lock` / :func:`try_lock` (closing the file also releases it)."""
    if fcntl is not None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return
    import msvcrt
    os.lseek(fh.fileno(), 0, os.SEEK_SET)
    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
