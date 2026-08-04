"""Central Android extension, origin, type, and archive-date policy."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from backup_hard.models import Classification, FileCategory, SourceFile
from backup_hard.policies.base import PolicyContext


MEDIA_EXTENSIONS = {
    FileCategory.IMAGE: frozenset({".avif", ".bmp", ".dng", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".raw", ".tif", ".tiff", ".webp"}),
    FileCategory.VIDEO: frozenset({".3gp", ".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".webm"}),
    FileCategory.AUDIO: frozenset({".aac", ".aiff", ".alac", ".amr", ".flac", ".m4a", ".mid", ".midi", ".mp3", ".ogg", ".opus", ".wav", ".wma"}),
}
DOCUMENT_EXTENSIONS = frozenset({".csv", ".doc", ".docx", ".epub", ".html", ".md", ".mobi", ".odp", ".ods", ".odt", ".pdf", ".ppt", ".pptx", ".rtf", ".txt", ".xls", ".xlsx"})
MINIMUM_BYTES = {
    FileCategory.IMAGE: 10 * 1024,
    FileCategory.VIDEO: 1024 * 1024,
    FileCategory.AUDIO: 2 * 1024,
    FileCategory.DOCUMENT: 1024,
}
PACKAGE_ORIGINS = {
    "com.whatsapp": "whatsapp", "org.telegram.messenger": "telegram",
    "com.instagram.android": "instagram", "com.facebook.katana": "facebook",
    "com.facebook.orca": "messenger", "com.camerasideas.instashot": "inshot",
    "com.reddit.frontpage": "reddit", "com.linkedin.android": "linkedin",
}
FILENAME_DATE = re.compile(r"(?<!\d)(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)(?!\d)")


def classify_origin(relative_path: str) -> tuple[str, str, str]:
    normalized = relative_path.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    folded = [part.casefold() for part in parts]
    package = ""
    if len(parts) >= 3 and folded[:2] == ["android", "media"]:
        package = parts[2]
        known = PACKAGE_ORIGINS.get(package.casefold())
        if known:
            return known, f"known package {package}", package
        final = re.sub(r"[^a-z0-9]+", "_", package.casefold().split(".")[-1]).strip("_")
        return (final or "other"), "sanitized Android/media package", package
    joined = "/".join(folded)
    rules = [
        ("screenshots", "screenshots"), ("screen recordings", "screen_recordings"),
        ("screen_recordings", "screen_recordings"), ("dcim/camera", "camera"),
        ("download", "downloads"), ("bluetooth", "bluetooth"),
        ("podcast", "podcasts"), ("recording", "recordings"), ("music", "music"),
    ]
    for marker, origin in rules:
        if marker in joined:
            return origin, f"path marker {marker}", package
    return "other", "no controlled origin rule matched", package


def select_archive_date(file: SourceFile) -> tuple[int | None, str, str]:
    match = FILENAME_DATE.search(file.filename)
    if match:
        try:
            year, month, day = (int(part) for part in match.groups())
            value = datetime(year, month, day, tzinfo=timezone.utc)
            return int(value.timestamp()), value.strftime("%Y_%m"), "filename"
        except ValueError:
            pass
    if file.mtime_ns > 0:
        seconds = file.mtime_ns / 1_000_000_000
        try:
            value = datetime.fromtimestamp(seconds, timezone.utc)
            return int(seconds), value.strftime("%Y_%m"), "filesystem_mtime"
        except (OverflowError, OSError, ValueError):
            pass
    return None, "unknown_date", "unknown"


class AndroidMediaDocumentsPolicy:
    name = "android_media_documents"

    def classify(self, file: SourceFile, context: PolicyContext) -> Classification:
        extension = file.extension.casefold()
        category = next((kind for kind, values in MEDIA_EXTENSIONS.items() if extension in values), None)
        if category is None and extension in DOCUMENT_EXTENSIONS:
            category = FileCategory.DOCUMENT
        if category is None:
            return Classification(False, FileCategory.OTHER, "unsupported file type")
        minimum = MINIMUM_BYTES[category]
        if file.size_bytes < minimum:
            return Classification(False, category, f"below minimum of {minimum} bytes")
        if category == FileCategory.DOCUMENT:
            return Classification(True, category, "Android media/document policy")
        origin, origin_rule, package = classify_origin(file.relative_path)
        timestamp, month, date_source = select_archive_date(file)
        return Classification(
            True, category, "Android media/document policy", origin=origin,
            media_type={FileCategory.IMAGE: "images", FileCategory.VIDEO: "videos", FileCategory.AUDIO: "audio"}[category],
            package_name=package, origin_rule=origin_rule,
            archive_timestamp=timestamp, archive_month=month, date_source=date_source,
        )
