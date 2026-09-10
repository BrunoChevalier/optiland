"""Bounded streaming GLC discovery; also runs as an isolated helper process.

This module deliberately uses only the standard library so the GUI can launch
the file directly without importing the optical engine in its scan worker.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

SEARCH_TIMEOUT_SECONDS = 10.0
MAX_EXAMINED_ENTRIES = 100_000
MAX_FOUND_CATALOGS = 1_000
MAX_SEARCH_DEPTH = 64
RESULT_BATCH_SIZE = 32


@dataclass(frozen=True)
class SearchLimits:
    """Resource bounds per explicit directory search, independent of GLC syntax."""

    timeout: float = SEARCH_TIMEOUT_SECONDS
    entries: int = MAX_EXAMINED_ENTRIES
    catalogs: int = MAX_FOUND_CATALOGS
    depth: int = MAX_SEARCH_DEPTH
    batch: int = RESULT_BATCH_SIZE

    def __post_init__(self) -> None:
        if not 0 < self.timeout < float("inf"):
            raise ValueError("Search timeout must be finite and positive")
        if any(
            type(v) is not int or v < 1
            for v in (self.entries, self.catalogs, self.batch)
        ):
            raise ValueError("Search bounds must be positive integers")
        if type(self.depth) is not int or self.depth < 0:
            raise ValueError("Search depth must be a nonnegative integer")


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def discover_catalogs(
    root: str | Path,
    *,
    limits: SearchLimits | None = None,
    clock: Callable[[], float] = time.monotonic,
    cancelled: Callable[[], bool] = lambda: False,
) -> Iterator[dict]:
    """Yield path batches then an explicit terminal outcome.

    No symlink/reparse directories are followed. Enumeration handles close even
    on generator cancellation. Timeout bounds cooperative calls; the GUI also
    enforces a deadline by killing the helper if a filesystem call blocks.
    """
    limits = limits or SearchLimits()
    root = Path(os.path.abspath(root))
    deadline = clock() + limits.timeout
    stack: list[tuple[os.ScandirIterator, int]] = []
    seen_dirs: set[tuple[int, int] | str] = set()
    seen_files: set[tuple[int, int] | str] = set()
    batch: list[str] = []
    examined = found = skipped = 0
    reason = ""

    def stopped() -> str:
        if cancelled():
            return "Search cancelled"
        if clock() >= deadline:
            return "Time limit reached"
        return ""

    try:
        info = root.lstat()
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            reason = "Selected directory is a link, reparse point or not a directory"
        else:
            seen_dirs.add(
                (info.st_dev, info.st_ino)
                if info.st_ino
                else os.path.normcase(str(root))
            )
            stack.append((os.scandir(root), 0))
        while stack and not reason:
            reason = stopped()
            if reason:
                break
            iterator, depth = stack[-1]
            try:
                entry = next(iterator)
            except StopIteration:
                iterator.close()
                stack.pop()
                continue
            except OSError:
                skipped += 1
                iterator.close()
                stack.pop()
                continue
            if examined >= limits.entries:
                reason = "Entry limit reached"
                break
            examined += 1
            try:
                info = entry.stat(follow_symlinks=False)
                if _is_link(info):
                    continue
                # Windows DirEntry.stat() may omit inode/device identity. A
                # full stat is needed to suppress hardlinks and directory cycles.
                if not info.st_ino and (
                    stat.S_ISDIR(info.st_mode) or entry.name.casefold().endswith(".glc")
                ):
                    info = os.stat(entry.path, follow_symlinks=False)
                    if _is_link(info):
                        continue
                identity = (
                    (info.st_dev, info.st_ino)
                    if info.st_ino
                    else os.path.normcase(entry.path)
                )
                if stat.S_ISDIR(info.st_mode):
                    if depth >= limits.depth:
                        reason = "Depth limit reached"
                        break
                    if identity in seen_dirs:
                        continue
                    seen_dirs.add(identity)
                    stack.append((os.scandir(entry.path), depth + 1))
                elif stat.S_ISREG(info.st_mode) and entry.name.casefold().endswith(
                    ".glc"
                ):
                    # inode 0 means unavailable on some filesystems.
                    key = identity
                    if key in seen_files:
                        continue
                    seen_files.add(key)
                    batch.append(entry.path)
                    found += 1
                    if found >= limits.catalogs:
                        reason = "Catalog limit reached"
            except OSError:
                skipped += 1
            reason = reason or stopped()
            if len(batch) >= limits.batch:
                yield {"paths": sorted(batch, key=lambda p: (p.casefold(), p))}
                batch = []
    except OSError:
        skipped += 1
        reason = "Selected directory could not be read"
    finally:
        for iterator, _ in stack:
            iterator.close()
    if batch:
        yield {"paths": sorted(batch, key=lambda p: (p.casefold(), p))}
    yield {
        "done": True,
        "complete": not bool(reason) and skipped == 0,
        "reason": reason or ("Some entries could not be read" if skipped else ""),
        "skipped": skipped,
        "examined": examined,
        "found": found,
    }


def main() -> None:
    """Write bounded JSON batches for the parent GUI process."""
    root, raw_limits = sys.argv[1:3]
    limits = SearchLimits(**json.loads(raw_limits))
    for message in discover_catalogs(root, limits=limits):
        print(json.dumps(message), flush=True)


if __name__ == "__main__":
    main()
