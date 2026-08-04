"""Bounded-memory, atomic, source-neutral copy primitives."""

from __future__ import annotations

import hashlib
import os
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
    marker = f".backup-hard-{run_id}-"
    for path in directory.rglob("*.partial"):
        if marker in path.name and path.is_file():
            path.unlink()
            removed += 1
    return removed


@dataclass(frozen=True)
class StreamCopyOutcome:
    size_bytes: int
    sha256: str


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
        f".{destination.name}.backup-hard-{run_id}-{uuid.uuid4().hex}.partial"
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
