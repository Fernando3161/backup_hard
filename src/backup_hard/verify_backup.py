"""Independently verify a Case A full-disk backup using fresh scans."""

from __future__ import annotations

import argparse
import csv
import json
import os
import stat as stat_module
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backup_hard.backup_run import destination_directory, step_directory, validate_backup_id
from backup_hard.copy_backup import load_minimum_sizes, load_path_exclusions, path_exclusion_reason
from backup_hard.inventory import normalized_extension


DISCREPANCY_FIELDS = [
    "scope", "category", "source_relative_path", "destination_relative_path",
    "issue", "source_size_bytes", "destination_size_bytes", "detail",
]


@dataclass(frozen=True)
class FileInfo:
    relative_path: Path
    size_bytes: int
    extension: str


@dataclass
class ScanResult:
    files: dict[str, FileInfo] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)
    skipped_links: int = 0


def scan_tree(root: Path) -> ScanResult:
    """Fresh iterative scan; keys use Windows-case-insensitive relative paths."""
    root = root.resolve()
    result = ScanResult()
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            info = Path(entry.path).stat(follow_symlinks=False)
                        attributes = getattr(info, "st_file_attributes", 0)
                        reparse_flag = getattr(info, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                        if stat_module.S_ISDIR(info.st_mode):
                            if entry.is_symlink() or bool(attributes & reparse_flag):
                                result.skipped_links += 1
                            else:
                                pending.append(Path(entry.path))
                        elif stat_module.S_ISREG(info.st_mode):
                            relative = Path(entry.path).relative_to(root)
                            result.files[str(relative).casefold()] = FileInfo(
                                relative, info.st_size, normalized_extension(entry.name)
                            )
                    except OSError as exc:
                        result.errors.append(
                            {
                                "relative_path": os.path.relpath(entry.path, root),
                                "operation": "inspect entry",
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
        except OSError as exc:
            result.errors.append(
                {
                    "relative_path": os.path.relpath(directory, root),
                    "operation": "list directory",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return result


def load_policy(path: Path) -> tuple[dict[str, str], str | None, set[str], set[str]]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("status") != "locked" or policy.get("schema_version") != 3:
        raise ValueError("Verification requires the locked Step 2 schema-version-3 policy")
    mappings = dict(policy["extension_categories"])
    default = policy.get("unmapped_extension_category")
    categories = set(policy["category_rules"])
    return mappings, default, categories, set(policy.get("excluded_extensions", []))


def load_plan(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _totals() -> dict[str, list[int]]:
    return defaultdict(lambda: [0, 0])


def verify(
    source_scan: ScanResult,
    destination_scan: ScanResult,
    plan: list[dict[str, str]],
    extension_categories: dict[str, str],
    default_category: str | None,
    known_categories: set[str],
    excluded_extensions: set[str] | None = None,
    path_exclusions: dict[str, set[str]] | None = None,
    minimum_sizes: dict[str, int] | None = None,
) -> tuple[list[dict[str, object]], dict[str, list[int]], dict[str, list[int]]]:
    excluded_extensions = excluded_extensions or set()
    path_exclusions = path_exclusions or {}
    minimum_sizes = minimum_sizes or {}
    discrepancies: list[dict[str, object]] = []
    source_totals, destination_totals = _totals(), _totals()

    for item in source_scan.files.values():
        if item.extension in excluded_extensions:
            continue
        category = extension_categories.get(item.extension, default_category)
        if (
            category is None
            or path_exclusion_reason(item.relative_path, path_exclusions)
            or item.size_bytes < minimum_sizes.get(item.extension, 0)
        ):
            continue
        source_totals[category][0] += 1
        source_totals[category][1] += item.size_bytes

    for item in destination_scan.files.values():
        if item.extension in excluded_extensions:
            continue
        category = extension_categories.get(item.extension, default_category)
        if category is None or item.size_bytes < minimum_sizes.get(item.extension, 0):
            continue
        destination_totals[category][0] += 1
        destination_totals[category][1] += item.size_bytes

    plan_sources: dict[str, dict[str, str]] = {}
    plan_destinations: dict[str, dict[str, str]] = {}
    for row in plan:
        if row["extension"] in excluded_extensions:
            continue
        source_key = str(Path(row["source_relative_path"])).casefold()
        destination_key = str(Path(row["destination_relative_path"])).casefold()
        if source_key in plan_sources or destination_key in plan_destinations:
            discrepancies.append(
                {
                    "scope": "plan", "category": row["category"],
                    "source_relative_path": row["source_relative_path"],
                    "destination_relative_path": row["destination_relative_path"],
                    "issue": "duplicate_plan_mapping", "source_size_bytes": row["size_bytes"],
                    "destination_size_bytes": "", "detail": "plan path is not unique",
                }
            )
        plan_sources[source_key] = row
        plan_destinations[destination_key] = row

    for source_key, source in source_scan.files.items():
        if source.extension in excluded_extensions:
            continue
        row = plan_sources.get(source_key)
        category = extension_categories.get(source.extension, default_category)
        if (
            category is None
            or path_exclusion_reason(source.relative_path, path_exclusions)
            or source.size_bytes < minimum_sizes.get(source.extension, 0)
        ):
            continue
        if row is None:
            discrepancies.append(
                {
                    "scope": "source", "category": category,
                    "source_relative_path": str(source.relative_path), "destination_relative_path": "",
                    "issue": "source_not_in_plan", "source_size_bytes": source.size_bytes,
                    "destination_size_bytes": "", "detail": "fresh source file is absent from copy plan",
                }
            )
        elif int(row["size_bytes"]) != source.size_bytes:
            discrepancies.append(
                {
                    "scope": "source", "category": category,
                    "source_relative_path": str(source.relative_path),
                    "destination_relative_path": row["destination_relative_path"],
                    "issue": "source_changed_since_plan", "source_size_bytes": source.size_bytes,
                    "destination_size_bytes": row["size_bytes"], "detail": "fresh source size differs from plan",
                }
            )

    for source_key, row in plan_sources.items():
        source = source_scan.files.get(source_key)
        destination_key = str(Path(row["destination_relative_path"])).casefold()
        destination = destination_scan.files.get(destination_key)
        if source is None:
            discrepancies.append(
                {
                    "scope": "source", "category": row["category"],
                    "source_relative_path": row["source_relative_path"],
                    "destination_relative_path": row["destination_relative_path"],
                    "issue": "planned_source_now_missing", "source_size_bytes": row["size_bytes"],
                    "destination_size_bytes": destination.size_bytes if destination else "",
                    "detail": "file was planned but absent from fresh source scan",
                }
            )
        if destination is None:
            discrepancies.append(
                {
                    "scope": "destination", "category": row["category"],
                    "source_relative_path": row["source_relative_path"],
                    "destination_relative_path": row["destination_relative_path"],
                    "issue": "destination_missing", "source_size_bytes": source.size_bytes if source else row["size_bytes"],
                    "destination_size_bytes": "", "detail": "planned destination file does not exist",
                }
            )
        else:
            expected_size = source.size_bytes if source else int(row["size_bytes"])
            if destination.size_bytes != expected_size:
                discrepancies.append(
                    {
                        "scope": "destination", "category": row["category"],
                        "source_relative_path": row["source_relative_path"],
                        "destination_relative_path": row["destination_relative_path"],
                        "issue": "destination_size_mismatch", "source_size_bytes": expected_size,
                        "destination_size_bytes": destination.size_bytes,
                        "detail": "destination size differs from fresh source/plan",
                    }
                )

    for destination_key, destination in destination_scan.files.items():
        if destination.extension in excluded_extensions:
            continue
        if destination_key not in plan_destinations:
            category = extension_categories.get(destination.extension, default_category) or "[outside policy]"
            discrepancies.append(
                {
                    "scope": "destination", "category": category,
                    "source_relative_path": "", "destination_relative_path": str(destination.relative_path),
                    "issue": "unexpected_destination_file", "source_size_bytes": "",
                    "destination_size_bytes": destination.size_bytes,
                    "detail": "destination file is absent from copy plan",
                }
            )
    return discrepancies, source_totals, destination_totals


def write_outputs(
    output: Path,
    discrepancies: list[dict[str, object]],
    source_totals: dict[str, list[int]],
    destination_totals: dict[str, list[int]],
    source_scan: ScanResult,
    destination_scan: ScanResult,
    started: datetime,
    finished: datetime,
    path_exclusions: dict[str, set[str]] | None = None,
) -> bool:
    output.mkdir(parents=True, exist_ok=True)
    with (output / "validation_discrepancies.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=DISCREPANCY_FIELDS)
        writer.writeheader()
        writer.writerows(discrepancies)
    with (output / "validation_scan_errors.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        fields = ["tree", "relative_path", "operation", "error"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({"tree": "source", **row} for row in source_scan.errors)
        writer.writerows({"tree": "destination", **row} for row in destination_scan.errors)

    path_exclusions = path_exclusions or {}
    critical_source_errors = [
        row for row in source_scan.errors
        if path_exclusion_reason(Path(row["relative_path"]) / "__directory_probe__", path_exclusions) is None
    ]
    validation_pass = not discrepancies and not critical_source_errors
    all_categories = sorted(set(source_totals) | set(destination_totals))
    lines = [
        "# Step 4 — Independent validation report", "",
        f"> Policy-filtered backup validation: **{'PASS' if validation_pass else 'FAIL'}**", "",
        f"- Started: `{started.isoformat(timespec='seconds')}`",
        f"- Finished: `{finished.isoformat(timespec='seconds')}`",
        f"- Duration: **{finished - started}**",
        f"- Fresh source files scanned: **{len(source_scan.files):,}**",
        f"- Fresh destination files scanned: **{len(destination_scan.files):,}**",
        f"- Total discrepancies: **{len(discrepancies):,}**",
        f"- Source scan errors: **{len(source_scan.errors):,}**",
        f"- In-scope source scan errors: **{len(critical_source_errors):,}**",
        f"- Destination scan errors: **{len(destination_scan.errors):,}**",
        "", "## Fresh category comparison", "",
        "| Category | Source files | Destination files | Source GiB | Destination GiB | Match |",
        "|---|---:|---:|---:|---:|:---:|",
    ]
    for category in all_categories:
        source_count, source_bytes = source_totals[category]
        destination_count, destination_bytes = destination_totals[category]
        match = source_count == destination_count and source_bytes == destination_bytes
        lines.append(
            f"| {category} | {source_count:,} | {destination_count:,} | "
            f"{source_bytes / 1024**3:.3f} | {destination_bytes / 1024**3:.3f} | "
            f"{'PASS' if match else 'FAIL'} |"
        )
    lines.extend(
        [
            "", "Validation uses fresh filesystem scans and does not trust Step 3 manifest statuses. "
            "Every planned destination is checked for existence and byte size. Media and document "
            "paths are checked individually using their preserved source-relative structure.",
            "Access errors under policy-excluded directories do not fail validation.", "",
        ]
    )
    (output / "validation_report.md").write_text("\n".join(lines), encoding="utf-8")
    return validation_pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--source-type", choices=("filesystem", "android"), default="filesystem")
    parser.add_argument("--device-serial", help="Exact ADB serial for Android")
    parser.add_argument("--backup-id", required=True, help="source_label_YYYYMMDD")
    parser.add_argument("--destination-root", type=Path, required=True)
    parser.add_argument("--reports-root", type=Path, default=Path("reports"))
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel local hashing threads for --source-type android (default 4)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = datetime.now(timezone.utc).astimezone()
    try:
        if args.workers < 1:
            raise ValueError("--workers must be at least 1")
        if args.source_type == "android":
            if not args.device_serial:
                raise ValueError("Android verification requires --device-serial")
            from backup_hard.android import verify_main
            return verify_main(args)
        if args.source is None:
            raise ValueError("Filesystem verification requires --source")
        backup_id = validate_backup_id(args.backup_id)
        output = step_directory(args.reports_root, backup_id, 4)
        policy_path = step_directory(args.reports_root, backup_id, 2) / "format_policy.json"
        mappings, default, categories, exclusions = load_policy(policy_path)
        path_exclusions = load_path_exclusions(policy_path)
        minimum_sizes = load_minimum_sizes(policy_path)
        plan = load_plan(step_directory(args.reports_root, backup_id, 3) / "copy_plan.csv")
        destination = destination_directory(args.destination_root, backup_id)
        print("Fresh-scanning source...", flush=True)
        source_scan = scan_tree(args.source)
        print(f"Source: {len(source_scan.files):,} files; scanning destination...", flush=True)
        destination_scan = scan_tree(destination)
        print(f"Destination: {len(destination_scan.files):,} files; comparing...", flush=True)
        discrepancies, source_totals, destination_totals = verify(
            source_scan, destination_scan, plan, mappings, default, categories, exclusions,
            path_exclusions, minimum_sizes,
        )
        finished = datetime.now(timezone.utc).astimezone()
        validation_pass = write_outputs(
            output, discrepancies, source_totals, destination_totals,
            source_scan, destination_scan, started, finished, path_exclusions,
        )
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"Validation failed to run: {exc}", file=sys.stderr)
        return 2
    print(
        f"Validation complete: {len(discrepancies):,} discrepancies; "
        f"full-disk files {'PASS' if validation_pass else 'FAIL'}. Report: {output.resolve()}"
    )
    return 0 if validation_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
