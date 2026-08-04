import unittest


MAX_MEDIA_FILES_PER_DIRECTORY = 100


def required_parts(file_count: int) -> list[int]:
    if file_count <= MAX_MEDIA_FILES_PER_DIRECTORY:
        return []
    return list(range(1, (file_count - 1) // MAX_MEDIA_FILES_PER_DIRECTORY + 2))


class MediaLayoutRequirementTests(unittest.TestCase):
    def test_conditional_part_boundaries(self) -> None:
        cases = {
            99: [],
            100: [],
            101: [1, 2],
            200: [1, 2],
            201: [1, 2, 3],
        }
        for count, expected in cases.items():
            with self.subTest(count=count):
                self.assertEqual(required_parts(count), expected)

    def test_part_names_are_fixed_width(self) -> None:
        self.assertEqual([f"part_{value:03d}" for value in required_parts(201)], [
            "part_001", "part_002", "part_003",
        ])


if __name__ == "__main__":
    unittest.main()
