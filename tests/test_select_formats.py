import json
import tempfile
import unittest
from pathlib import Path

from backup_hard.select_formats import (
    build_selection,
    destination_category,
    extension_category,
    write_outputs,
    write_os_policy,
)


class FormatSelectionTests(unittest.TestCase):
    def test_required_formats_are_included(self) -> None:
        required = {
            ".mp4", ".jpg", ".zip", ".pdf", ".mp3", ".png", ".jpeg", ".rar",
            ".heic", ".mobi", ".3gp", ".pptx", ".docx", ".csv", ".xlsx",
            ".epub", ".mov", ".webm", ".webp", ".ppt", ".doc", ".drawio",
            ".exe", ".html", ".htm", ".json", ".jar", ".java", ".xml",
            ".file",
        }
        self.assertFalse({item for item in required if extension_category(item) is None})

    def test_unknown_format_requires_review(self) -> None:
        rows = [{"extension": ".mystery", "file_count": "2", "total_size_bytes": "10"}]
        result = build_selection(rows)
        self.assertEqual(result[0]["decision"], "review")

    def test_lock_all_includes_unknown_format(self) -> None:
        rows = [{"extension": ".mystery", "file_count": "2", "total_size_bytes": "10"}]
        result = build_selection(rows, include_all=True)
        self.assertEqual(result[0]["decision"], "include")
        self.assertEqual(result[0]["category"], "other")
        self.assertEqual(result[0]["layout"], "preserve")

    def test_tmp_is_explicitly_excluded(self) -> None:
        rows = [{"extension": ".tmp", "file_count": "2", "total_size_bytes": "199385"}]
        result = build_selection(rows, include_all=True)
        self.assertEqual(result[0]["decision"], "exclude")
        self.assertEqual(result[0]["layout"], "excluded")

    def test_personal_database_and_extensionless_files_are_included(self) -> None:
        self.assertEqual(extension_category(".zolca"), "database_data")
        self.assertEqual(extension_category("[no extension]"), "generic_requested")

    def test_media_and_document_layout_categories(self) -> None:
        self.assertEqual(destination_category(".jpg", include_all=True), "media")
        self.assertEqual(destination_category(".mp4", include_all=True), "media")
        rows = [
            {"extension": ".jpg", "file_count": "1", "total_size_bytes": "2"},
            {"extension": ".pdf", "file_count": "1", "total_size_bytes": "3"},
        ]
        result = build_selection(rows, include_all=True)
        self.assertEqual(result[0]["layout"], "preserve")
        self.assertEqual(result[1]["layout"], "preserve")

    def test_locked_policy_includes_unknown_future_extensions(self) -> None:
        rows = [{"extension": ".jpg", "file_count": "1", "total_size_bytes": "2"}]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            write_outputs(build_selection(rows, include_all=True), output, locked=True)
            policy = json.loads((output / "format_policy.json").read_text(encoding="utf-8"))
            self.assertEqual(policy["unmapped_extension_action"], "include")
            self.assertEqual(policy["unmapped_extension_category"], "other")
            self.assertEqual(policy["schema_version"], 3)
            self.assertEqual(policy["case"], "A_full_disk")

    def test_os_policy_accepts_portable_relative_prefixes(self) -> None:
        rows = [{"extension": ".jpg", "file_count": "1", "total_size_bytes": "20000"}]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            write_os_policy(rows, output, "system_media_20260801", ["project/reports"])
            policy = json.loads((output / "format_policy.json").read_text(encoding="utf-8"))
            self.assertEqual(policy["path_exclusions"]["relative_path_prefixes"], ["project/reports"])


if __name__ == "__main__":
    unittest.main()
