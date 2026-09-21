"""Small, local safety primitives. No model, retrieval or ranking policy lives here."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache, wraps
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading


class DataChangedError(RuntimeError):
    """A source changed while a result was being constructed."""


def file_signature(path: str | Path) -> tuple:
    """Include SQLite WAL changes and same-path atomic replacement, not just mtime.

    This is a change detector, not proof that two independently published databases
    belong to the same business revision. Publishers must still finish both updates.
    """
    path = Path(path).resolve()
    # SQLite may chmod its WAL when opening a reader, changing ctime without
    # changing data. WAL identity/size/mtime track writes without false refreshes.
    values = []
    for item in (path, Path(str(path) + '-wal')):
        try:
            stat = item.stat()
        except FileNotFoundError:
            values.append(None)
        else:
            values.append((stat.st_dev, stat.st_ino, stat.st_size,
                           stat.st_mtime_ns, stat.st_ctime_ns if item == path else None))
    return (str(path), *values)


def revision_cached(reader):
    """Memoize a path reader by the current file revision; preserve cache_clear()."""
    @lru_cache(maxsize=2)
    def cached(path: str, revision: tuple):
        result = reader(path)
        if file_signature(path) != revision:
            raise DataChangedError('商品数据正在更新，请在更新完成后重试。')
        return result

    @wraps(reader)
    def load(path: str):
        resolved = str(Path(path).resolve())
        for _ in range(2):
            revision = file_signature(resolved)
            if revision[1] is None:
                raise FileNotFoundError(resolved)
            try:
                result = cached(resolved, revision)
            except DataChangedError:
                continue
            if file_signature(resolved) == revision:
                return result
        raise DataChangedError('商品数据持续变化，本次未发布混合版本结果。')

    load.cache_clear = cached.cache_clear
    load.cache_info = cached.cache_info
    return load


def atomic_json(path: str | Path, value) -> None:
    """Readers see the previous complete JSON or the next complete JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(encoded + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def json_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def stock_snapshot(path: str | Path) -> dict | None:
    """Provenance of the bytes read, not an invented inventory business timestamp."""
    path = Path(path)
    before = file_signature(path)
    if before[1] is None:
        return None
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    if file_signature(path) != before:
        raise DataChangedError('库存文件正在更新，本次未使用混合库存。')
    return {
        'filename': path.name,
        'sha256': digest.hexdigest(),
        'file_modified_at': datetime.fromtimestamp(before[1][3] / 1e9, timezone.utc).isoformat(),
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'time_basis': 'source_file_mtime_not_business_asof',
    }


def probability(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    value = float(value)
    return value if math.isfinite(value) and 0 <= value <= 1 else None


class StoreLease:
    """Non-blocking per-store OS lock. Process exit releases it, including crashes.

    Intended for the existing local Windows/Linux filesystem deployment. This is
    not a distributed lock for multiple hosts or filesystems with no lock support.
    """
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._guard = threading.Lock()
        self._file = path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self._file.seek(0, os.SEEK_END)
                if self._file.tell() == 0:
                    self._file.write(b'\0')
                    self._file.flush()
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file.close()
            self._file = None
            raise ValueError('该店铺已有分析任务，请等待完成或停止后再试。') from exc

    def close(self):
        with self._guard:
            handle, self._file = self._file, None
            if handle is None:
                return
            try:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
