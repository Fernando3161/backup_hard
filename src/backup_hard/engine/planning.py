"""Deterministic destination grouping and conditional media splitting."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from backup_hard.models import Classification, DestinationMapping, FileCategory, SourceFile
from backup_hard.paths import destination_collision_key, encode_windows_name


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


def _suffix_name(filename: str, index: int) -> str:
    if index == 0:
        return filename
    suffix = PurePosixPath(filename).suffix
    stem = filename[:-len(suffix)] if suffix else filename
    return f"{stem}_{index}{suffix}"


def plan_destinations(items: list[PlanningItem], maximum: int = MAX_MEDIA_FILES_PER_DIRECTORY) -> list[DestinationMapping]:
    preliminary = assign_media_groups(items, maximum)
    for item in sorted(items, key=_sort_key):
        if not item.classification.included or item.classification.category != FileCategory.DOCUMENT:
            continue
        folder = item.file.extension.casefold().lstrip(".") or "no_extension"
        preliminary.append(DestinationMapping(
            item.file.source_path, PurePosixPath("documents") / folder / item.file.filename,
            f"documents/{folder}", 0, None, 0, item.file.filename,
        ))
    preliminary.sort(key=lambda mapping: (mapping.group_key.casefold(), mapping.position, mapping.source_path))
    used: set[str] = set()
    result: list[DestinationMapping] = []
    for mapping in preliminary:
        original_filename = mapping.destination_path.name
        encoded = encode_windows_name(original_filename)
        directory = mapping.destination_path.parent
        collision_index = 0
        while True:
            candidate = directory / _suffix_name(encoded.encoded, collision_index)
            key = destination_collision_key(str(candidate))
            if key not in used:
                used.add(key)
                break
            collision_index += 1
        result.append(DestinationMapping(
            mapping.source_path, candidate, mapping.group_key, mapping.position,
            mapping.part_number, collision_index, encoded.encoded,
        ))
    return result
