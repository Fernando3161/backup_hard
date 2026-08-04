"""Deterministic destination grouping and conditional media splitting."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from backup_hard.models import Classification, DestinationMapping, FileCategory, SourceFile


MAX_MEDIA_FILES_PER_DIRECTORY = 100


@dataclass(frozen=True)
class PlanningItem:
    file: SourceFile
    classification: Classification


def _sort_key(item: PlanningItem) -> tuple[object, ...]:
    timestamp = item.classification.archive_timestamp
    return (
        timestamp if timestamp is not None else 2**63 - 1,
        item.file.filename.casefold(), item.file.filename,
        item.file.volume_id, item.file.source_path,
    )


def assign_media_groups(items: list[PlanningItem], maximum: int = MAX_MEDIA_FILES_PER_DIRECTORY) -> list[DestinationMapping]:
    if maximum < 1:
        raise ValueError("maximum files per directory must be positive")
    groups: dict[str, list[PlanningItem]] = defaultdict(list)
    for item in items:
        classification = item.classification
        if not classification.included or classification.category in {FileCategory.DOCUMENT, FileCategory.OTHER}:
            continue
        group = f"{classification.origin}/{classification.media_type}/{classification.archive_month}"
        groups[group].append(item)
    mappings: list[DestinationMapping] = []
    for group_key in sorted(groups, key=str.casefold):
        members = sorted(groups[group_key], key=_sort_key)
        split = len(members) > maximum
        for offset, item in enumerate(members):
            position = offset + 1
            part = offset // maximum + 1 if split else None
            directory = PurePosixPath("media") / group_key
            if part is not None:
                directory /= f"part_{part:03d}"
            mappings.append(DestinationMapping(
                item.file.source_path, directory / item.file.filename, group_key,
                position, part, 0, item.file.filename,
            ))
    return mappings
