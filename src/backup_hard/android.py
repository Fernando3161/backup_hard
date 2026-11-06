"""Safe Android shared-storage inventory, planning, copying, and verification."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Sequence

from backup_hard.backup_run import step_directory, validate_backup_id


MEDIA_EXTENSIONS = {
    "image": {".avif", ".bmp", ".dng", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".raw", ".tif", ".tiff", ".webp"},
    "video": {".3gp", ".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".webm"},
    "audio": {".aac", ".aiff", ".alac", ".amr", ".flac", ".m4a", ".mid", ".midi", ".mp3", ".ogg", ".opus", ".wav", ".wma"},
}
DOCUMENT_EXTENSIONS = {
    ".bib", ".csv", ".doc", ".docm", ".docx", ".epub", ".html", ".ics",
    ".md", ".mobi", ".odp", ".ods", ".odt", ".pdf", ".pps", ".ppsx",
    ".ppt", ".pptm", ".pptx", ".rtf", ".rst", ".tex", ".txt", ".vcf",
    ".xls", ".xlsm", ".xlsx",
}
GLOBAL_MINIMUM = 1024
CATEGORY_MINIMUMS = {"image": 10 * 1024, "audio": 2 * 1024, "video": 1024 * 1024, "document": GLOBAL_MINIMUM}
EXCLUDED_DIRECTORY_NAMES = {
    ".cache", ".thumbnails", ".trash", ".trashed", "cache", "caches", "code_cache",
    "temp", "tmp", "thumbnails", "trash", "recycle", "recyclebin", "$recycle.bin",
}
EXCLUDED_FILE_SUFFIXES = {".tmp", ".temp", ".part", ".partial", ".crdownload", ".download"}
EXCLUDED_PATH_PREFIXES = {"android/data", "android/obb"}
STREAMING_MARKERS = {
    "com.spotify.music/files/spotifycache", "com.netflix.mediaclient", "offline_cache",
    "streaming_cache", "videochunks",
}
INVENTORY_FIELDS = [
    "serial", "device_label", "manufacturer", "model", "volume", "source_root",
    "source_path", "relative_path", "filename", "extension", "category", "size_bytes",
    "mtime", "status", "exclusion_reason",
]
PLAN_FIELDS = [
    "serial", "device_label", "volume", "source_root", "source_path", "relative_path",
    "destination_relative_path", "filename", "extension", "category", "size_bytes", "mtime",
    "media_bucket", "collision_index", "collision_reason",
]
SOCIAL_MEDIA_MARKERS = {
    "whatsapp", "whassap", "facebook", "instagram", "twitter", "telegram",
    "reddit", "linkedin", "messenger", "snapchat", "tiktok", "signal",
    "discord", "threads", "pinterest", "tumblr", "wechat",
    "com.whatsapp", "com.facebook", "com.instagram", "org.telegram",
    "com.reddit", "com.linkedin", "com.twitter",
}


class AndroidError(RuntimeError):
    """An actionable Android transport or policy error."""


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    details: dict[str, str]


@dataclass(frozen=True)
class Volume:
    label: str
    root: str
    identifier: str


class Adb:
    """Mockable, argument-vector-only ADB command layer."""

    def __init__(self, executable: str = "adb") -> None:
        self.executable = executable

    def run(self, args: Sequence[str], *, binary: bool = False, check: bool = True) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [self.executable, *args], check=check, capture_output=True,
                text=not binary, encoding=None if binary else "utf-8",
                errors=None if binary else "replace", shell=False,
            )
        except FileNotFoundError as exc:
            raise AndroidError("adb was not found. Install Android SDK Platform Tools and add adb to PATH.") from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
            raise AndroidError(f"ADB command failed: {(stderr or '').strip() or 'no diagnostic returned'}") from exc

    def devices(self) -> list[Device]:
        result = self.run(["devices", "-l"])
        devices: list[Device] = []
        for line in result.stdout.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 2:
                continue
            details = dict(token.split(":", 1) for token in fields[2:] if ":" in token)
            devices.append(Device(fields[0], fields[1], details))
        return devices

    def shell(self, serial: str, *args: str, binary: bool = False, check: bool = True) -> subprocess.CompletedProcess:
        # `adb shell` concatenates argv into a remote shell command. Quote every
        # token explicitly so metacharacters and user filenames remain data.
        command = " ".join(shlex.quote(arg) for arg in args)
        return self.run(["-s", serial, "shell", command], binary=binary, check=check)

    def pull(self, serial: str, source: str, destination: Path) -> None:
        self.run(["-s", serial, "pull", source, str(destination)])

    def stream(self, serial: str, source: str) -> bytes:
        return self.run(["-s", serial, "exec-out", "cat", "--", source], binary=True).stdout


def select_device(adb: Adb, serial: str | None) -> Device:
    devices = adb.devices()
    if serial:
        matches = [device for device in devices if device.serial == serial]
        if not matches:
            raise AndroidError(f"Device {serial!r} is disconnected or not visible to adb.")
        selected = matches[0]
    else:
        usable = [device for device in devices if device.state == "device"]
        if not devices:
            raise AndroidError("No Android device detected. Connect it and enable USB debugging.")
        if len(usable) != 1:
            raise AndroidError("Multiple or no ready devices found; pass --device-serial explicitly.")
        selected = usable[0]
    if selected.state == "unauthorized":
        raise AndroidError("Device is unauthorized. Unlock it and accept the USB-debugging authorization prompt.")
    if selected.state == "offline":
        raise AndroidError("Device is offline. Reconnect it or restart ADB, then authorize it again.")
    if selected.state != "device":
        raise AndroidError(f"Device {selected.serial!r} is not ready (state: {selected.state}).")
    return selected


def _prop(adb: Adb, serial: str, name: str) -> str:
    return adb.shell(serial, "getprop", name).stdout.strip()


def discover_volumes(adb: Adb, serial: str) -> tuple[list[Volume], list[dict[str, str]]]:
    candidates = [("internal", "/storage/emulated/0", "emulated")]
    errors: list[dict[str, str]] = []
    # Some devices restrict `sm`, so use a non-checking probe and retain internal storage.
    raw = adb.run(["-s", serial, "shell", "sm", "list-volumes", "public"], check=False)
    if raw.returncode == 0:
        uuids = sorted({match.group(1) for line in raw.stdout.splitlines() if (match := re.search(r"mounted\s+([0-9A-Fa-f-]{4,})", line))})
        for uuid_value in uuids:
            candidates.append(("sd_card", f"/storage/{uuid_value}", uuid_value))
    seen: set[str] = set()
    volumes: list[Volume] = []
    sd_count = sum(1 for label, _, _ in candidates if label == "sd_card")
    for label, root, identifier in candidates:
        probe = adb.run(["-s", serial, "shell", "readlink", "-f", root], check=False)
        canonical = probe.stdout.strip() if probe.returncode == 0 else root
        if canonical in seen:
            continue
        access = adb.run(["-s", serial, "shell", "test", "-r", root], check=False)
        if access.returncode:
            errors.append({"path": root, "operation": "probe volume", "error": "shared-storage volume is inaccessible"})
            continue
        seen.add(canonical)
        readable = label if label == "internal" or sd_count == 1 else f"sd_card_{re.sub('[^0-9a-z]', '', identifier.lower())[:8]}"
        volumes.append(Volume(readable, root, identifier))
    if not volumes:
        raise AndroidError("No accessible shared-storage volume was found on the selected device.")
    return volumes, errors


def classify(extension: str) -> str | None:
    for category, extensions in MEDIA_EXTENSIONS.items():
        if extension in extensions:
            return category
    return "document" if extension in DOCUMENT_EXTENSIONS else None


def exclusion_reason(relative_path: str, extension: str, category: str | None, size: int) -> str | None:
    normalized = relative_path.replace("\\", "/").strip("/").casefold()
    parts = normalized.split("/")
    if any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in EXCLUDED_PATH_PREFIXES):
        return "private application package or expansion data"
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in parts[:-1]):
        return "cache, temporary, thumbnail, trash, or recycle directory"
    if any(marker in normalized for marker in STREAMING_MARKERS):
        return "known streaming or offline cache"
    if extension in EXCLUDED_FILE_SUFFIXES:
        return "temporary or incomplete download"
    if category is None:
        return "unsupported file type"
    threshold = max(GLOBAL_MINIMUM, CATEGORY_MINIMUMS[category])
    if size < threshold:
        return f"below {category} minimum of {threshold} bytes"
    return None


def _list_paths(adb: Adb, serial: str, root: str) -> tuple[list[str], list[dict[str, str]]]:
    """List files under root, tolerating unreadable subdirectories.

    Android 11+ denies ADB access to other apps' folders under Android/data and
    Android/obb. `find` then exits non-zero but still prints everything it could
    read, so keep that output and report each denied path separately.
    """
    result = adb.shell(serial, "find", root, "-type", "f", "-print0", binary=True, check=False)
    paths = [item.decode("utf-8", errors="surrogateescape") for item in (result.stdout or b"").split(b"\0") if item]
    stderr = result.stderr or b""
    stderr = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr
    errors: list[dict[str, str]] = []
    unexpected: list[str] = []
    for line in stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("find: ") and line.endswith("Permission denied"):
            denied = line[len("find: "):].rsplit(":", 1)[0].strip()
            errors.append({"path": denied, "operation": "scan directory", "error": "Permission denied (skipped)"})
        else:
            unexpected.append(line)
    if result.returncode and (unexpected or not paths):
        raise AndroidError(f"ADB command failed: {'; '.join(unexpected) or 'no diagnostic returned'}")
    return paths, errors


def _stat(adb: Adb, serial: str, path: str) -> tuple[int, int]:
    result = adb.shell(serial, "stat", "-c", "%s|%Y", "--", path)
    size, mtime = result.stdout.strip().split("|", 1)
    return int(size), int(mtime)


STAT_BATCH_FORMAT = "%s|%Y|%n"
STAT_BATCH_SIZE = 500


def _stat_many(adb: Adb, serial: str, root: str) -> dict[str, tuple[int, int]]:
    """Return {path: (size, mtime)} for every readable file under root in one ADB call.

    One `find | xargs stat` pipeline on the device replaces one ADB round trip
    per file. Output is newline-delimited
    with the name last, so `|` in names is safe, and a trailing CR is dropped. A name containing a newline
    cannot be parsed reliably; such paths, and any name seen twice, are left out
    so the caller falls back to a per-file stat for them.
    """
    # Toybox `find -exec ... {} +` overflows the argument limit on large trees
    # ("Argument list too long"), so chunk through xargs instead. The root is
    # passed as $1, never spliced into the script.
    script = f'find "$1" -type f -print0 | xargs -0 -n {STAT_BATCH_SIZE} stat -c "{STAT_BATCH_FORMAT}"'
    result = adb.shell(serial, "sh", "-c", script, "sh", root, binary=True, check=False)
    stats: dict[str, tuple[int, int]] = {}
    ambiguous: set[str] = set()
    for line in (result.stdout or b"").split(b"\n"):
        # Real devices were observed returning CRLF line endings over adb shell.
        parts = line.rstrip(b"\r").split(b"|", 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].lstrip(b"-").isdigit() or not parts[2]:
            continue
        name = parts[2].decode("utf-8", errors="surrogateescape")
        if name in stats:
            ambiguous.add(name)
        stats[name] = (int(parts[0]), int(parts[1]))
    for name in ambiguous:
        del stats[name]
    return stats


def inventory_device(adb: Adb, serial: str | None, label: str) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, str]]]:
    device = select_device(adb, serial)
    serial = device.serial
    volumes, errors = discover_volumes(adb, serial)
    manufacturer = _prop(adb, serial, "ro.product.manufacturer")
    model = _prop(adb, serial, "ro.product.model")
    rows: list[dict[str, object]] = []
    for volume in volumes:
        try:
            paths, scan_errors = _list_paths(adb, serial, volume.root)
            errors.extend(scan_errors)
        except AndroidError as exc:
            errors.append({"path": volume.root, "operation": "scan volume", "error": str(exc)})
            continue
        try:
            batch_stats = _stat_many(adb, serial, volume.root)
        except AndroidError:
            batch_stats = {}
        for source_path in sorted(paths, key=str.casefold):
            relative = source_path[len(volume.root):].lstrip("/")
            extension = PurePosixPath(relative).suffix.casefold()
            try:
                cached = batch_stats.get(source_path)
                size, mtime = cached if cached is not None else _stat(adb, serial, source_path)
                category = classify(extension)
                reason = exclusion_reason(relative, extension, category, size)
                status = "excluded" if reason else "included"
            except (AndroidError, ValueError) as exc:
                size, mtime, category, status, reason = 0, 0, classify(extension), "error", f"inspection failed: {exc}"
                errors.append({"path": source_path, "operation": "stat file", "error": str(exc)})
            rows.append({
                "serial": serial, "device_label": label, "manufacturer": manufacturer, "model": model,
                "volume": volume.label, "source_root": volume.root, "source_path": source_path,
                "relative_path": relative, "filename": PurePosixPath(relative).name,
                "extension": extension, "category": category or "unknown", "size_bytes": size,
                "mtime": mtime, "status": status, "exclusion_reason": reason or "",
            })
    metadata = {"schema_version": 1, "source_type": "android", "serial": serial, "device_label": label,
                "manufacturer": manufacturer, "model": model, "volumes": [volume.__dict__ for volume in volumes]}
    return metadata, rows, errors


def _write_csv(path: Path, fields: Sequence[str], rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_inventory(output: Path, metadata: dict[str, object], rows: list[dict[str, object]], errors: list[dict[str, str]]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "android_inventory.csv", INVENTORY_FIELDS, rows)
    _write_csv(output / "scan_errors.csv", ["path", "operation", "error"], errors)
    totals: dict[str, list[int]] = {}
    for row in rows:
        extension = str(row["extension"] or "[no extension]")
        item = totals.setdefault(extension, [0, 0])
        item[0] += 1
        item[1] += int(row["size_bytes"])
    _write_csv(output / "file_formats.csv", ["extension", "file_count", "total_size_bytes", "total_size_mib", "total_size_gib"], [
        {"extension": ext, "file_count": values[0], "total_size_bytes": values[1], "total_size_mib": f"{values[1]/1024**2:.3f}", "total_size_gib": f"{values[1]/1024**3:.6f}"}
        for ext, values in sorted(totals.items(), key=lambda pair: (-pair[1][1], pair[0]))
    ])
    (output / "device.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    included = [row for row in rows if row["status"] == "included"]
    excluded = [row for row in rows if row["status"] == "excluded"]
    lines = ["# Step 1 — Android shared-storage inventory", "", f"- Device label: `{metadata['device_label']}`",
             f"- Manufacturer/model: `{metadata['manufacturer']} {metadata['model']}`", f"- Volumes: **{len(metadata['volumes'])}**",
             f"- Included: **{len(included):,} files** ({sum(int(r['size_bytes']) for r in included):,} bytes)",
             f"- Excluded: **{len(excluded):,} files** ({sum(int(r['size_bytes']) for r in excluded):,} bytes)",
             f"- Errors/inaccessible paths: **{len(errors):,}**", "", "Review `android_inventory.csv` and `scan_errors.csv` before locking Step 2.", ""]
    (output / "inventory_report.md").write_text("\n".join(lines), encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _collision_name(filename: str, index: int) -> str:
    suffix = PurePosixPath(filename).suffix
    stem = filename[:-len(suffix)] if suffix else filename
    return filename if index == 0 else f"{stem}_{index}{suffix}"


def android_media_bucket(relative_path: str, category: str) -> str:
    """Return the fixed Android-only media destination directory."""
    normalized = relative_path.replace("\\", "/").casefold()
    parts = [part for part in normalized.split("/") if part]
    filename = parts[-1] if parts else ""
    if category == "audio":
        return "media/audio"
    is_social = any(marker in normalized for marker in SOCIAL_MEDIA_MARKERS)
    is_camera = "dcim" in parts or "camera" in parts
    if category == "video":
        subgroup = "social_media" if is_social else "camera" if is_camera else "other"
        return f"media/video/{subgroup}"
    if category == "image":
        is_screenshot = "screenshot" in filename or "screenshots" in parts
        subgroup = (
            "screenshots" if is_screenshot else
            "social_media" if is_social else
            "camera" if is_camera else "other"
        )
        return f"media/photos/{subgroup}"
    raise ValueError(f"Unsupported Android media category: {category}")


def create_plan(step1: Path, output: Path, backup_id: str) -> list[dict[str, object]]:
    if (output / "android_plan.csv").exists() or (output / "android_policy.json").exists():
        raise AndroidError("Android Step 2 is already locked; use a new backup ID to change the plan.")
    metadata = json.loads((step1 / "device.json").read_text(encoding="utf-8"))
    rows = [row for row in read_csv(step1 / "android_inventory.csv") if row["status"] == "included"]
    used: dict[str, int] = Counter()
    plan: list[dict[str, object]] = []
    for row in sorted(rows, key=lambda value: (value["volume"].casefold(), value["relative_path"].casefold(), value["source_path"])):
        if row["category"] == "document":
            folder = row["extension"].lstrip(".") or "no_extension"
            key = f"{folder}/{row['filename']}".casefold()
            index = used[key]
            used[key] += 1
            destination = f"documents/{folder}/{_collision_name(row['filename'], index)}"
            reason = "" if index == 0 else f"duplicate filename renamed with deterministic _{index} suffix"
            media_bucket = ""
        else:
            media_bucket = android_media_bucket(row["relative_path"], row["category"])
            key = f"{media_bucket}/{row['filename']}".casefold()
            index = used[key]
            used[key] += 1
            destination = f"{media_bucket}/{_collision_name(row['filename'], index)}"
            reason = "" if index == 0 else f"duplicate filename renamed with deterministic _{index} suffix"
        plan.append({**row, "destination_relative_path": destination,
                     "media_bucket": media_bucket, "collision_index": index,
                     "collision_reason": reason})
    output.mkdir(parents=True, exist_ok=False)
    _write_csv(output / "android_plan.csv", PLAN_FIELDS, plan)
    policy = {"schema_version": 3, "source_type": "android", "status": "locked", "backup_id": backup_id,
              "device": metadata, "media_extensions": {k: sorted(v) for k, v in MEDIA_EXTENSIONS.items()},
              "document_extensions": sorted(DOCUMENT_EXTENSIONS), "global_minimum_bytes": GLOBAL_MINIMUM,
              "category_minimum_bytes": CATEGORY_MINIMUMS, "excluded_directory_names": sorted(EXCLUDED_DIRECTORY_NAMES),
              "excluded_path_prefixes": sorted(EXCLUDED_PATH_PREFIXES), "streaming_markers": sorted(STREAMING_MARKERS),
              "media_layout": {
                  "audio": ["media/audio"],
                  "video": ["media/video/camera", "media/video/social_media", "media/video/other"],
                  "photos": ["media/photos/camera", "media/photos/social_media", "media/photos/screenshots", "media/photos/other"],
              },
              "social_media_markers": sorted(SOCIAL_MEDIA_MARKERS),
              "file_collision_strategy": "deterministic numeric suffix before extension",
              "expected_file_count": len(plan), "expected_bytes": sum(int(row["size_bytes"]) for row in plan)}
    (output / "android_policy.json").write_text(json.dumps(policy, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "format_policy.json").write_text(json.dumps(policy, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_csv(output / "format_selection.csv", ["extension", "category", "minimum_size_bytes"], [
        {"extension": ext, "category": category, "minimum_size_bytes": max(GLOBAL_MINIMUM, CATEGORY_MINIMUMS[category])}
        for category, extensions in {**MEDIA_EXTENSIONS, "document": DOCUMENT_EXTENSIONS}.items() for ext in sorted(extensions)
    ])
    (output / "format_selection_report.md").write_text(
        f"# Step 2 — Locked Android copy plan\n\n> Status: **LOCKED**\n\n- Files: **{len(plan):,}**\n- Bytes: **{policy['expected_bytes']:,}**\n- Media is flattened into the fixed audio, video, and photo category directories.\n- Duplicate filenames use deterministic numeric suffixes.\n- Documents remain flattened by extension.\n",
        encoding="utf-8",
    )
    return plan


def destination_directory_android(destination_root: Path, backup_id: str) -> Path:
    return destination_root / validate_backup_id(backup_id)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_source(adb: Adb, serial: str, path: str) -> str:
    return hashlib.sha256(adb.stream(serial, path)).hexdigest()


def copy_plan(adb: Adb, serial: str, plan: list[dict[str, str]], destination: Path, output: Path, retries: int) -> tuple[list[dict[str, object]], bool]:
    if retries < 1:
        raise ValueError("retries must be at least 1")
    destination.parent.mkdir(parents=True, exist_ok=True)
    for folder in ("media", "documents", "metadata"):
        (destination / folder).mkdir(parents=True, exist_ok=True)
    required = sum(int(row["size_bytes"]) for row in plan)
    if shutil.disk_usage(destination.parent).free < required:
        raise AndroidError(f"Insufficient destination space for {required:,} planned bytes.")
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "copy_manifest.csv"
    previous = {row["source_path"]: row for row in read_csv(manifest_path)} if manifest_path.exists() else {}
    results: list[dict[str, object]] = []
    interrupted = False
    try:
        for row in plan:
            target = destination / Path(row["destination_relative_path"])
            old = previous.get(row["source_path"])
            if old and old.get("status") in {"copied", "already_complete"} and target.is_file() and target.stat().st_size == int(row["size_bytes"]) and sha256_file(target) == old.get("sha256"):
                results.append({**row, "status": "already_complete", "attempts": 0, "sha256": old["sha256"], "reason": "manifest and destination hash match"})
                continue
            if target.exists():
                results.append({**row, "status": "failed", "attempts": 0, "sha256": "", "reason": "pre-existing destination was not proven to be this source; not overwritten"})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            last_error = ""
            for attempt in range(1, retries + 1):
                partial = target.with_name(f".{target.name}.backup-hard-{uuid.uuid4().hex}.partial")
                try:
                    adb.pull(serial, row["source_path"], partial)
                    if partial.stat().st_size != int(row["size_bytes"]):
                        raise AndroidError("temporary copy size differs from locked plan")
                    source_hash = sha256_source(adb, serial, row["source_path"])
                    if sha256_file(partial) != source_hash:
                        raise AndroidError("temporary copy SHA-256 differs from source")
                    if target.exists():
                        raise AndroidError("destination appeared during copy; refusing overwrite")
                    os.replace(partial, target)
                    results.append({**row, "status": "copied", "attempts": attempt, "sha256": source_hash, "reason": ""})
                    break
                except (AndroidError, OSError) as exc:
                    last_error = str(exc)
                    try:
                        partial.unlink(missing_ok=True)
                    except OSError:
                        pass
            else:
                results.append({**row, "status": "failed", "attempts": retries, "sha256": "", "reason": last_error})
    except KeyboardInterrupt:
        interrupted = True
    fields = PLAN_FIELDS + ["status", "attempts", "sha256", "reason"]
    _write_csv(manifest_path, fields, results)
    _write_csv(output / "copy_failures.csv", fields, [row for row in results if row["status"] == "failed"])
    _write_csv(output / "copy_plan.csv", PLAN_FIELDS, plan)
    counts = Counter(str(row["status"]) for row in results)
    (output / "copy_report.md").write_text(
        "# Step 3 — Android copy report\n\n" + "\n".join(f"- {key}: **{value:,}**" for key, value in sorted(counts.items())) + "\n",
        encoding="utf-8",
    )
    return results, interrupted


def verify_plan(adb: Adb, serial: str, plan: list[dict[str, str]], destination: Path, output: Path,
                fresh_rows: list[dict[str, object]] | None = None,
                scan_errors: list[dict[str, str]] | None = None) -> list[dict[str, object]]:
    discrepancies: list[dict[str, object]] = []
    scan_errors = scan_errors or []
    planned_sources = {row["source_path"] for row in plan}
    if fresh_rows is not None:
        for fresh in fresh_rows:
            if fresh["status"] == "included" and fresh["source_path"] not in planned_sources:
                discrepancies.append({**fresh, "destination_relative_path": "", "collision_index": "",
                                      "collision_reason": "", "issue": "source_not_in_locked_plan",
                                      "detail": "fresh in-policy source file appeared after inventory"})
    for error in scan_errors:
        discrepancies.append({"source_path": error["path"], "destination_relative_path": "",
                              "issue": "source_scan_error", "detail": f"{error['operation']}: {error['error']}"})
    planned_destinations = {Path(row["destination_relative_path"]) for row in plan}
    for row in plan:
        target = destination / Path(row["destination_relative_path"])
        try:
            current_size, current_mtime = _stat(adb, serial, row["source_path"])
        except (AndroidError, ValueError) as exc:
            discrepancies.append({**row, "issue": "source_inaccessible", "detail": str(exc)})
            continue
        if current_size != int(row["size_bytes"]) or current_mtime != int(row["mtime"]):
            discrepancies.append({**row, "issue": "source_changed_since_inventory", "detail": f"now size={current_size}, mtime={current_mtime}"})
        if not target.is_file():
            discrepancies.append({**row, "issue": "destination_missing", "detail": "planned destination is absent"})
            continue
        if target.stat().st_size != current_size:
            discrepancies.append({**row, "issue": "destination_size_mismatch", "detail": f"destination size={target.stat().st_size}"})
            continue
        try:
            if sha256_source(adb, serial, row["source_path"]) != sha256_file(target):
                discrepancies.append({**row, "issue": "content_hash_mismatch", "detail": "source and destination SHA-256 differ"})
        except (AndroidError, OSError) as exc:
            discrepancies.append({**row, "issue": "hash_failed", "detail": str(exc)})
    if destination.exists():
        for path in destination.rglob("*"):
            if path.is_file() and path.relative_to(destination) not in planned_destinations:
                discrepancies.append({"source_path": "", "destination_relative_path": str(path.relative_to(destination)), "issue": "unexpected_destination_file", "detail": "file is absent from locked plan"})
    output.mkdir(parents=True, exist_ok=True)
    fields = PLAN_FIELDS + ["issue", "detail"]
    _write_csv(output / "validation_discrepancies.csv", fields, discrepancies)
    _write_csv(output / "validation_scan_errors.csv", ["path", "operation", "error"], scan_errors)
    passed = not discrepancies
    (output / "validation_report.md").write_text(
        f"# Step 4 — Independent Android verification\n\n> Status: **{'PASS' if passed else 'FAIL'}**\n\n- Planned files: **{len(plan):,}**\n- Discrepancies: **{len(discrepancies):,}**\n- Content verification: **SHA-256 streamed fresh from the device and hashed locally**\n",
        encoding="utf-8",
    )
    return discrepancies


def inventory_main(args: object) -> int:
    backup_id = validate_backup_id(args.backup_id)
    metadata, rows, errors = inventory_device(Adb(), args.device_serial, args.device_label)
    write_inventory(step_directory(args.reports_root, backup_id, 1), metadata, rows, errors)
    return 1 if errors else 0


def select_main(args: object) -> int:
    backup_id = validate_backup_id(args.backup_id)
    create_plan(step_directory(args.reports_root, backup_id, 1), step_directory(args.reports_root, backup_id, 2), backup_id)
    return 0


def copy_main(args: object) -> int:
    backup_id = validate_backup_id(args.backup_id)
    policy = json.loads((step_directory(args.reports_root, backup_id, 2) / "android_policy.json").read_text(encoding="utf-8"))
    if policy["device"]["serial"] != args.device_serial:
        raise AndroidError("Selected serial differs from the device locked in Step 2.")
    plan = read_csv(step_directory(args.reports_root, backup_id, 2) / "android_plan.csv")
    _, interrupted = copy_plan(Adb(), args.device_serial, plan, destination_directory_android(args.destination_root, backup_id), step_directory(args.reports_root, backup_id, 3), args.retries)
    return 130 if interrupted else 0


def verify_main(args: object) -> int:
    backup_id = validate_backup_id(args.backup_id)
    policy = json.loads((step_directory(args.reports_root, backup_id, 2) / "android_policy.json").read_text(encoding="utf-8"))
    if policy["device"]["serial"] != args.device_serial:
        raise AndroidError("Selected serial differs from the device locked in Step 2.")
    adb = Adb()
    select_device(adb, args.device_serial)
    plan = read_csv(step_directory(args.reports_root, backup_id, 2) / "android_plan.csv")
    _, fresh_rows, scan_errors = inventory_device(adb, args.device_serial, policy["device"]["device_label"])
    discrepancies = verify_plan(adb, args.device_serial, plan, destination_directory_android(args.destination_root, backup_id), step_directory(args.reports_root, backup_id, 4), fresh_rows, scan_errors)
    return 1 if discrepancies else 0
