"""Source-neutral policy contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backup_hard.models import Classification, SourceFile


@dataclass(frozen=True)
class PolicyContext:
    source_type: str


class BackupPolicy(Protocol):
    name: str

    def classify(self, file: SourceFile, context: PolicyContext) -> Classification: ...
