"""Safe Android shared-storage inventory, planning, copying, and verification."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shlex
import sqlite3
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Sequence

from backup_hard.backup_run import step_directory, validate_backup_id
from backup_hard.destination import RunMarker, initialize_destination, require_owned_destination
from backup_hard.engine.copying import (
    DestinationConflict,
    cleanup_owned_partials,
    copy_and_record,
    remaining_bytes,
    require_free_space,
)
from backup_hard.engine.planning import PlanningItem, plan_destinations
from backup_hard.manifest import Manifest
from backup_hard.models import (
    CopyResult,
    CopyStatus,
    FileCategory,
    SourceFile,
    SourceIdentity,
    SourceVolume,
)
from backup_hard.policies.android_media_documents import AndroidMediaDocumentsPolicy
from backup_hard.policies.base import PolicyContext
from backup_hard.sources.android.inventory import inventory_source


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
    "origin", "media_type", "package_name", "origin_rule", "archive_timestamp",
    "archive_month", "date_source", "destination_group", "position", "part_number",
    "encoded_filename", "media_bucket", "collision_index", "collision_reason",
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

    def shell(self, serial: str, *args: str, binary: bool = False) -> subprocess.CompletedProcess:
        # `adb shell` concatenates argv into a remote shell command. Quote every
        # token explicitly so metacharacters and user filenames remain data.
        command = " ".join(shlex.quote(arg) for arg in args)
        return self.run(["-s", serial, "shell", command], binary=binary)

    def pull(self, serial: str, source: str, destination: Path) -> None:
        self.run(["-s", serial, "pull", source, str(destination)])

    def stream(self, serial: str, source: str) -> bytes:
        return self.run(["-s", serial, "exec-out", "cat", "--", source], binary=True).stdout

    def open_stream(self, serial: str, source: str) -> "AdbStream":
        command = " ".join(shlex.quote(arg) for arg in ("cat", "--", source))
        try:
            process = subprocess.Popen(
                [self.executable, "-s", serial, "exec-out", command],
                shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise AndroidError("adb was not found. Install Android SDK Platform Tools and add adb to PATH.") from exc
        return AdbStream(process)

    def open_shell(self, serial: str, arguments: Sequence[str]) -> subprocess.Popen[bytes]:
        command = " ".join(shlex.quote(argument) for argument in arguments)
        try:
            return subprocess.Popen(
                [self.executable, "-s", serial, "shell", command], shell=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise AndroidError("adb was not found. Install Android SDK Platform Tools and add adb to PATH.") from exc


class AdbStream:
    """Bounded ADB stdout reader that reports transport failure at EOF."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdout is None or process.stderr is None:
            process.kill()
            raise AndroidError("ADB stream pipes are unavailable")
        self.process = process
        self.stdout = process.stdout
        self.stderr = process.stderr
        self.checked = False

    def read(self, size: int = -1) -> bytes:
        block = self.stdout.read(size)
        if not block and not self.checked:
            self.checked = True
            returncode = self.process.wait()
            detail = self.stderr.read().decode("utf-8", errors="replace").strip()
            if returncode:
                raise AndroidError(detail or f"ADB stream exited with status {returncode}")
        return block

    def close(self) -> None:
        self.stdout.close()
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait()
        self.stderr.close()

    def __enter__(self) -> "AdbStream":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


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


def _list_paths(adb: Adb, serial: str, root: str) -> list[str]:
    result = adb.shell(serial, "find", root, "-type", "f", "-print0", binary=True)
    return [item.decode("utf-8", errors="surrogateescape") for item in result.stdout.split(b"\0") if item]


def _stat(adb: Adb, serial: str, path: str) -> tuple[int, int]:
    result = adb.shell(serial, "stat", "-c", "%s|%Y", "--", path)
    size, mtime = result.stdout.strip().split("|", 1)
    return int(size), int(mtime)


def inventory_device(adb: Adb, serial: str | None, label: str) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, str]]]:
    device = select_device(adb, serial)
    serial = device.serial
    volumes, errors = discover_volumes(adb, serial)
    manufacturer = _prop(adb, serial, "ro.product.manufacturer")
    model = _prop(adb, serial, "ro.product.model")
    fingerprint = _prop(adb, serial, "ro.build.fingerprint")
    rows: list[dict[str, object]] = []
    source_volumes = [SourceVolume(volume.identifier, volume.label, volume.root, volume.label != "internal")
                      for volume in volumes]
    files, batch_errors = inventory_source(adb, serial, source_volumes)
    errors.extend({"path": "", "operation": "batched inventory", "error": detail}
                  for detail in batch_errors)
    roots = {volume.identifier: volume.root for volume in volumes}
    labels = {volume.identifier: volume.label for volume in volumes}
    for file in sorted(files, key=lambda value: (value.volume_id, value.source_path.casefold(), value.source_path)):
        category = classify(file.extension)
        reason = exclusion_reason(file.relative_path, file.extension, category, file.size_bytes)
        rows.append({
            "serial": serial, "device_label": label, "manufacturer": manufacturer, "model": model,
            "volume": labels[file.volume_id], "source_root": roots[file.volume_id],
            "source_path": file.source_path, "relative_path": file.relative_path,
            "filename": file.filename, "extension": file.extension,
            "category": category or "unknown", "size_bytes": file.size_bytes,
            "mtime": file.mtime_ns // 1_000_000_000, "status": "excluded" if reason else "included",
            "exclusion_reason": reason or "",
        })
    metadata = {"schema_version": 1, "source_type": "android", "serial": serial, "device_label": label,
                "manufacturer": manufacturer, "model": model, "build_fingerprint": fingerprint,
                "volumes": [volume.__dict__ for volume in volumes]}
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
        for ext, values in sorted(totals.items())
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
    source_rows: dict[str, dict[str, str]] = {}
    items: list[PlanningItem] = []
    policy_engine = AndroidMediaDocumentsPolicy()
    for row in read_csv(step1 / "android_inventory.csv"):
        if row["status"] != "included":
            continue
        source = SourceFile(
            row["volume"], row["source_path"], row["relative_path"], row["filename"],
            row["extension"], int(row["size_bytes"]), int(row["mtime"]) * 1_000_000_000,
        )
        classification = policy_engine.classify(source, PolicyContext("android"))
        if classification.included:
            source_rows[source.source_path] = row
            items.append(PlanningItem(source, classification))
    mappings = {mapping.source_path: mapping for mapping in plan_destinations(items)}
    plan: list[dict[str, object]] = []
    for item in items:
        row = source_rows[item.file.source_path]
        classification = item.classification
        mapping = mappings[item.file.source_path]
        plan.append({
            **row, "destination_relative_path": mapping.destination_path.as_posix(),
            "origin": classification.origin, "media_type": classification.media_type,
            "package_name": classification.package_name, "origin_rule": classification.origin_rule,
            "archive_timestamp": classification.archive_timestamp if classification.archive_timestamp is not None else "",
            "archive_month": classification.archive_month, "date_source": classification.date_source,
            "destination_group": mapping.group_key, "position": mapping.position,
            "part_number": mapping.part_number if mapping.part_number is not None else "",
            "encoded_filename": mapping.encoded_filename, "media_bucket": str(mapping.destination_path.parent),
            "collision_index": mapping.collision_index,
            "collision_reason": "" if mapping.collision_index == 0 else "Windows-portable destination collision",
        })
    plan.sort(key=lambda row: (str(row["destination_relative_path"]).casefold(), str(row["source_path"])))
    output.mkdir(parents=True, exist_ok=False)
    _write_csv(output / "android_plan.csv", PLAN_FIELDS, plan)
    policy = {"schema_version": 3, "source_type": "android", "status": "locked", "backup_id": backup_id,
              "device": metadata, "media_extensions": {k: sorted(v) for k, v in MEDIA_EXTENSIONS.items()},
              "document_extensions": sorted(DOCUMENT_EXTENSIONS), "global_minimum_bytes": GLOBAL_MINIMUM,
              "category_minimum_bytes": CATEGORY_MINIMUMS, "excluded_directory_names": sorted(EXCLUDED_DIRECTORY_NAMES),
              "excluded_path_prefixes": sorted(EXCLUDED_PATH_PREFIXES), "streaming_markers": sorted(STREAMING_MARKERS),
              "media_layout": "media/<origin>/<media-type>/<YYYY_MM>[/part_NNN]/<file>",
              "max_media_files_per_directory": 100,
              "file_collision_strategy": "deterministic numeric suffix before extension",
              "filename_encoding": "reversible Windows-safe percent encoding before collision numbering",
              "expected_file_count": len(plan), "expected_bytes": sum(int(row["size_bytes"]) for row in plan)}
    (output / "android_policy.json").write_text(json.dumps(policy, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "format_policy.json").write_text(json.dumps(policy, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "policy.sha256").write_text(Manifest.canonical_digest(policy) + "\n", encoding="ascii")
    (output / "plan.sha256").write_text(Manifest.canonical_digest(plan) + "\n", encoding="ascii")
    _write_csv(output / "format_selection.csv", ["extension", "category", "minimum_size_bytes"], [
        {"extension": ext, "category": category, "minimum_size_bytes": max(GLOBAL_MINIMUM, CATEGORY_MINIMUMS[category])}
        for category, extensions in {**MEDIA_EXTENSIONS, "document": DOCUMENT_EXTENSIONS}.items() for ext in sorted(extensions)
    ])
    (output / "format_selection_report.md").write_text(
        f"# Step 2 — Locked Android copy plan\n\n> Status: **LOCKED**\n\n- Files: **{len(plan):,}**\n- Bytes: **{policy['expected_bytes']:,}**\n- Media layout: `media/<origin>/<media-type>/<YYYY_MM>[/part_NNN]/<file>`.\n- Groups split only when they exceed 100 files.\n- Names are Windows-safe and collision assignments are deterministic.\n- Documents are grouped by normalized extension.\n",
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


def _tool_version() -> str:
    try:
        return version("backup-hard")
    except PackageNotFoundError:
        return "development"


def _source_identity(device: dict[str, object], serial: str) -> SourceIdentity:
    return SourceIdentity(
        "android", serial, str(device.get("device_label", "android")),
        str(device.get("manufacturer", "")), str(device.get("model", "")),
        str(device.get("build_fingerprint", "")),
    )


def _run_marker(backup_id: str, device: dict[str, object], policy: object,
                plan: object) -> RunMarker:
    source_digest = Manifest.canonical_digest(device)
    return RunMarker(
        backup_id, source_digest, Manifest.canonical_digest(policy),
        Manifest.canonical_digest(plan),
    )


def _write_managed_metadata(metadata: Path, policy: object, plan: object,
                            marker: RunMarker) -> None:
    (metadata / "policy.json").write_text(
        json.dumps(policy, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (metadata / "plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (metadata / "policy.sha256").write_text(marker.policy_sha256 + "\n", encoding="ascii")
    (metadata / "plan.sha256").write_text(marker.plan_sha256 + "\n", encoding="ascii")


def _assert_managed_metadata(metadata: Path, policy: object, plan: object,
                             marker: RunMarker) -> None:
    try:
        stored_policy = json.loads((metadata / "policy.json").read_text(encoding="utf-8"))
        stored_plan = json.loads((metadata / "plan.json").read_text(encoding="utf-8"))
        policy_digest = (metadata / "policy.sha256").read_text(encoding="ascii").strip()
        plan_digest = (metadata / "plan.sha256").read_text(encoding="ascii").strip()
    except (OSError, json.JSONDecodeError) as exc:
        raise AndroidError("Managed destination metadata is missing or invalid") from exc
    if stored_policy != policy or stored_plan != plan:
        raise AndroidError("Managed policy or plan differs from the locked Step 2 data")
    if (policy_digest, plan_digest) != (marker.policy_sha256, marker.plan_sha256):
        raise AndroidError("Managed policy or plan digest was modified")


def _record_locked_plan(manifest: Manifest, plan: list[dict[str, str]]) -> None:
    with manifest.transaction() as connection:
        for row in plan:
            connection.execute(
                "INSERT OR IGNORE INTO volumes VALUES (?, ?, ?, ?)",
                (row["volume"], row["volume"], row["source_root"], int(row["volume"] != "internal")),
            )
            connection.execute(
                "INSERT OR IGNORE INTO inventory_files VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row["source_path"], row["volume"], row["relative_path"], row["filename"],
                 row["extension"], int(row["size_bytes"]), int(row["mtime"]) * 1_000_000_000),
            )
            category = FileCategory(row["category"])
            connection.execute(
                "INSERT OR IGNORE INTO classifications VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row["source_path"], category.value, "Android media/document policy",
                 row.get("origin", ""), row.get("media_type", ""), row.get("package_name", ""),
                 row.get("origin_rule", ""), int(row["archive_timestamp"]) if row.get("archive_timestamp") else None,
                 row.get("archive_month", "unknown_date"), row.get("date_source", "unknown")),
            )
            group = row.get("destination_group") or str(Path(row["destination_relative_path"]).parent)
            connection.execute(
                "INSERT OR IGNORE INTO destination_groups VALUES (?, 0, ?)",
                (group, int(bool(row.get("part_number")))),
            )
            connection.execute(
                "INSERT OR IGNORE INTO destination_mappings VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row["source_path"], row["destination_relative_path"], group,
                 int(row.get("position") or 0), int(row["part_number"]) if row.get("part_number") else None,
                 int(row["collision_index"]), row.get("encoded_filename", row["filename"])),
            )
        connection.execute(
            "UPDATE destination_groups SET file_count = "
            "(SELECT COUNT(*) FROM destination_mappings WHERE group_key = destination_groups.group_key)"
        )


def _open_adb_stream(adb: object, serial: str, source_path: str) -> BinaryIO:
    opener = getattr(adb, "open_stream", None)
    if opener is not None:
        return opener(serial, source_path)
    return BytesIO(adb.stream(serial, source_path))


def copy_plan(adb: Adb, serial: str, plan: list[dict[str, str]], destination: Path,
              output: Path, retries: int, policy: dict[str, object] | None = None,
              backup_id: str | None = None) -> tuple[list[dict[str, object]], bool]:
    if retries < 1:
        raise ValueError("retries must be at least 1")
    backup_id = backup_id or destination.name
    policy = policy or {"backup_id": backup_id, "device": {"serial": serial, "device_label": "android"}}
    device = dict(policy.get("device", {}))
    marker = _run_marker(backup_id, device, policy, plan)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        metadata = require_owned_destination(destination, marker)
        _assert_managed_metadata(metadata, policy, plan, marker)
    else:
        metadata = initialize_destination(destination, marker)
        _write_managed_metadata(metadata, policy, plan, marker)
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    interrupted = False
    manifest_path = metadata / "manifest.sqlite"
    with Manifest(manifest_path) as manifest:
        if manifest.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0:
            manifest.create_run(backup_id, _tool_version(), _source_identity(device, serial))
            manifest.lock_policy_and_plan(backup_id, policy, plan)
            _record_locked_plan(manifest, plan)
        else:
            manifest.assert_locked_digests(backup_id, policy, plan)
        completed = manifest.completed_source_paths()
        cleanup_owned_partials(destination, backup_id)
        require_free_space(destination, remaining_bytes(
            {row["source_path"]: int(row["size_bytes"]) for row in plan}, completed
        ))
        try:
            for row in plan:
                target = destination / Path(row["destination_relative_path"])
                old = manifest.copy_result(row["source_path"])
                if (old is not None and old["status"] == CopyStatus.COPIED.value and target.is_file()
                        and target.stat().st_size == int(row["size_bytes"])
                        and sha256_file(target) == old["sha256"]):
                    results.append({**row, "status": "already_verified", "attempts": 0,
                                    "sha256": old["sha256"], "reason": "SQLite state and destination hash match"})
                    continue
                if target.exists():
                    result = CopyResult(row["source_path"], str(target), CopyStatus.DESTINATION_CONFLICT,
                                        0, 0, "", "unrelated pre-existing destination was not overwritten")
                    manifest.record_copy_result(result)
                    results.append({**row, "status": "failed", "attempts": 0, "sha256": "", "reason": result.detail})
                    continue
                last_error = ""
                for attempt in range(1, retries + 1):
                    try:
                        current_size, current_mtime = _stat(adb, serial, row["source_path"])
                        if (current_size, current_mtime) != (int(row["size_bytes"]), int(row["mtime"])):
                            raise AndroidError("source metadata differs from the locked plan")
                        stream = _open_adb_stream(adb, serial, row["source_path"])
                        try:
                            copied = copy_and_record(row["source_path"], stream, target,
                                                     int(row["size_bytes"]), backup_id, manifest, attempt)
                        finally:
                            stream.close()
                        results.append({**row, "status": "copied", "attempts": attempt,
                                        "sha256": copied.sha256, "reason": ""})
                        break
                    except (AndroidError, OSError, RuntimeError, DestinationConflict) as exc:
                        last_error = str(exc)
                        if attempt < retries:
                            time.sleep(min(2 ** (attempt - 1), 4))
                else:
                    failed = CopyResult(row["source_path"], str(target), CopyStatus.FAILED,
                                        retries, 0, "", last_error)
                    manifest.record_copy_result(failed)
                    results.append({**row, "status": "failed", "attempts": retries,
                                    "sha256": "", "reason": last_error})
        except KeyboardInterrupt:
            interrupted = True
    fields = PLAN_FIELDS + ["status", "attempts", "sha256", "reason"]
    _write_csv(output / "copy_manifest.csv", fields, results)
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
                scan_errors: list[dict[str, str]] | None = None,
                policy: dict[str, object] | None = None, backup_id: str | None = None,
                fresh_metadata: dict[str, object] | None = None) -> list[dict[str, object]]:
    discrepancies: list[dict[str, object]] = []
    scan_errors = scan_errors or []
    backup_id = backup_id or destination.name
    policy = policy or {"backup_id": backup_id, "device": {"serial": serial, "device_label": "android"}}
    device = dict(policy.get("device", {}))
    marker = _run_marker(backup_id, device, policy, plan)
    try:
        metadata = require_owned_destination(destination, marker)
        _assert_managed_metadata(metadata, policy, plan, marker)
        manifest_path = metadata / "manifest.sqlite"
        if not manifest_path.is_file():
            raise AndroidError("Managed SQLite manifest is missing")
        connection = sqlite3.connect(f"file:{manifest_path.as_posix()}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT policy_sha256, plan_sha256 FROM runs WHERE backup_id = ?", (backup_id,)
            ).fetchone()
        finally:
            connection.close()
        if row != (marker.policy_sha256, marker.plan_sha256):
            raise AndroidError("SQLite policy or plan digest differs from the locked run")
    except (OSError, RuntimeError, ValueError) as exc:
        discrepancies.append({"source_path": "", "destination_relative_path": ".backup-hard",
                              "issue": "managed_metadata_invalid", "detail": str(exc)})
    if fresh_metadata is not None and fresh_metadata != device:
        discrepancies.append({"source_path": "", "destination_relative_path": "",
                              "issue": "source_identity_changed",
                              "detail": "fresh device or volume identity differs from the locked policy"})
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
        try:
            for path in destination.rglob("*"):
                relative = path.relative_to(destination)
                if relative.parts and relative.parts[0] == ".backup-hard":
                    continue
                if path.is_file() and relative not in planned_destinations:
                    discrepancies.append({"source_path": "", "destination_relative_path": str(relative),
                                          "issue": "unexpected_destination_file",
                                          "detail": "file is absent from locked plan"})
        except OSError as exc:
            discrepancies.append({"source_path": "", "destination_relative_path": "",
                                  "issue": "destination_scan_error", "detail": str(exc)})
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
    plan = json.loads((step_directory(args.reports_root, backup_id, 2) / "plan.json").read_text(encoding="utf-8"))
    results, interrupted = copy_plan(
        Adb(), args.device_serial, plan, destination_directory_android(args.destination_root, backup_id),
        step_directory(args.reports_root, backup_id, 3), args.retries, policy, backup_id,
    )
    if interrupted:
        return 130
    return 1 if any(row["status"] == "failed" for row in results) else 0


def verify_main(args: object) -> int:
    backup_id = validate_backup_id(args.backup_id)
    policy = json.loads((step_directory(args.reports_root, backup_id, 2) / "android_policy.json").read_text(encoding="utf-8"))
    if policy["device"]["serial"] != args.device_serial:
        raise AndroidError("Selected serial differs from the device locked in Step 2.")
    adb = Adb()
    select_device(adb, args.device_serial)
    plan = json.loads((step_directory(args.reports_root, backup_id, 2) / "plan.json").read_text(encoding="utf-8"))
    fresh_metadata, fresh_rows, scan_errors = inventory_device(
        adb, args.device_serial, policy["device"]["device_label"]
    )
    discrepancies = verify_plan(
        adb, args.device_serial, plan, destination_directory_android(args.destination_root, backup_id),
        step_directory(args.reports_root, backup_id, 4), fresh_rows, scan_errors,
        policy, backup_id, fresh_metadata,
    )
    return 1 if discrepancies else 0
