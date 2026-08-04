"""Full-format filesystem policy."""

from backup_hard.models import Classification, FileCategory, SourceFile
from backup_hard.policies.base import PolicyContext


class FullFilesystemPolicy:
    name = "full_filesystem"

    def classify(self, file: SourceFile, context: PolicyContext) -> Classification:
        if file.extension.casefold() == ".tmp":
            return Classification(False, FileCategory.OTHER, "explicit temporary extension")
        return Classification(True, FileCategory.OTHER, "full-filesystem policy")
