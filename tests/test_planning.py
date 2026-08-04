import unittest

from backup_hard.engine.planning import PlanningItem, assign_media_groups
from backup_hard.models import Classification, FileCategory, SourceFile


def items(count: int) -> list[PlanningItem]:
    return [PlanningItem(
        SourceFile("internal", f"/DCIM/IMG_{index:04d}.jpg", f"DCIM/IMG_{index:04d}.jpg", f"IMG_{index:04d}.jpg", ".jpg", 10240, index),
        Classification(True, FileCategory.IMAGE, "test", "camera", "images", archive_timestamp=index, archive_month="2026_08", date_source="filesystem_mtime"),
    ) for index in range(count)]


class PlanningTests(unittest.TestCase):
    def test_required_split_boundaries(self) -> None:
        cases = {99: {None}, 100: {None}, 101: {1, 2}, 200: {1, 2}, 201: {1, 2, 3}}
        for count, expected in cases.items():
            with self.subTest(count=count):
                mappings = assign_media_groups(items(count))
                self.assertEqual({mapping.part_number for mapping in mappings}, expected)
                if count > 100:
                    part_counts = {part: sum(item.part_number == part for item in mappings) for part in expected}
                    self.assertLessEqual(max(part_counts.values()), 100)

    def test_ordering_is_deterministic(self) -> None:
        original = items(101)
        forward = assign_media_groups(original)
        backward = assign_media_groups(list(reversed(original)))
        self.assertEqual(forward, backward)
        self.assertEqual(forward[100].part_number, 2)


if __name__ == "__main__":
    unittest.main()
