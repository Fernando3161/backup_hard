"""Plan and execute a resumable, fully reported Case A full-disk backup."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import stat as stat_module
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backup_hard.backup_run import destination_directory, step_directory, validate_backup_id
from backup_hard.inventory import normalized_extension


PLAN_FIELDS = [
    "source_relative_path", "destination_relative_path", "extension", "category",
    "layout", "collision_index", "size_bytes", "source_mtime_ns",
]
MANIFEST_FIELDS = PLAN_FIELDS + ["status", "attempts", "reason"]


@dataclass(frozen=True)
class CategoryRule:
    destination_folder: str
    layout: str


@dataclass
class PlannedFile:
    source_relative_path: Path
    extension: str
    category: str
    layout: str
    size_bytes: int
    source_mtime_ns: int
    destination_relative_path: Path | None = None
    collision_index: int = 0

    def as_row(self) -> dict[str, object]:
        if self.destination_relative_path is None:
            raise ValueError("Plan entry has no destination")
        return {
            "source_relative_path": str(self.source_relative_path),
            "destination_relative_path": str(self.destination_relative_path),
            "extension": self.extension,
            "category": self.category,
            "layout": self.layout,
            "collision_index": self.collision_index,
            "size_bytes": self.size_bytes,
            "source_mtime_ns": self.source_mtime_ns,
        }


@dataclass(frozen=True)
class UnresolvedEntry:
    source_relative_path: str
    status: str
    reason: str
    size_bytes: int = 0

    def as_row(self) -> dict[str, object]:
        return {
            "source_relative_path": self.source_relative_path,
            "destination_relative_path": "",
            "extension": "[directory]" if self.status == "directory_unreadable" else "",
            "category": "unresolved",
            "layout": "",
            "collision_index": "",
            "size_bytes": self.size_bytes,
            "source_mtime_ns": "",
            "status": self.status,
            "attempts": 0,
            "reason": self.reason,
        }


@dataclass
class BackupPlan:
    files: list[PlannedFile]
    unresolved: list[UnresolvedEntry]
    skipped_directory_links: int = 0

    @property
    def total_bytes(self) -> int:
        return sum(item.size_bytes for item in self.files)


@dataclass
class CopyTotals:
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    bytes: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    categories: dict[str, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    unresolved: list[dict[str, object]] = field(default_factory=list)


def load_policy(
    policy_path: Path,
) -> tuple[dict[str, str], dict[str, CategoryRule], str | None, set[str]]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if policy.get("status") != "locked" or policy.get("schema_version") != 3:
        raise ValueError("Step 2 policy must be locked schema version 3")
    extension_categories = dict(policy.get("extension_categories", {}))
    rules = {
        name: CategoryRule(value["destination_folder"], value["layout"])
        for name, value in policy.get("category_rules", {}).items()
    }
    unmapped_action = policy.get("unmapped_extension_action")
    default_category = policy.get("unmapped_extension_category")
    if unmapped_action not in {"include", "exclude"}:
        raise ValueError("Policy has an invalid unmapped extension action")
    if unmapped_action == "include" and not default_category:
        raise ValueError("Policy must name a category for unmapped extensions")
    if default_category is not None and default_category not in rules:
        raise ValueError("Default category has no category rule")
    if not extension_categories or any(category not in rules for category in extension_categories.values()):
        raise ValueError("Policy contains missing or invalid extension category mappings")
    if any(rule.layout != "preserve" for rule in rules.values()):
        raise ValueError("Case A requires source-relative paths to be preserved")
    return extension_categories, rules, default_category, set(policy.get("excluded_extensions", []))


def load_path_exclusions(policy_path: Path) -> dict[str, set[str]]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    return {
        key: {str(value).casefold() for value in values}
        for key, values in policy.get("path_exclusions", {}).items()
    }


def load_minimum_sizes(policy_path: Path) -> dict[str, int]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    thresholds = policy.get("size_thresholds", {})
    global_minimum = int(thresholds.get("global_minimum_bytes", 0))
    by_type = {
        str(name): int(value)
        for name, value in thresholds.get("content_type_minimum_bytes", {}).items()
    }
    content_types = policy.get("extension_content_types", {})
    return {
        extension: max(global_minimum, by_type.get(content_type, 0))
        for extension, content_type in content_types.items()
    }


def path_exclusion_reason(relative_path: Path, rules: dict[str, set[str]]) -> str | None:
    parts = [part.casefold() for part in relative_path.parts]
    if not parts:
        return None
    filename = parts[-1]
    if filename in rules.get("filename_names", set()):
        return "excluded temporary/system filename"
    if any(filename.startswith(prefix) for prefix in rules.get("filename_prefixes", set())):
        return "excluded temporary filename prefix"
    if parts[0] in rules.get("root_directory_names", set()):
        return "excluded root directory"
    for part in parts[:-1]:
        if part in rules.get("directory_names", set()):
            return "excluded directory name"
        if any(part.startswith(prefix) for prefix in rules.get("directory_name_prefixes", set())):
            return "excluded directory prefix"
        tokens = {token for token in re.split(r"[^a-z0-9]+", part) if token}
        if tokens & rules.get("directory_name_tokens", set()):
            return "excluded directory token"
        if any(value in part for value in rules.get("directory_name_substrings", set())):
            return "excluded directory substring"
    return None


def _safe_stat(entry: os.DirEntry[str]) -> os.stat_result:
    try:
        return entry.stat(follow_symlinks=False)
    except OSError:
        return Path(entry.path).stat(follow_symlinks=False)


def _collision_name(filename: str, index: int) -> str:
    if index == 0:
        return filename
    suffix = Path(filename).suffix
    stem = filename[: -len(suffix)] if suffix else filename
    return f"{stem}_{index}{suffix}"


def build_plan(
    source: Path,
    extension_categories: dict[str, str],
    rules: dict[str, CategoryRule],
    default_category: str | None,
    excluded_extensions: set[str] | None = None,
    path_exclusions: dict[str, set[str]] | None = None,
    minimum_sizes: dict[str, int] | None = None,
) -> BackupPlan:
    """Scan source and deterministically assign every readable regular file."""
    source = source.resolve()
    excluded_extensions = excluded_extensions or set()
    path_exclusions = path_exclusions or {}
    minimum_sizes = minimum_sizes or {}
    pending = [source]
    files: list[PlannedFile] = []
    unresolved: list[UnresolvedEntry] = []
    skipped_links = 0
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        info = _safe_stat(entry)
                        attributes = getattr(info, "st_file_attributes", 0)
                        reparse_flag = getattr(info, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                        if stat_module.S_ISDIR(info.st_mode):
                            if entry.is_symlink() or bool(attributes & reparse_flag):
                                skipped_links += 1
                            else:
                                relative = Path(entry.path).relative_to(source)
                                probe = relative / "__directory_probe__"
                                if path_exclusion_reason(probe, path_exclusions) is None:
                                    pending.append(Path(entry.path))
                        elif stat_module.S_ISREG(info.st_mode):
                            relative = Path(entry.path).relative_to(source)
                            extension = normalized_extension(entry.name)
                            if (
                                extension in excluded_extensions
                                or path_exclusion_reason(relative, path_exclusions)
                            ):
                                continue
                            category = extension_categories.get(extension, default_category)
                            if category is None:
                                continue
                            if info.st_size < minimum_sizes.get(extension, 0):
                                continue
                            rule = rules[category]
                            files.append(
                                PlannedFile(
                                    source_relative_path=relative,
                                    extension=extension,
                                    category=category,
                                    layout=rule.layout,
                                    size_bytes=info.st_size,
                                    source_mtime_ns=info.st_mtime_ns,
                                )
                            )
                    except OSError as exc:
                        relative = os.path.relpath(entry.path, source)
                        unresolved.append(
                            UnresolvedEntry(
                                relative,
                                "source_unreadable",
                                f"source inspection failed: {type(exc).__name__}: {exc}",
                            )
                        )
        except OSError as exc:
            unresolved.append(
                UnresolvedEntry(
                    os.path.relpath(directory, source),
                    "directory_unreadable",
                    f"directory listing failed: {type(exc).__name__}: {exc}",
                )
            )

    files.sort(key=lambda item: str(item.source_relative_path).casefold())
    for item in files:
        item.destination_relative_path = item.source_relative_path
    return BackupPlan(files, unresolved, skipped_links)


def write_plan(plan: BackupPlan, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=PLAN_FIELDS)
        writer.writeheader()
        writer.writerows(item.as_row() for item in plan.files)


def validate_roots(source: Path, destination: Path, required_bytes: int) -> tuple[Path, Path]:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_dir():
        raise ValueError(f"Source is not an accessible directory: {source}")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Source and destination must be separate, non-nested trees")
    if not destination.parent.exists():
        raise ValueError(f"Destination parent is unavailable: {destination.parent}")
    free_bytes = shutil.disk_usage(destination.parent).free
    if free_bytes < required_bytes:
        raise ValueError(f"Insufficient space: need {required_bytes:,}, have {free_bytes:,} bytes")
    return source, destination


def extended_path(path: Path) -> Path:
    # abspath is lexical and does not open the path. Path.resolve() calls the
    # Windows API and can itself fail for protected files before copy retries run.
    absolute = os.path.abspath(path)
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def same_metadata(source_stat: os.stat_result, destination: Path) -> bool:
    try:
        target_stat = destination.stat()
    except OSError:
        return False
    return target_stat.st_size == source_stat.st_size and target_stat.st_mtime_ns == source_stat.st_mtime_ns


def copy_one(source: Path, destination: Path, retries: int) -> tuple[str, int, str]:
    source_io, destination_io = extended_path(source), extended_path(destination)
    last_error = ""
    for attempt in range(1, retries + 1):
        partial = destination_io.with_name(
            f".{destination_io.name}.backup-hard-{uuid.uuid4().hex}.partial"
        )
        try:
            before = source_io.stat()
            if destination_io.is_file() and same_metadata(before, destination_io):
                return "already_present", attempt, "same size and modification time"
            destination_io.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_io, partial)
            after, partial_stat = source_io.stat(), partial.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise OSError("source changed during copy")
            if partial_stat.st_size != after.st_size:
                raise OSError(f"partial size mismatch: expected {after.st_size}, got {partial_stat.st_size}")
            replaced = destination_io.exists()
            os.replace(partial, destination_io)
            return ("replaced" if replaced else "copied"), attempt, ""
        except OSError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            try:
                partial.unlink(missing_ok=True)
            except OSError as cleanup_error:
                last_error += f"; partial cleanup failed: {cleanup_error}"
            if attempt < retries:
                time.sleep(min(2 ** (attempt - 1), 5))
    return "failed", retries, last_error


class ProgressBar:
    def __init__(self, total_files: int, total_bytes: int) -> None:
        self.total_files = total_files
        self.total_bytes = total_bytes
        self.started = time.monotonic()
        self.last_update = 0.0

    def update(self, completed: int, completed_bytes: int, failures: int, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_update < 0.5:
            return
        ratio = completed / self.total_files if self.total_files else 1.0
        width, filled = 32, int(32 * ratio)
        bar = "#" * filled + "-" * (width - filled)
        elapsed = now - self.started
        rate = completed / elapsed if elapsed else 0.0
        remaining = (self.total_files - completed) / rate if rate else 0.0
        line = (
            f"\r[{bar}] {ratio:6.2%}  {completed:,}/{self.total_files:,} files  "
            f"{completed_bytes / 1024**3:,.2f}/{self.total_bytes / 1024**3:,.2f} GiB  "
            f"errors:{failures:,}  ETA:{remaining / 60:,.1f}m"
        )
        print(line, end="\n" if force else "", flush=True)
        self.last_update = now


def execute_plan(
    plan: BackupPlan, source: Path, destination: Path, reports: Path, retries: int
) -> tuple[CopyTotals, bool]:
    totals, interrupted = CopyTotals(), False
    totals.unresolved.extend(item.as_row() for item in plan.unresolved)
    for item in plan.unresolved:
        totals.counts[item.status] += 1
    progress = ProgressBar(len(plan.files), plan.total_bytes)
    manifest_path = reports / "copy_manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        try:
            for completed, item in enumerate(plan.files, start=1):
                row = item.as_row()
                status, attempts, reason = copy_one(
                    source / item.source_relative_path,
                    destination / item.destination_relative_path,
                    retries,
                )
                row.update({"status": status, "attempts": attempts, "reason": reason})
                writer.writerow(row)
                totals.counts[status] += 1
                totals.bytes[status] += item.size_bytes
                if status in {"copied", "replaced", "already_present"}:
                    totals.categories[item.category][0] += 1
                    totals.categories[item.category][1] += item.size_bytes
                else:
                    totals.unresolved.append(row)
                if completed % 250 == 0:
                    handle.flush()
                successful_bytes = sum(
                    totals.bytes[key] for key in ("copied", "replaced", "already_present")
                )
                progress.update(completed, successful_bytes, len(totals.unresolved))
        except KeyboardInterrupt:
            interrupted = True
            print("\nCopy interrupted; rerunning with the same plan is safe.", file=sys.stderr)
    progress.update(
        sum(totals.counts[key] for key in ("copied", "replaced", "already_present", "failed")),
        sum(totals.bytes[key] for key in ("copied", "replaced", "already_present")),
        len(totals.unresolved),
        force=True,
    )
    with (reports / "copy_failures.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(totals.unresolved)
    return totals, interrupted


def write_report(
    plan: BackupPlan,
    totals: CopyTotals,
    path: Path,
    source: Path,
    destination: Path,
    started: datetime,
    finished: datetime,
    interrupted: bool,
) -> None:
    successful = sum(totals.counts[key] for key in ("copied", "replaced", "already_present"))
    successful_bytes = sum(totals.bytes[key] for key in ("copied", "replaced", "already_present"))
    status = "INTERRUPTED" if interrupted else "COMPLETED WITH UNRESOLVED ENTRIES" if totals.unresolved else "COMPLETED"
    lines = [
        "# Step 3 — Backup copy report", "", f"> Status: **{status}**", "",
        f"- Source: `{source}`", f"- Destination: `{destination}`",
        f"- Started: `{started.isoformat(timespec='seconds')}`",
        f"- Finished: `{finished.isoformat(timespec='seconds')}`",
        f"- Duration: **{finished - started}**",
        f"- Planned readable files: **{len(plan.files):,}**",
        f"- Successfully present files: **{successful:,}**",
        f"- Successfully present size: **{successful_bytes:,} bytes** ({successful_bytes / 1024**3:.3f} GiB)",
        f"- Newly copied: **{totals.counts['copied']:,}**",
        f"- Already present: **{totals.counts['already_present']:,}**",
        f"- Copy failures after retries: **{totals.counts['failed']:,}**",
        f"- Unreadable files: **{totals.counts['source_unreadable']:,}**",
        f"- Unreadable directories: **{totals.counts['directory_unreadable']:,}**",
        f"- Directory links/reparse points skipped: **{plan.skipped_directory_links:,}**",
        "", "## Successfully present by category", "",
        "| Category | Files | Size (GiB) |", "|---|---:|---:|",
    ]
    for category, (count, size) in sorted(totals.categories.items()):
        lines.append(f"| {category} | {count:,} | {size / 1024**3:.3f} |")
    lines.extend(
        [
            "", "`copy_plan.csv` records every deterministic source-to-destination mapping. "
            "`copy_manifest.csv` records every copy result, and `copy_failures.csv` records unresolved entries.",
            "Step 4 independently scans the destination and validates it against the plan.", "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--source-type", choices=("filesystem", "android"), default="filesystem")
    parser.add_argument("--device-serial", help="Exact ADB serial for Android")
    parser.add_argument("--backup-id", required=True, help="source_label_YYYYMMDD")
    parser.add_argument("--destination-root", type=Path, required=True)
    parser.add_argument("--reports-root", type=Path, default=Path("reports"))
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel ADB pulls for --source-type android (default 4)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.retries < 1:
        print("Backup failed: --retries must be at least 1", file=sys.stderr)
        return 2
    if args.workers < 1:
        print("Backup failed: --workers must be at least 1", file=sys.stderr)
        return 2
    try:
        if args.source_type == "android":
            if not args.device_serial:
                raise ValueError("Android copy requires --device-serial")
            from backup_hard.android import copy_main
            return copy_main(args)
        if args.source is None:
            raise ValueError("Filesystem copy requires --source")
        backup_id = validate_backup_id(args.backup_id)
        reports = step_directory(args.reports_root, backup_id, 3)
        policy = step_directory(args.reports_root, backup_id, 2) / "format_policy.json"
        destination_arg = destination_directory(args.destination_root, backup_id)
        extension_categories, rules, default_category, excluded_extensions = load_policy(policy)
        path_exclusions = load_path_exclusions(policy)
        minimum_sizes = load_minimum_sizes(policy)
        source = args.source.resolve()
        print("Planning policy-filtered backup...", flush=True)
        plan = build_plan(
            source, extension_categories, rules, default_category, excluded_extensions,
            path_exclusions, minimum_sizes,
        )
        source, destination = validate_roots(source, destination_arg, plan.total_bytes)
        reports.mkdir(parents=True, exist_ok=True)
        write_plan(plan, reports / "copy_plan.csv")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Backup preflight failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"Plan: {len(plan.files):,} readable files, {plan.total_bytes / 1024**3:.3f} GiB, "
        f"{len(plan.unresolved):,} unresolved scan entries. Starting copy...",
        flush=True,
    )
    started = datetime.now(timezone.utc).astimezone()
    totals, interrupted = execute_plan(plan, source, destination, reports, args.retries)
    finished = datetime.now(timezone.utc).astimezone()
    write_report(plan, totals, reports / "copy_report.md", source, destination, started, finished, interrupted)
    print(f"Report: {(reports / 'copy_report.md').resolve()}")
    return 130 if interrupted else (1 if totals.unresolved else 0)


if __name__ == "__main__":
    raise SystemExit(main())
