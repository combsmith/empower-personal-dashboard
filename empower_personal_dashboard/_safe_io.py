"""
_safe_io.py — Directory-anchored file I/O that resists path-swap races.

Path guards such as ``client._safe_fs_path`` validate a pathname, but a later
``open``/``os.replace`` on that same pathname resolves it again. A local actor
who renames an ancestor and plants a symlink in between redirects the write
(CWE-367). :class:`PinnedDir` closes that window: it opens the validated
directory one component at a time with ``O_NOFOLLOW`` (refusing a symlink
anywhere in the chain) and then performs every create, replace and read
relative to the resulting directory file descriptor, so later renames of the
path cannot change which directory is used.

Platforms without ``dir_fd`` / ``O_NOFOLLOW`` support (e.g. Windows) cannot
hold a directory open for relative operations from pure Python: ``os`` exposes
no ``openat``-style calls there. The fallback instead checks every component
with ``lstat`` (refusing symlinks and junctions, creating missing components
one at a time), records the directory's ``(st_dev, st_ino)`` identity, and
re-checks both before every operation. That refuses a swap made between
pinning and use, but a swap landing inside a single operation's own
check-then-use window cannot be ruled out without native handle-relative APIs.
"""

import errno
import os
import stat
import uuid
from pathlib import Path
from typing import IO, Any, Callable, Dict, List, Optional, Tuple, Union

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
# Windows: keep the fd binary so the text layer alone handles newlines.
_O_BINARY = getattr(os, "O_BINARY", 0)
# Open reads non-blocking so a FIFO planted at the path cannot hang the reader.
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
# Open directories for search only (Linux O_PATH, macOS/BSD O_SEARCH) so an
# ancestor that grants execute but not read permission can still be traversed,
# as it can with a plain pathname; O_RDONLY (0) otherwise.
_O_DIR_SEARCH = getattr(os, "O_PATH", 0) or getattr(os, "O_SEARCH", 0) or os.O_RDONLY

# ``os.replace`` shares ``renameat`` with ``os.rename`` but is not listed in
# ``os.supports_dir_fd`` on every Python build, so probe ``os.rename`` instead.
DIR_FD_SUPPORTED = (
    bool(_O_NOFOLLOW and _O_DIRECTORY)
    and all(fn in os.supports_dir_fd for fn in (os.open, os.mkdir, os.rename, os.unlink, os.stat))
    and os.listdir in os.supports_fd
)

# errno values an ``O_NOFOLLOW`` open reports for a symlinked component
# (ELOOP on Linux/macOS, EMLINK on FreeBSD; ENOTDIR for a non-directory).
_SYMLINK_ERRNOS = frozenset({errno.ELOOP, errno.EMLINK, errno.ENOTDIR})


def _check_name(name: str) -> None:
    """Accept only a single, plain path component."""
    if not name or name in (".", "..") or "/" in name or os.sep in name or "\x00" in name:
        raise ValueError(f"Invalid file name: {name!r}")


# Windows reparse tags with the "name surrogate" bit set (symlinks, junctions /
# mount points) redirect a path elsewhere, exactly like a POSIX symlink.
_REPARSE_NAME_SURROGATE = 0x20000000


def is_link_like(st: os.stat_result) -> bool:
    """True when an ``lstat`` result is a symlink or a redirecting reparse point."""
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_reparse_tag", 0) & _REPARSE_NAME_SURROGATE)


def _symlinked_component_error(path: Path) -> ValueError:
    return ValueError(f"Refusing to use directory with a symlinked or non-directory component: {path}")


def _open_dir_at(parent_fd: int, name: str, create: bool, display: Path) -> int:
    """Open directory ``name`` relative to ``parent_fd`` without following a symlink."""
    _check_name(name)
    flags = _O_DIR_SEARCH | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
    try:
        try:
            return os.open(name, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            if not create:
                raise
            try:
                os.mkdir(name, dir_fd=parent_fd)
            except FileExistsError:
                pass
            return os.open(name, flags, dir_fd=parent_fd)
    except OSError as e:
        if e.errno in _SYMLINK_ERRNOS:
            raise _symlinked_component_error(display) from e
        raise


def _walk_path(path: Path, create: bool) -> Tuple[int, int]:
    """Path fallback: check each component with ``lstat``; return the leaf's identity."""
    current = Path(path.anchor)
    st = os.lstat(current)
    for name in path.parts[1:]:
        _check_name(name)
        current = current / name
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            if not create:
                raise
            try:
                os.mkdir(current)
            except FileExistsError:
                pass
            st = os.lstat(current)
        if is_link_like(st) or not stat.S_ISDIR(st.st_mode):
            raise _symlinked_component_error(path)
    return (st.st_dev, st.st_ino)


class PinnedDir:
    """A directory held open by file descriptor (or by validated path as a fallback).

    ``path`` must be absolute and already validated by the caller. With
    ``create=True`` missing components are created (default mode, umask
    applied), each one relative to its already-opened parent.
    """

    def __init__(self, path: Union[str, Path], *, create: bool = False) -> None:
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError(f"Directory path must be absolute: {self.path}")
        self.fd: Optional[int] = None
        self._identity: Optional[Tuple[int, int]] = None
        if DIR_FD_SUPPORTED:
            self.fd = self._open_nofollow(create)
        else:
            self._identity = _walk_path(self.path, create)

    @classmethod
    def _from_fd(cls, path: Path, fd: int) -> "PinnedDir":
        obj = cls.__new__(cls)
        obj.path = path
        obj.fd = fd
        obj._identity = None
        return obj

    def _open_nofollow(self, create: bool) -> int:
        flags = _O_DIR_SEARCH | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
        parts = self.path.parts
        fd = os.open(parts[0], flags)
        try:
            for name in parts[1:]:
                nxt = _open_dir_at(fd, name, create, self.path)
                os.close(fd)
                fd = nxt
        except BaseException:
            os.close(fd)
            raise
        return fd

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> "PinnedDir":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _revalidate(self) -> None:
        """Path fallback: refuse if the directory was swapped since it was pinned."""
        try:
            identity = _walk_path(self.path, create=False)
        except FileNotFoundError as e:
            raise ValueError(f"Pinned directory no longer exists: {self.path}") from e
        if identity != self._identity:
            raise ValueError(f"Refusing to use directory replaced since it was pinned: {self.path}")

    def _target(self, name: str) -> str:
        if self.fd is not None:
            return name
        self._revalidate()
        return str(self.path / name)

    def _dir_kw(self) -> Dict[str, int]:
        return {"dir_fd": self.fd} if self.fd is not None else {}

    def _refuse_link_leaf_without_fd(self, name: str) -> None:
        """Path fallback: ``O_NOFOLLOW`` may be unavailable, so check the leaf first."""
        if self.fd is None:
            st = self.lstat(name)
            if st is not None and is_link_like(st):
                raise OSError(errno.ELOOP, "Refusing to open symlinked file", str(self.path / name))

    def subdir(self, name: str, *, create: bool = False) -> "PinnedDir":
        """Pin child directory ``name`` relative to this one (the caller closes it)."""
        _check_name(name)
        if self.fd is None:
            self._revalidate()
            return PinnedDir(self.path / name, create=create)
        return PinnedDir._from_fd(self.path / name, _open_dir_at(self.fd, name, create, self.path / name))

    def lstat(self, name: str) -> Optional[os.stat_result]:
        """``lstat`` ``name`` inside the directory; ``None`` when it does not exist."""
        _check_name(name)
        try:
            return os.stat(self._target(name), follow_symlinks=False, **self._dir_kw())
        except FileNotFoundError:
            return None

    def listdir(self) -> List[str]:
        """Sorted entry names of the pinned directory."""
        if self.fd is None:
            self._revalidate()
            return sorted(os.listdir(self.path))
        # The pin may be a search-only (O_PATH) descriptor; open the same
        # directory for reading relative to it.
        fd = os.open(".", os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC, dir_fd=self.fd)
        try:
            return sorted(os.listdir(fd))
        finally:
            os.close(fd)

    def replace(self, src: str, dst: str) -> None:
        """Atomically rename ``src`` over ``dst``, both inside the directory."""
        _check_name(src)
        _check_name(dst)
        if self.fd is not None:
            os.replace(src, dst, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        else:
            os.replace(self._target(src), self._target(dst))

    def unlink(self, name: str, *, missing_ok: bool = True) -> None:
        _check_name(name)
        try:
            os.unlink(self._target(name), **self._dir_kw())
        except FileNotFoundError:
            if not missing_ok:
                raise

    def _discard(self, name: str) -> None:
        """Best-effort cleanup that never masks the error being handled."""
        try:
            self.unlink(name)
        except (OSError, ValueError):
            pass

    def open_text(self, name: str, encoding: str = "utf-8") -> IO[str]:
        """Open ``name`` for reading, refusing a symlinked or non-regular file."""
        _check_name(name)
        flags = os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC | _O_BINARY | _O_NONBLOCK
        try:
            self._refuse_link_leaf_without_fd(name)
            fd = os.open(self._target(name), flags, **self._dir_kw())
        except OSError as e:
            if e.errno in (errno.ELOOP, errno.EMLINK):
                raise ValueError(f"Refusing to read symlinked file: {self.path / name}") from e
            raise
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError(f"Refusing to read non-regular file: {self.path / name}")
            if _O_NONBLOCK:
                os.set_blocking(fd, True)
            return os.fdopen(fd, "r", encoding=encoding)
        except BaseException:
            os.close(fd)
            raise

    def open_private_append(self, name: str) -> int:
        """Open ``name`` for appending, owner-only (``0o600``), never through a symlink.

        Returns the raw descriptor; an existing file's mode is tightened to
        owner-only bits.
        """
        _check_name(name)
        self._refuse_link_leaf_without_fd(name)
        # O_NONBLOCK keeps a FIFO planted at the path from hanging the open; the
        # descriptor is then required to be a regular file.
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | _O_NOFOLLOW | _O_CLOEXEC | _O_BINARY | _O_NONBLOCK
        )
        fd = os.open(self._target(name), flags, 0o600, **self._dir_kw())
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise ValueError(f"Refusing to open non-regular file: {self.path / name}")
            if hasattr(os, "fchmod"):
                os.fchmod(fd, st.st_mode & 0o600)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def write_temp(
        self,
        name: str,
        write_body: Callable[[IO[str]], Any],
        *,
        mode: Optional[int] = None,
        newline: Optional[str] = None,
    ) -> str:
        """Write a new exclusive sibling temp file for ``name`` and return its name.

        The file is created owner-only (``0o600``); ``mode`` forces other
        permission bits before any content is written. The temp file is
        removed if writing fails.
        """
        _check_name(name)
        tmp_name = f".{name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC | _O_BINARY
        fd = os.open(self._target(tmp_name), flags, 0o600, **self._dir_kw())
        try:
            if mode is not None and hasattr(os, "fchmod"):
                os.fchmod(fd, mode)
            f = os.fdopen(fd, "w", encoding="utf-8", newline=newline)
            fd = -1  # now owned by ``f``
            with f:
                write_body(f)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            if fd != -1:
                os.close(fd)
            self._discard(tmp_name)
            raise
        return tmp_name

    def write_atomic(
        self,
        name: str,
        write_body: Callable[[IO[str]], Any],
        *,
        mode: Optional[int] = None,
        newline: Optional[str] = None,
    ) -> Path:
        """Write ``name`` via an exclusive sibling temp file and an atomic replace.

        ``mode`` forces the final permission bits (e.g. ``0o600``) and is set
        before any content is written. With ``mode=None`` a new file is created
        owner-only (``0o600``) and an existing regular file keeps its permission
        bits. A symlink planted at ``name`` is replaced, never followed.
        """
        _check_name(name)
        final_mode = mode
        if final_mode is None:
            st = self.lstat(name)
            if st is not None and stat.S_ISREG(st.st_mode):
                # Permission bits only: never carry setuid/setgid/sticky over.
                final_mode = stat.S_IMODE(st.st_mode) & 0o777
        tmp_name = self.write_temp(name, write_body, mode=final_mode, newline=newline)
        try:
            self.replace(tmp_name, name)
        except BaseException:
            self._discard(tmp_name)
            raise
        return self.path / name
