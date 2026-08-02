import tempfile
import unittest
from pathlib import Path

from backup_hard.verify_backup import scan_tree, verify


class VerifyBackupTests(unittest.TestCase):
    def test_fresh_scans_validate_preserved_media(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source, destination = base / "source", base / "destination"
            (source / "photos").mkdir(parents=True)
            (destination / "photos").mkdir(parents=True)
            (source / "photos" / "cat.jpg").write_bytes(b"cat")
            (destination / "photos" / "cat.jpg").write_bytes(b"cat")
            plan = [{
                "source_relative_path": str(Path("photos/cat.jpg")),
                "destination_relative_path": str(Path("photos/cat.jpg")),
                "extension": ".jpg", "category": "media", "size_bytes": "3",
            }]
            discrepancies, source_totals, destination_totals = verify(
                scan_tree(source), scan_tree(destination), plan,
                {".jpg": "media"}, "other", {"media", "other"}, set(),
            )
            self.assertEqual(discrepancies, [])
            self.assertEqual(source_totals["media"], destination_totals["media"])

    def test_missing_media_is_critical_discrepancy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source, destination = base / "source", base / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "video.mp4").write_bytes(b"video")
            plan = [{
                "source_relative_path": "video.mp4",
                "destination_relative_path": str(Path("video.mp4")),
                "extension": ".mp4", "category": "media", "size_bytes": "5",
            }]
            discrepancies, _, _ = verify(
                scan_tree(source), scan_tree(destination), plan,
                {".mp4": "media"}, "other", {"media", "other"}, set(),
            )
            self.assertTrue(any(row["issue"] == "destination_missing" for row in discrepancies))

    def test_excluded_tmp_is_out_of_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source, destination = base / "source", base / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "ignored.tmp").write_bytes(b"tmp")
            discrepancies, source_totals, destination_totals = verify(
                scan_tree(source), scan_tree(destination), [], {}, "other", {"other"}, {".tmp"}
            )
            self.assertEqual(discrepancies, [])
            self.assertEqual(dict(source_totals), {})
            self.assertEqual(dict(destination_totals), {})

    def test_os_path_and_size_exclusions_are_out_of_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source, destination = base / "source", base / "destination"
            (source / "Windows").mkdir(parents=True)
            destination.mkdir()
            (source / "Windows" / "large.jpg").write_bytes(b"x" * 20000)
            (source / "tiny.jpg").write_bytes(b"x" * 9999)
            discrepancies, source_totals, destination_totals = verify(
                scan_tree(source), scan_tree(destination), [], {".jpg": "media"},
                None, {"media"}, set(), {"root_directory_names": {"windows"}},
                {".jpg": 10000},
            )
            self.assertEqual(discrepancies, [])
            self.assertEqual(dict(source_totals), {})
            self.assertEqual(dict(destination_totals), {})


if __name__ == "__main__":
    unittest.main()
