"""Build a reviewable backup-format policy from a Step 1 inventory."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from backup_hard.backup_run import step_directory, validate_backup_id


CATEGORIES: dict[str, set[str]] = {
    "video": {
        ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4",
        ".mpeg", ".mpg", ".mts", ".ogv", ".webm", ".wmv",
    },
    "image": {
        ".avif", ".bmp", ".emf", ".eps", ".gif", ".heic", ".heif", ".ico",
        ".jfif", ".jpeg", ".jpg", ".png", ".ppm", ".ps", ".raw", ".svg",
        ".tif", ".tiff", ".webp", ".xbm",
    },
    "audio": {
        ".aac", ".aiff", ".alac", ".flac", ".m4a", ".mid", ".midi", ".mp3",
        ".ogg", ".opus", ".pcm", ".wav", ".wma",
    },
    "document": {
        ".bib", ".csv", ".doc", ".docm", ".docx", ".dotx", ".epub", ".htm",
        ".html", ".ics", ".ldif", ".md", ".mht", ".mobi", ".msg", ".odp",
        ".ods", ".odt", ".pdf", ".pps", ".ppsx", ".ppt", ".pptm", ".pptx",
        ".prn", ".ris", ".rtf", ".rst", ".tex", ".txt", ".vcf", ".xls",
        ".xlsm", ".xlsx", ".xhtml",
    },
    "design_cad": {
        ".drawio", ".dwg", ".dxf", ".fig", ".iam", ".idw", ".ipt", ".obj",
        ".skp", ".stl", ".stp", ".step", ".vsdx", ".xmind",
    },
    "archive_backup": {
        ".7z", ".bak", ".backup", ".bz2", ".gz", ".iso", ".rar", ".tar",
        ".tgz", ".torrent", ".xz", ".zip",
    },
    "application_installer": {
        ".apk", ".appx", ".exe", ".jar", ".msi", ".msix",
    },
    "structured_web_source": {
        ".aspx", ".bat", ".c", ".cmd", ".cpp", ".cs", ".css", ".go", ".h",
        ".hpp", ".ipynb", ".java", ".js", ".json", ".kt", ".lua", ".m",
        ".php", ".pl", ".properties", ".ps1", ".py", ".r", ".sh", ".sql",
        ".ts", ".xml", ".xsd", ".xsl", ".yaml", ".yml",
    },
    "database_data": {
        ".crypt1", ".crypt12", ".crypt13", ".crypt14", ".db", ".db-shm",
        ".db-wal", ".h5", ".hdf5", ".mat", ".pickle", ".sav", ".sqlite", ".zolca",
    },
    "game_user_data": {
        ".civ6cfg", ".civ6save", ".ctv6", ".ctv6archive", ".ctv6bak", ".p2s",
        ".ps2", ".stormbank", ".stormreplay", ".stormsave",
    },
    "generic_requested": {".file", ".temp", ".tmp", "[no extension]"},
}

# Destination categories are intentionally fewer than recognition categories.
# Image and video are merged as requested; names are filesystem-safe and stable.
CATEGORY_NAMES = {
    "video": "media",
    "image": "media",
    "audio": "audio",
    "document": "document",
    "design_cad": "design_cad",
    "archive_backup": "archive_backup",
    "application_installer": "app_installer",
    "structured_web_source": "source_code",
    "database_data": "database_data",
    "game_user_data": "game_user_data",
    "generic_requested": "generic",
}

CATEGORY_LAYOUTS = {category: "preserve" for category in {*CATEGORY_NAMES.values(), "other"}}

# Explicitly out of backup scope. This overrides the include-all fallback.
EXCLUDED_EXTENSIONS = {".tmp"}

OS_CONTENT_TYPES = {name for name in ("video", "image", "audio", "document")}
OS_EXCLUDED_EXTENSIONS = {
    ".tmp", ".temp", ".log", ".bak", ".dmp", ".mdmp", ".part",
    ".partial", ".crdownload", ".cache", ".swp", ".swo",
}
OS_SIZE_THRESHOLDS = {
    "global_minimum_bytes": 1024,
    "content_type_minimum_bytes": {
        "document": 1024 * 1024,
        "audio": 10 * 1024,
        "image": 10 * 1024,
        "video": 1024 * 1024,
    },
}
OS_PATH_EXCLUSIONS = {
    "root_directory_names": [
        "windows", "program files", "program files (x86)", "program files (arm)",
        "programdata", "program", "program (x86)", "programe", "programe (x86)",
        "programm", "programm (x86)", "programme", "programme (x86)",
        "programmi", "programmi (x86)", "archivos de programa",
        "archivos de programa (x86)", "arquivos de programas",
        "arquivos de programas (x86)", "fichiers de programme",
        "fichiers de programme (x86)",
        "$recycle.bin", "system volume information", "recovery", "perflogs",
    ],
    "directory_names": [
        "temp", "tmp", "temporary files", "cache", "caches", "__pycache__",
        "steam", "steamlibrary", "gog", "gog galaxy", "ea", "ea games",
        "electronic arts", "xbox", "xboxgames", "xbox games", "epic games",
        "ubisoft", "ubisoft game launcher", "battle.net", "rockstar games",
        "elevateddiagnostics",
    ],
    "directory_name_prefixes": ["anaconda"],
    "directory_name_tokens": ["log", "logs"],
    "directory_name_substrings": ["logs"],
    "filename_prefixes": ["~$"],
    "filename_names": ["thumbs.db", "desktop.ini"],
}


def extension_category(extension: str) -> str | None:
    for category, extensions in CATEGORIES.items():
        if extension in extensions:
            return category
    return None


def destination_category(extension: str, *, include_all: bool = False) -> str | None:
    recognized = extension_category(extension)
    if recognized:
        return CATEGORY_NAMES[recognized]
    return "other" if include_all else None


def load_inventory(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_os_policy(
    rows: list[dict[str, str]], output: Path, backup_id: str,
    excluded_relative_prefixes: list[str] | None = None,
) -> None:
    """Lock the strict OS media/document policy, including per-file size gates."""
    output.mkdir(parents=True, exist_ok=True)
    selection: list[dict[str, object]] = []
    extension_categories: dict[str, str] = {}
    extension_content_types: dict[str, str] = {}
    for row in rows:
        extension = row["extension"]
        content_type = extension_category(extension)
        included = content_type in OS_CONTENT_TYPES and extension not in OS_EXCLUDED_EXTENSIONS
        category = CATEGORY_NAMES[content_type] if included and content_type else "excluded"
        if included and content_type:
            extension_categories[extension] = category
            extension_content_types[extension] = content_type
        selection.append({
            "extension": extension,
            "decision": "include" if included else "exclude",
            "category": category,
            "content_type": content_type or "unclassified",
            "minimum_size_bytes": (
                max(
                    OS_SIZE_THRESHOLDS["global_minimum_bytes"],
                    OS_SIZE_THRESHOLDS["content_type_minimum_bytes"].get(content_type, 0),
                ) if included else ""
            ),
            "layout": "preserve" if included else "excluded",
            "file_count": int(row["file_count"]),
            "total_size_bytes": int(row["total_size_bytes"]),
            "reason": "OS media/document allowlist" if included else "outside locked OS policy",
        })
    fields = list(selection[0]) if selection else [
        "extension", "decision", "category", "content_type", "minimum_size_bytes",
        "layout", "file_count", "total_size_bytes", "reason",
    ]
    with (output / "format_selection.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(selection)
    path_exclusions = {key: list(values) for key, values in OS_PATH_EXCLUSIONS.items()}
    if excluded_relative_prefixes:
        path_exclusions["relative_path_prefixes"] = excluded_relative_prefixes
    policy = {
        "schema_version": 3,
        "case": "B_os_media_documents",
        "backup_id": backup_id,
        "status": "locked",
        "extension_matching": "case-insensitive final suffix",
        "target_root_template": "<destination-root>\\backup_<backup-id>",
        "unmapped_extension_action": "exclude",
        "unmapped_extension_category": None,
        "excluded_extensions": sorted(OS_EXCLUDED_EXTENSIONS),
        "path_exclusions": path_exclusions,
        "size_thresholds": OS_SIZE_THRESHOLDS,
        "include_extensions": sorted(extension_categories),
        "category_rules": {
            category: {"destination_folder": ".", "layout": "preserve"}
            for category in {"media", "audio", "document"}
        },
        "extension_categories": extension_categories,
        "extension_content_types": extension_content_types,
    }
    (output / "format_policy.json").write_text(
        json.dumps(policy, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    included = [row for row in selection if row["decision"] == "include"]
    candidate_count = sum(int(row["file_count"]) for row in included)
    candidate_bytes = sum(int(row["total_size_bytes"]) for row in included)
    lines = [
        "# Step 2 — Locked OS media/document policy", "",
        "> Status: **LOCKED — approved for Step 3 planning**", "",
        f"- Backup ID: `{backup_id}`",
        f"- Included extensions: **{len(included):,}**",
        f"- Pre-size-filter candidates: **{candidate_count:,} files** ({candidate_bytes / 1024**3:.3f} GiB)",
        "- Global minimum: **1 KiB**",
        "- Documents: **1 MiB minimum**",
        "- Audio: **10 KiB minimum**",
        "- Images: **10 KiB minimum**",
        "- Videos: **1 MiB minimum**", "",
        "The inventory is aggregated by extension, so exact post-size and post-path totals are determined "
        "when Step 3 builds its file-level plan. Every selected file preserves its source-relative path. "
        "The locked path exclusions for OS, program, temporary, log, Anaconda, and gaming directories "
        "remain enforced.", "",
    ]
    (output / "format_selection_report.md").write_text("\n".join(lines), encoding="utf-8")


def build_selection(
    rows: list[dict[str, str]], *, include_all: bool = False
) -> list[dict[str, object]]:
    selection: list[dict[str, object]] = []
    for row in rows:
        extension = row["extension"]
        category = destination_category(extension, include_all=include_all)
        excluded = extension in EXCLUDED_EXTENSIONS
        included = category is not None and not excluded
        selection.append(
            {
                "extension": extension,
                "decision": "exclude" if excluded else "include" if included else "review",
                "category": "excluded_temp" if excluded else category or "unclassified",
                "destination_folder": "" if excluded else ".",
                "layout": "excluded" if excluded else CATEGORY_LAYOUTS[category] if category else "",
                "file_count": int(row["file_count"]),
                "total_size_bytes": int(row["total_size_bytes"]),
                "reason": (
                    "explicitly excluded temporary file"
                    if excluded
                    else "recognized content type"
                    if extension_category(extension)
                    else "included by user-approved all-format policy"
                    if include_all else "not covered by current policy"
                ),
            }
        )
    return selection


def write_outputs(
    selection: list[dict[str, object]], output: Path, *, locked: bool = False,
    backup_id: str | None = None,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    review_path = output / "format_selection.csv"
    fields = [
        "extension", "decision", "category", "destination_folder", "layout",
        "file_count", "total_size_bytes", "reason",
    ]
    with review_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(selection)

    included = [row for row in selection if row["decision"] == "include"]
    excluded = [row for row in selection if row["decision"] == "exclude"]
    review = [row for row in selection if row["decision"] == "review"]
    locked = {
        "schema_version": 3,
        "case": "A_full_disk",
        "backup_id": backup_id,
        "status": "locked" if locked else "proposed",
        "extension_matching": "case-insensitive final suffix",
        "target_root_template": "<destination-root>\\backup_<backup-id>",
        "unmapped_extension_action": "include",
        "unmapped_extension_category": "other",
        "excluded_extensions": sorted(EXCLUDED_EXTENSIONS),
        "include_extensions": sorted(str(row["extension"]) for row in included),
        "category_rules": {
            category: {"destination_folder": ".", "layout": layout}
            for category, layout in CATEGORY_LAYOUTS.items()
        },
        "extension_categories": {
            str(row["extension"]): str(row["category"]) for row in included
        },
    }
    (output / "format_policy.json").write_text(
        json.dumps(locked, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    category_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in included:
        totals = category_totals[str(row["category"])]
        totals[0] += int(row["file_count"])
        totals[1] += int(row["total_size_bytes"])

    included_files = sum(int(row["file_count"]) for row in included)
    included_bytes = sum(int(row["total_size_bytes"]) for row in included)
    excluded_files = sum(int(row["file_count"]) for row in excluded)
    excluded_bytes = sum(int(row["total_size_bytes"]) for row in excluded)
    review_files = sum(int(row["file_count"]) for row in review)
    review_bytes = sum(int(row["total_size_bytes"]) for row in review)
    lines = [
        "# Step 2 — Locked format selection" if locked else "# Step 2 — Proposed format selection",
        "",
        "> Status: **LOCKED — approved for copying**"
        if locked
        else "> Status: **PROPOSED — review required before copying**",
        "",
        f"- Included extensions: **{len(included):,}**",
        f"- Included files: **{included_files:,}**",
        f"- Included size: **{included_bytes:,} bytes** ({included_bytes / 1024**3:.3f} GiB)",
        f"- Explicitly excluded extensions: **{len(excluded):,}**",
        f"- Explicitly excluded files: **{excluded_files:,}** ({excluded_bytes:,} bytes)",
        f"- Extensions requiring review: **{len(review):,}**",
        f"- Files requiring review: **{review_files:,}**",
        f"- Size requiring review: **{review_bytes:,} bytes** ({review_bytes / 1024**3:.3f} GiB)",
        "",
        "## Included categories",
        "",
        "| Category | Layout | Files | Size (GiB) |",
        "|---|---|---:|---:|",
    ]
    for category, (count, size) in sorted(category_totals.items()):
        lines.append(
            f"| {category} | {CATEGORY_LAYOUTS[category]} | {count:,} | {size / 1024**3:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Destination layout",
            "",
            "The target root is `<destination-root>\\backup_<backup-id>`. Every included file keeps "
            "its complete path relative to the source root. Categories are reporting metadata only.",
            "Any extension first encountered after this policy was generated is included under `other`.",
            "Files with the explicitly excluded `.tmp` extension are not backed up.",
            "",
        ]
    )
    if locked:
        lines.append(
            "Every inventoried extension is locked for inclusion in Step 3; no formats remain for review."
        )
    else:
        lines.append(
            "Every inventoried extension appears in `format_selection.csv`. Rows marked "
            "`review` are not yet included and must be decided before Step 3."
        )
    lines.extend(
        [
            "The typo `wepb` from the original request is interpreted as `.webp`; `.webp` is included.",
            "",
        ]
    )
    (output / "format_selection_report.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-id", required=True, help="source_label_YYYYMMDD")
    parser.add_argument("--reports-root", type=Path, default=Path("reports"))
    parser.add_argument(
        "--lock-all",
        action="store_true",
        help="Include every inventoried extension and mark the policy locked",
    )
    parser.add_argument("--os-media-documents", action="store_true")
    parser.add_argument("--android-media-documents", action="store_true")
    parser.add_argument(
        "--exclude-relative-prefix", action="append", default=[], metavar="PATH",
        help="Source-relative directory prefix to exclude; repeat as needed",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    backup_id = validate_backup_id(args.backup_id)
    output = step_directory(args.reports_root, backup_id, 2)
    if args.android_media_documents:
        from backup_hard.android import select_main
        return select_main(args)
    inventory_path = step_directory(args.reports_root, backup_id, 1) / "file_formats.csv"
    rows = load_inventory(inventory_path)
    if args.os_media_documents:
        write_os_policy(rows, output, backup_id, args.exclude_relative_prefix)
        print(f"Locked OS media/document policy. Review: {(output / 'format_selection.csv').resolve()}")
        return 0
    selection = build_selection(rows, include_all=args.lock_all)
    write_outputs(selection, output, locked=args.lock_all, backup_id=backup_id)
    print(f"Classified {len(selection):,} extensions. Review: {(output / 'format_selection.csv').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
