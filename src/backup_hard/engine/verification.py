"""Source-neutral verification shared by filesystem and Android runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib

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


def _sha256(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_destination(item: VerificationItem, level: VerificationLevel) -> VerificationResult:
    try:
        size = item.destination.stat().st_size
    except OSError as exc:
        return VerificationResult(item.source_path, str(item.destination), level, False,
                                  "destination_access_error", str(exc))
    if size != item.expected_size:
        return VerificationResult(item.source_path, str(item.destination), level, False,
                                  "size_mismatch", f"expected {item.expected_size}, got {size}")
    if level in (VerificationLevel.HASH, VerificationLevel.FULL):
        actual = _sha256(item.destination)
        if not item.expected_sha256 or actual != item.expected_sha256:
            return VerificationResult(item.source_path, str(item.destination), level, False,
                                      "hash_mismatch", actual)
    return VerificationResult(item.source_path, str(item.destination), level, True)


def verify_items(items: list[VerificationItem], level: VerificationLevel,
                 source_scan_errors: list[str] | None = None) -> VerificationSummary:
    summary = VerificationSummary(scan_errors=list(source_scan_errors or []))
    for item in items:
        try:
            summary.results.append(verify_destination(item, level))
        except OSError as exc:
            summary.scan_errors.append(f"{item.destination}: {exc}")
    return summary
