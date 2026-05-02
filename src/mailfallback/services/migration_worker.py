"""Migration worker — copy engine with progress tracking.

Provides low-level file-copy primitives used by migration_service to
relocate Maildir directories when an admin changes a user's store.
Uses only Python stdlib; no project dependencies.
"""

from __future__ import annotations

import os
import shutil


def prescan(source_path: str) -> tuple[int, int]:
    """Recursively count files and total bytes under *source_path*.

    Returns ``(file_count, total_bytes)``.  Symlinks are never followed.
    """
    file_count = 0
    total_bytes = 0

    with os.scandir(source_path) as it:
        for entry in it:
            if entry.is_dir(follow_symlinks=False):
                sub_files, sub_bytes = prescan(entry.path)
                file_count += sub_files
                total_bytes += sub_bytes
            elif entry.is_file(follow_symlinks=False):
                file_count += 1
                total_bytes += entry.stat(follow_symlinks=False).st_size

    return file_count, total_bytes


def copy_tree(
    source: str,
    dest: str,
    on_progress: callable,
    _counter: list | None = None,
) -> None:
    """Recursively copy *source* to *dest* with skip-existing semantics.

    * Creates directories with ``os.makedirs(exist_ok=True)``.
    * Copies files with ``shutil.copy2()`` (preserves metadata).
    * **Skips** files that already exist at the destination — safe for
      Maildir because mail files are immutable with unique names.
    * Calls *on_progress(copied_files, copied_bytes)* after every file
      (both copied and skipped) so callers can track cumulative progress.
    * *_counter* is an internal accumulator shared across recursive calls;
      callers should leave it as ``None``.
    """
    if _counter is None:
        _counter = [0, 0]

    os.makedirs(dest, exist_ok=True)

    with os.scandir(source) as it:
        for entry in it:
            src_path = entry.path
            dst_path = os.path.join(dest, entry.name)

            if entry.is_dir(follow_symlinks=False):
                copy_tree(src_path, dst_path, on_progress, _counter)
            elif entry.is_file(follow_symlinks=False):
                size = entry.stat(follow_symlinks=False).st_size

                if not os.path.exists(dst_path):
                    shutil.copy2(src_path, dst_path, follow_symlinks=False)

                _counter[0] += 1
                _counter[1] += size
                on_progress(_counter[0], _counter[1])


def verify_copy(source: str, dest: str) -> tuple[bool, str]:
    """Compare file counts between *source* and *dest*.

    Returns ``(True, "OK")`` when counts match, otherwise
    ``(False, "File count mismatch: source=X, dest=Y")``.
    """
    src_files, _ = prescan(source)
    dst_files, _ = prescan(dest)

    if src_files == dst_files:
        return True, "OK"

    return False, f"File count mismatch: source={src_files}, dest={dst_files}"
