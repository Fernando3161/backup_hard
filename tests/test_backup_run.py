import unittest
from pathlib import Path

from backup_hard.backup_run import destination_directory, step_directory, validate_backup_id


class BackupRunTests(unittest.TestCase):
    def test_descriptive_date_id_builds_run_paths(self) -> None:
        backup_id = validate_backup_id("archive_disk_20260801")
        self.assertEqual(
            step_directory(Path("reports"), backup_id, 3),
            Path("reports/archive_disk_20260801/step_3"),
        )
        self.assertEqual(
            destination_directory(Path("destination"), backup_id),
            Path("destination/backup_archive_disk_20260801"),
        )

    def test_date_only_and_unsafe_ids_are_rejected(self) -> None:
        for value in ("20260801", "Invalid Label 20260801", "../disk_20260801"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_backup_id(value)


if __name__ == "__main__":
    unittest.main()
