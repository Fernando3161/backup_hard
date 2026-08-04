"""Strict OS media-and-document policy adapter."""

from backup_hard.models import Classification, FileCategory, SourceFile
from backup_hard.policies.base import PolicyContext
from backup_hard.select_formats import OS_CONTENT_TYPES, OS_EXCLUDED_EXTENSIONS, extension_category


class OsMediaDocumentsPolicy:
    name = "os_media_documents"

    def classify(self, file: SourceFile, context: PolicyContext) -> Classification:
        kind = extension_category(file.extension.casefold())
        if kind not in OS_CONTENT_TYPES or file.extension.casefold() in OS_EXCLUDED_EXTENSIONS:
            return Classification(False, FileCategory.OTHER, "outside OS media/document policy")
        return Classification(True, FileCategory(kind), "OS media/document allowlist")
