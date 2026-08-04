import unittest

from backup_hard.models import FileCategory, SourceFile
from backup_hard.policies.android_media_documents import AndroidMediaDocumentsPolicy, classify_origin
from backup_hard.policies.base import PolicyContext
from backup_hard.policies.full_filesystem import FullFilesystemPolicy


def source(filename: str, extension: str, size: int) -> SourceFile:
    return SourceFile("volume", "/" + filename, filename, filename, extension, size, 0)


class PolicyTests(unittest.TestCase):
    def test_android_threshold_is_inclusive(self) -> None:
        result = AndroidMediaDocumentsPolicy().classify(source("a.jpg", ".JPG", 10240), PolicyContext("android"))
        self.assertTrue(result.included)
        self.assertEqual(result.category, FileCategory.IMAGE)

    def test_android_unknown_extension_is_excluded(self) -> None:
        self.assertFalse(AndroidMediaDocumentsPolicy().classify(source("a.apk", ".apk", 99999), PolicyContext("android")).included)

    def test_full_filesystem_keeps_unknown_but_excludes_tmp(self) -> None:
        policy = FullFilesystemPolicy()
        self.assertTrue(policy.classify(source("a.unknown", ".unknown", 1), PolicyContext("filesystem")).included)
        self.assertFalse(policy.classify(source("a.tmp", ".tmp", 1), PolicyContext("filesystem")).included)

    def test_android_origin_and_filename_month(self) -> None:
        item = SourceFile("internal", "/storage/x", "Android/media/com.whatsapp/x/IMG-20260804-WA1.jpg", "IMG-20260804-WA1.jpg", ".jpg", 10240, 1)
        result = AndroidMediaDocumentsPolicy().classify(item, PolicyContext("android"))
        self.assertEqual((result.origin, result.media_type, result.archive_month, result.date_source),
                         ("whatsapp", "images", "2026_08", "filename"))

    def test_unknown_package_origin_is_sanitized(self) -> None:
        self.assertEqual(classify_origin("Android/media/org.example.cool_app/picture.jpg")[0], "cool_app")


if __name__ == "__main__":
    unittest.main()
