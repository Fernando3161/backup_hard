import csv
import tempfile
import unittest
from pathlib import Path

from backup_hard.inventory import NO_EXTENSION, inventory, normalized_extension, write_outputs


class InventoryTests(unittest.TestCase):
    def test_extension_normalization(self) -> None:
        self.assertEqual(normalized_extension("PHOTO.JPEG"), ".jpeg")
        self.assertEqual(normalized_extension("archive.tar.gz"), ".gz")
        self.assertEqual(normalized_extension("README"), NO_EXTENSION)
        self.assertEqual(normalized_extension("odd."), NO_EXTENSION)

    def test_inventory_and_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            output = Path(temp) / "reports"
            (root / "nested").mkdir(parents=True)
            (root / "one.JPG").write_bytes(b"123")
            (root / "nested" / "two.jpg").write_bytes(b"12345")
            (root / "README").write_bytes(b"x")

            result = inventory(root)
            self.assertEqual(result.total_files, 3)
            self.assertEqual(result.by_extension[".jpg"].file_count, 2)
            self.assertEqual(result.by_extension[".jpg"].total_size_bytes, 8)
            self.assertEqual(result.by_extension[NO_EXTENSION].total_size_bytes, 1)

            write_outputs(result, root, output)
            with (output / "file_formats.csv").open(encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual({row["extension"] for row in rows}, {".jpg", NO_EXTENSION})
            self.assertTrue((output / "inventory_report.md").is_file())
            self.assertTrue((output / "scan_errors.csv").is_file())


if __name__ == "__main__":
    unittest.main()
