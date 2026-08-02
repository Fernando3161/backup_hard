"""Read-only disk inventory grouped by file extension."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backup_hard.backup_run import step_directory, validate_backup_id


NO_EXTENSION = "[no extension]"


@dataclass
class ExtensionStats:
    file_count: int = 0
    total_size_bytes: int = 0


@dataclass(frozen=True)
class ScanError:
    path: str
    operation: str
    error: str


@dataclass
class InventoryResult:
    by_extension: dict[str, ExtensionStats]
    errors: list[ScanError]
    skipped_directory_links: int = 0

    @property
    def total_files(self) -> int:
        return sum(item.file_count for item in self.by_extension.values())

    @property
    def total_size_bytes(self) -> int:
        return sum(item.total_size_bytes for item in self.by_extension.values())


def normalized_extension(filename: str) -> str:
    """Return the normalized final suffix, or a stable extensionless label."""
    suffix = Path(filename).suffix
    return suffix.lower() if suffix and suffix != "." else NO_EXTENSION


def inventory(source: Path) -> InventoryResult:
    source = source.resolve()
    if not source.is_dir():
        raise ValueError(f"Source is not an accessible directory: {source}")

    stats: dict[str, ExtensionStats] = defaultdict(ExtensionStats)
    errors: list[ScanError] = []
    result = InventoryResult(by_extension=stats, errors=errors)

    # Use a dedicated walker here so skipped directory links can be counted.
    pending = [source]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            info = entry.stat(follow_symlinks=False)
                            attributes = getattr(info, "st_file_attributes", 0)
                            reparse_flag = getattr(info, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                            if entry.is_symlink() or bool(attributes & reparse_flag):
                                result.skipped_directory_links += 1
                            else:
                                pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            size = entry.stat(follow_symlinks=False).st_size
                            item = stats[normalized_extension(entry.name)]
                            item.file_count += 1
                            item.total_size_bytes += size
                    except OSError as exc:
                        errors.append(ScanError(entry.path, "inspect entry", str(exc)))
        except OSError as exc:
            errors.append(ScanError(str(directory), "list directory", str(exc)))
    return result


def write_outputs(
    result: InventoryResult, source: Path, output: Path, backup_id: str | None = None
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    with (output / "file_formats.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["extension", "file_count", "total_size_bytes", "total_size_mib", "total_size_gib"]
        )
        for extension, item in sorted(
            result.by_extension.items(), key=lambda pair: (-pair[1].total_size_bytes, pair[0])
        ):
            writer.writerow(
                [
                    extension,
                    item.file_count,
                    item.total_size_bytes,
                    f"{item.total_size_bytes / 1024**2:.3f}",
                    f"{item.total_size_bytes / 1024**3:.6f}",
                ]
            )

    with (output / "scan_errors.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["path", "operation", "error"])
        for error in result.errors:
            writer.writerow([error.path, error.operation, error.error])

    report = (
        "# Step 1 — Source inventory\n\n"
        f"- Generated: `{generated}`\n"
        + (f"- Backup ID: `{backup_id}`\n" if backup_id else "")
        +
        f"- Source: `{source.resolve()}`\n"
        f"- Regular files inventoried: **{result.total_files:,}**\n"
        f"- Total file size: **{result.total_size_bytes:,} bytes** "
        f"({result.total_size_bytes / 1024**3:.3f} GiB)\n"
        f"- Distinct extensions: **{len(result.by_extension):,}**\n"
        f"- Scan errors: **{len(result.errors):,}**\n"
        f"- Directory links/reparse points skipped: **{result.skipped_directory_links:,}**\n\n"
        "The authoritative extension totals are in `file_formats.csv`. Review "
        "`scan_errors.csv` before locking the backup formats; a non-zero error count "
        "means the inventory may be incomplete.\n"
    )
    (output / "inventory_report.md").write_text(report, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Filesystem source directory or volume root")
    parser.add_argument("--source-type", choices=("filesystem", "android"), default="filesystem")
    parser.add_argument("--device-serial", help="Exact ADB serial (required for Android)")
    parser.add_argument("--device-label", help="Readable Android device label")
    parser.add_argument("--backup-id", required=True, help="source_label_YYYYMMDD")
    parser.add_argument("--reports-root", type=Path, default=Path("reports"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.source_type == "android":
            if not args.device_label:
                raise ValueError("Android inventory requires --device-label")
            from backup_hard.android import AndroidError, inventory_main
            return inventory_main(args)
        if args.source is None:
            raise ValueError("Filesystem inventory requires --source")
        backup_id = validate_backup_id(args.backup_id)
        output = step_directory(args.reports_root, backup_id, 1)
        result = inventory(args.source)
        write_outputs(result, args.source, output, backup_id)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Inventory failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Inventoried {result.total_files:,} files ({result.total_size_bytes:,} bytes); "
        f"{len(result.errors):,} scan errors. Reports: {output.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
