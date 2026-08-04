import unittest

from backup_hard.paths import decode_windows_name, destination_collision_key, encode_windows_name


class PathEncodingTests(unittest.TestCase):
    def test_normal_name_is_unchanged(self) -> None:
        self.assertEqual(encode_windows_name("photo.jpg").encoded, "photo.jpg")

    def test_invalid_trailing_and_reserved_names_are_safe_and_reversible(self) -> None:
        for value in ['a<b>:c"d/e\\f|g?h*.txt', "name. ", "CON", "LPT1.txt", "100%.txt"]:
            with self.subTest(value=value):
                encoded = encode_windows_name(value)
                self.assertEqual(decode_windows_name(encoded.encoded), value)

    def test_long_name_is_deterministic_and_original_is_recorded(self) -> None:
        value = "a" * 400 + ".jpg"
        first = encode_windows_name(value)
        self.assertTrue(first.shortened)
        self.assertEqual(first, encode_windows_name(value))
        self.assertLessEqual(len(first.encoded), 180)
        self.assertEqual(first.original, value)

    def test_case_and_unicode_normalization_collide(self) -> None:
        self.assertEqual(destination_collision_key("A/CAFÉ.jpg"), destination_collision_key("a/cafe\u0301.JPG"))


if __name__ == "__main__":
    unittest.main()
