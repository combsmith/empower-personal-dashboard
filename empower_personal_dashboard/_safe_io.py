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

Platforms without ``dir_fd`` / ``O_NOFOLLOW`` support (e.g. Windows) fall back
to the previous validated-pathname behavior instead of failing.
"""

import errno
import os
import stat
import uuid
from pathlib import Path
from typing import IO, Any, Callable, Dict, Optional, Union

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
DIR_FD_SUPPORTED = bool(_O_NOFOLLOW and _O_DIRECTORY) and all(
    fn in os.supports_dir_fd for fn in (os.open, os.mkdir, os.rename, os.unlink, os.stat)
)

# errno values an ``O_NOFOLLOW`` open reports for a symlinked component
# (ELOOP on Linux/macOS, EMLINK on FreeBSD; ENOTDIR for a non-directory).
_SYMLINK_ERRNOS = frozenset({errno.ELOOP, errno.EMLINK, errno.ENOTDIR})


def _check_name(name: str) -> None:
    """Accept only a single, plain path component."""
    if not name or name in (".", "..") or "/" in name or os.sep in name or "\x00" in name:
        raise ValueError(f"Invalid file name: {name!r}")


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
        if DIR_FD_SUPPORTED:
            self.fd = self._open_nofollow(create)
        else:
            if create:
                self.path.mkdir(parents=True, exist_ok=True)
            if self.path.is_symlink() or not self.path.is_dir():
                raise ValueError(f"Refusing to use non-directory or symlinked directory: {self.path}")

    def _open_nofollow(self, create: bool) -> int:
        flags = _O_DIR_SEARCH | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
        parts = self.path.parts
        fd = os.open(parts[0], flags)
        try:
            for name in parts[1:]:
                _check_name(name)
                try:
                    try:
                        nxt = os.open(name, flags, dir_fd=fd)
                    except FileNotFoundError:
                        if not create:
                            raise
                        try:
                            os.mkdir(name, dir_fd=fd)
                        except FileExistsError:
                            pass
                        nxt = os.open(name, flags, dir_fd=fd)
                except OSError as e:
                    if e.errno in _SYMLINK_ERRNOS:
                        raise ValueError(
                            f"Refusing to use directory with a symlinked or non-directory component: {self.path}"
                        ) from e
                    raise
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

    def _target(self, name: str) -> str:
        return name if self.fd is not None else str(self.path / name)

    def _dir_kw(self) -> Dict[str, int]:
        return {"dir_fd": self.fd} if self.fd is not None else {}

    def open_text(self, name: str, encoding: str = "utf-8") -> IO[str]:
        """Open ``name`` for reading, refusing a symlinked or non-regular file."""
        _check_name(name)
        flags = os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC | _O_BINARY | _O_NONBLOCK
        try:
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
        kw = self._dir_kw()
        final_mode = mode
        if final_mode is None:
            try:
                st = os.stat(self._target(name), follow_symlinks=False, **kw)
                if stat.S_ISREG(st.st_mode):
                    # Permission bits only: never carry setuid/setgid/sticky over.
                    final_mode = stat.S_IMODE(st.st_mode) & 0o777
            except FileNotFoundError:
                pass
        tmp_name = f".{name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC | _O_BINARY
        fd = os.open(self._target(tmp_name), flags, 0o600, **kw)
        replaced = False
        try:
            if final_mode is not None and hasattr(os, "fchmod"):
                os.fchmod(fd, final_mode)
            f = os.fdopen(fd, "w", encoding="utf-8", newline=newline)
            fd = -1  # now owned by ``f``
            with f:
                write_body(f)
                f.flush()
                os.fsync(f.fileno())
            if self.fd is not None:
                os.replace(tmp_name, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            else:
                os.replace(self._target(tmp_name), self._target(name))
            replaced = True
        finally:
            if fd != -1:
                os.close(fd)
            if not replaced:
                try:
                    os.unlink(self._target(tmp_name), **kw)
                except FileNotFoundError:
                    pass
        return self.path / name
