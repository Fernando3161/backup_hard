"""Source-neutral verification shared by filesystem and Android runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from backup_hard.models import VerificationLevel, VerificationResult


@dataclass(frozen=True)
class VerificationItem:
    source_path: str
    destination: Path
    expected_size: int
    expected_sha256: str = ""


@dataclass
class VerificationSummary:
    results: list[VerificationResult] = field(default_factory=list)
    scan_errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.scan_errors and all(result.passed for result in self.results)
