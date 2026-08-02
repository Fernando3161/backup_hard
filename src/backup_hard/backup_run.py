"""Shared identifiers and report paths for a full-disk backup run."""

from __future__ import annotations

import re
from pathlib import Path


BACKUP_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*_\d{8}$")


def validate_backup_id(value: str) -> str:
    """Validate a sortable ``source_label_YYYYMMDD`` backup identifier."""
    if not BACKUP_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "backup ID must use source_label_YYYYMMDD, for example "
            "archive_disk_20260801"
        )
    return value


def step_directory(reports_root: Path, backup_id: str, step: int) -> Path:
    if step not in {1, 2, 3, 4}:
        raise ValueError("step must be between 1 and 4")
    return reports_root / validate_backup_id(backup_id) / f"step_{step}"


def destination_directory(destination_root: Path, backup_id: str) -> Path:
    return destination_root / f"backup_{validate_backup_id(backup_id)}"
