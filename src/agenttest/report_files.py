"""Directory-pinned report I/O for local POSIX and Windows filesystems.

The operator owns the root and its ancestors. These guards reject links and pin
directories during I/O; they do not defend against an administrator or raw disk writes.
"""

import errno
import os
import stat
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from pathlib import Path


def regular(info) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise ValueError("Linked or non-regular report file")


@lru_cache(maxsize=1)
def _windows_api():
    """Reuse function bindings only; never cache filesystem attributes or handles."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create_file.restype = wintypes.HANDLE
    attributes = kernel.GetFileAttributesW
    attributes.argtypes = [wintypes.LPCWSTR]
    attributes.restype = wintypes.DWORD
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    return create_file, attributes, close


def _windows_open(path: Path, *, directory=False, create=False, readonly=False) -> int:
    """Open the reparse point itself; deny deletion/replacement while held."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file, attributes, close = _windows_api()
    access = 0x80 if directory else (0x80000000 if readonly else 0xC0000000)
    handle = create_file(str(path), access, 3, None, 1 if create else 3,
                         0x00200000 | (0x02000000 if directory else 0), None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    # The handle denies rename, so this attribute read refers to the pinned path.
    attrs = attributes(str(path))
    if attrs == 0xFFFFFFFF or attrs & 0x400 or bool(attrs & 0x10) != directory:
        close(handle)
        raise ValueError("Linked reports are forbidden")
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY if directory or readonly else os.O_RDWR)
    except BaseException:
        close(handle)
        raise


def _windows_directory(path: Path, *, create: bool) -> int:
    # The open pins the path and rejects reparse points itself. A preliminary
    # lstat and mkdir on every existing ancestor duplicate that same lookup.
    try:
        return _windows_open(path, directory=True)
    except FileNotFoundError:
        if not create:
            raise
    try:
        path.mkdir()
    except FileExistsError:
        pass
    # Recheck even when another writer created the path: it may be a link.
    return _windows_open(path, directory=True)


class ReportDirectory:
    def __init__(self, path: Path, descriptor: int):
        self.path, self.descriptor = path, descriptor

    def open(self, name: str, *, create=False, readonly=False) -> int:
        if os.name == "nt":
            descriptor = _windows_open(self.path / name, create=create, readonly=readonly)
        else:
            flags = (os.O_RDONLY if readonly else os.O_RDWR) | os.O_NOFOLLOW | os.O_NONBLOCK
            if create:
                flags |= os.O_CREAT | os.O_EXCL
            descriptor = os.open(name, flags, 0o600, dir_fd=self.descriptor)
        try:
            regular(os.fstat(descriptor))
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def inspect(self, name: str) -> None:
        try:
            info = (os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
                    if os.name == "posix" else (self.path / name).lstat())
        except FileNotFoundError:
            return
        regular(info)

    def replace(self, source: str, target: str) -> None:
        self.inspect(source)
        self.inspect(target)
        if os.name == "posix":
            os.replace(source, target, src_dir_fd=self.descriptor, dst_dir_fd=self.descriptor)
        else:
            (self.path / source).replace(self.path / target)

    def unlink(self, name: str) -> None:
        if os.name == "posix":
            os.unlink(name, dir_fd=self.descriptor)
        else:
            (self.path / name).unlink()


@contextmanager
def pinned_directory(root: Path, run_id: str, *, create: bool):
    """Walk without following links, keeping every ancestor pinned until publication."""
    path = Path(root.anchor)
    with ExitStack() as stack:
        descriptor = None
        for name in [*root.parts[1:], run_id]:
            if descriptor is None:
                descriptor = (_windows_open(path, directory=True) if os.name == "nt" else
                              os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
                stack.callback(os.close, descriptor)
            path /= name
            if os.name == "nt":
                descriptor = _windows_directory(path, create=create)
                stack.callback(os.close, descriptor)
                continue
            try:
                info = path.lstat()
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("Linked reports are forbidden")
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            try:
                descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=descriptor)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError("Linked or non-directory report path") from None
                raise
            stack.callback(os.close, descriptor)
        yield ReportDirectory(path, descriptor)


@contextmanager
def writer_lock(directory: ReportDirectory):
    """Persistent lock inode, OS-released on crash; never unlink it while contenders exist."""
    try:
        descriptor = directory.open(".writer.lock", create=True)
    except FileExistsError:
        descriptor = directory.open(".writer.lock")
    with os.fdopen(descriptor, "r+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("Report writer already active") from None
        yield
