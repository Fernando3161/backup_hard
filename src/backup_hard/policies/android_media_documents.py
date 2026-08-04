"""Central Android extension and minimum-size policy."""

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
        return Classification(True, category, "Android media/document policy")
