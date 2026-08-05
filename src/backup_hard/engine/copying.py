"""Bounded-memory, atomic, source-neutral copy primitives."""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from backup_hard.manifest import Manifest
from backup_hard.models import CopyResult, CopyStatus


DEFAULT_BLOCK_SIZE = 1024 * 1024


class CopyError(RuntimeError):
    pass


class DestinationConflict(CopyError):
    pass


def cleanup_owned_partials(directory: Path, run_id: str) -> int:
    """Remove only temporary files created by this run."""
    removed = 0
    if not directory.exists():
        return removed
    markers = (f".backup-hard-{_run_token(run_id)}-", f".backup-hard-{run_id}-")
    for path in directory.rglob("*.partial"):
        if any(marker in path.name for marker in markers) and path.is_file():
            path.unlink()
            removed += 1
    return removed


def _run_token(run_id: str) -> str:
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class StreamCopyOutcome:
    size_bytes: int
    sha256: str


def remaining_bytes(planned_sizes: dict[str, int], completed: set[str]) -> int:
    return sum(size for path, size in planned_sizes.items() if path not in completed)


def require_free_space(destination: Path, required_bytes: int) -> None:
    available = shutil.disk_usage(destination).free
    if available < required_bytes:
        raise CopyError(f"Insufficient free space: need {required_bytes}, have {available}")


def copy_and_record(source_path: str, source: BinaryIO, destination: Path,
                    expected_size: int, run_id: str, manifest: Manifest,
                    attempt: int = 1) -> CopyResult:
    """Persist attempt and outcome independently so interruption is resumable."""
    manifest.begin_copy_attempt(source_path, attempt)
    outcome = stream_to_destination(source, destination, expected_size, run_id)
    result = CopyResult(source_path, str(destination), CopyStatus.COPIED, attempt,
                        outcome.size_bytes, outcome.sha256, "")
    manifest.record_copy_result(result)
    return result


def stream_to_destination(
    source: BinaryIO,
    destination: Path,
    expected_size: int,
    run_id: str,
    block_size: int = DEFAULT_BLOCK_SIZE,
) -> StreamCopyOutcome:
    """Stream, hash, fsync, and atomically promote without overwriting."""
    if block_size < 1:
        raise ValueError("block size must be positive")
    if destination.exists():
        raise DestinationConflict(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(
        f".backup-hard-{_run_token(run_id)}-{uuid.uuid4().hex[:16]}.partial"
    )
    digest = hashlib.sha256()
    count = 0
    try:
        with partial.open("xb") as output:
            while True:
                block = source.read(block_size)
                if not block:
                    break
                output.write(block)
                digest.update(block)
                count += len(block)
            output.flush()
            os.fsync(output.fileno())
        if count != expected_size:
            raise CopyError(f"Source stream size changed: expected {expected_size}, received {count}")
        if destination.exists():
            raise DestinationConflict("Destination appeared during copy")
        os.replace(partial, destination)
        return StreamCopyOutcome(count, digest.hexdigest())
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
