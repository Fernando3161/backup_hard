"""Source-neutral immutable models shared by backup engines and adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath
from typing import BinaryIO, Iterable, Protocol


class FileCategory(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    OTHER = "other"


class CopyStatus(str, Enum):
    PLANNED = "planned"
    COPYING = "copying"
    COPIED = "copied"
    ALREADY_VERIFIED = "already_verified"
    FAILED = "failed"
    SOURCE_CHANGED = "source_changed"
    SOURCE_MISSING = "source_missing"
    DESTINATION_CONFLICT = "destination_conflict"
    INTERRUPTED = "interrupted"


class VerificationLevel(str, Enum):
    METADATA = "metadata"
    HASH = "hash"
    FULL = "full"


@dataclass(frozen=True)
class SourceIdentity:
    source_type: str
    stable_id: str
    label: str
    manufacturer: str = ""
    model: str = ""
    fingerprint: str = ""


@dataclass(frozen=True)
class SourceVolume:
    stable_id: str
    label: str
    root: str
    removable: bool = False


@dataclass(frozen=True)
class SourceFile:
    volume_id: str
    source_path: str
    relative_path: str
    filename: str
    extension: str
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True)
class Classification:
    included: bool
    category: FileCategory
    reason: str
    origin: str = ""
    media_type: str = ""
    package_name: str = ""
    origin_rule: str = ""
    archive_timestamp: int | None = None
    archive_month: str = "unknown_date"
    date_source: str = "unknown"


@dataclass(frozen=True)
class DestinationMapping:
    source_path: str
    destination_path: PurePath
    group_key: str
    position: int
    part_number: int | None
    collision_index: int
    encoded_filename: str


@dataclass(frozen=True)
class CopyResult:
    source_path: str
    destination_path: str
    status: CopyStatus
    attempts: int
    size_bytes: int
    sha256: str = ""
    detail: str = ""


@dataclass(frozen=True)
class VerificationResult:
    source_path: str
    destination_path: str
    level: VerificationLevel
    passed: bool
    issue: str = ""
    detail: str = ""


class BackupSource(Protocol):
    def identity(self) -> SourceIdentity: ...
    def list_volumes(self) -> list[SourceVolume]: ...
    def inventory(self) -> Iterable[SourceFile]: ...
    def stat(self, file: SourceFile) -> SourceFile: ...
    def open_stream(self, file: SourceFile) -> BinaryIO: ...
