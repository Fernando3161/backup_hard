import json
import tempfile
import unittest
from pathlib import Path

from backup_hard.copy_backup import (
    CategoryRule,
    build_plan,
    execute_plan,
    extended_path,
    load_policy,
    load_minimum_sizes,
    path_exclusion_reason,
)


class CopyBackupTests(unittest.TestCase):
    def rules(self) -> dict[str, CategoryRule]:
        return {
            "media": CategoryRule("media", "preserve"),
            "document": CategoryRule("document", "preserve"),
            "other": CategoryRule(".", "preserve"),
        }

    def test_plan_preserves_every_source_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            (source / "a").mkdir(parents=True)
            (source / "b").mkdir()
            (source / "a" / "photo.JPG").write_bytes(b"a")
            (source / "b" / "photo.jpg").write_bytes(b"bb")
            (source / "a" / "same.bin").write_bytes(b"1")
            (source / "b" / "same.bin").write_bytes(b"2")
            plan = build_plan(source, {".jpg": "media"}, self.rules(), "other")
            destinations = {str(item.source_relative_path): str(item.destination_relative_path) for item in plan.files}
            self.assertEqual(destinations[str(Path("a/photo.JPG"))], str(Path("a/photo.JPG")))
            self.assertEqual(destinations[str(Path("b/photo.jpg"))], str(Path("b/photo.jpg")))
            self.assertEqual(destinations[str(Path("a/same.bin"))], str(Path("a/same.bin")))
            self.assertEqual(destinations[str(Path("b/same.bin"))], str(Path("b/same.bin")))

    def test_execute_is_atomic_and_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source, destination, reports = base / "source", base / "destination", base / "reports"
            source.mkdir()
            (source / "file.txt").write_bytes(b"content")
            plan = build_plan(source, {".txt": "document"}, self.rules(), "other")
            reports.mkdir()
            first, interrupted = execute_plan(plan, source, destination, reports, 2)
            self.assertFalse(interrupted)
            self.assertEqual(first.counts["copied"], 1)
            self.assertEqual((destination / "file.txt").read_bytes(), b"content")
            second, _ = execute_plan(plan, source, destination, reports, 2)
            self.assertEqual(second.counts["already_present"], 1)

    def test_locked_schema_three_policy_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "status": "locked",
                        "extension_categories": {".jpg": "media"},
                        "category_rules": {
                            "media": {"destination_folder": "media", "layout": "preserve"},
                            "other": {"destination_folder": ".", "layout": "preserve"},
                        },
                        "unmapped_extension_action": "include",
                        "unmapped_extension_category": "other",
                    }
                ),
                encoding="utf-8",
            )
            mappings, rules, default, exclusions = load_policy(path)
            self.assertEqual(mappings[".jpg"], "media")
            self.assertEqual(rules["media"].layout, "preserve")
            self.assertEqual(default, "other")
            self.assertEqual(exclusions, set())

    def test_plan_excludes_tmp_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            (source / "keep.jpg").write_bytes(b"x")
            (source / "skip.tmp").write_bytes(b"y")
            plan = build_plan(
                source, {".jpg": "media"}, self.rules(), "other", {".tmp"}
            )
            self.assertEqual([item.source_relative_path.name for item in plan.files], ["keep.jpg"])

    def test_extended_path_is_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(extended_path(Path(temp) / "file.txt").is_absolute())

    def test_extended_path_does_not_require_target_to_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing" / "deep" / "file.bin"
            result = extended_path(missing)
            self.assertIn("file.bin", str(result))

    def test_os_policy_excludes_unmapped_formats_and_restricted_paths(self) -> None:
        path_rules = {"root_directory_names": {"windows"}, "directory_names": {"steam"}}
        self.assertIsNotNone(path_exclusion_reason(Path("Windows/Web/a.jpg"), path_rules))
        self.assertIsNotNone(path_exclusion_reason(Path("Users/me/Steam/a.jpg"), path_rules))
        self.assertIsNone(path_exclusion_reason(Path("Users/me/Pictures/a.jpg"), path_rules))

    def test_plan_applies_content_specific_minimum_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            (source / "small.jpg").write_bytes(b"x" * 10239)
            (source / "keep.jpg").write_bytes(b"x" * 10240)
            (source / "small.pdf").write_bytes(b"x" * (1024 * 1024 - 1))
            (source / "keep.pdf").write_bytes(b"x" * (1024 * 1024))
            plan = build_plan(
                source, {".jpg": "media", ".pdf": "document"}, self.rules(), None,
                minimum_sizes={".jpg": 10240, ".pdf": 1024 * 1024},
            )
            self.assertEqual(
                {item.source_relative_path.name for item in plan.files},
                {"keep.jpg", "keep.pdf"},
            )


if __name__ == "__main__":
    unittest.main()
