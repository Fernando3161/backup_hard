"""Managed destination ownership and immutable run metadata."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


METADATA_DIRECTORY = ".backup-hard"
RUN_MARKER = "run.json"


class DestinationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunMarker:
    backup_id: str
    source_stable_id: str
    policy_sha256: str
    plan_sha256: str
    schema_version: int = 1


def _marker_path(destination: Path) -> Path:
    return destination / METADATA_DIRECTORY / RUN_MARKER


def initialize_destination(destination: Path, marker: RunMarker) -> Path:
    """Create a new managed destination; never adopt an existing directory."""
    if destination.exists():
        raise DestinationError(f"Destination already exists and will not be adopted: {destination}")
    metadata = destination / METADATA_DIRECTORY
    metadata.mkdir(parents=True, exist_ok=False)
    _marker_path(destination).write_text(
        json.dumps(asdict(marker), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return metadata


def require_owned_destination(destination: Path, expected: RunMarker) -> Path:
    marker_path = _marker_path(destination)
    if not destination.is_dir() or not marker_path.is_file():
        raise DestinationError("Existing destination is not owned by Backup Hard")
    try:
        actual = RunMarker(**json.loads(marker_path.read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DestinationError("Destination ownership marker is invalid") from exc
    if actual != expected:
        raise DestinationError("Destination belongs to a different run, source, policy, or plan")
    return destination / METADATA_DIRECTORY
