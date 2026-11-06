import unittest

from backup_hard.paths import decode_windows_name, destination_collision_key, encode_windows_name


class PathEncodingTests(unittest.TestCase):
    def test_normal_name_is_unchanged(self) -> None:
        self.assertEqual(encode_windows_name("photo.jpg").encoded, "photo.jpg")

    def test_windows_valid_names_are_kept_verbatim(self) -> None:
        for value in ["100%.txt", "The_Honking_%28Main_Episode%29_-_143.webp", "May 4, 2026 6꞉42꞉51 PM.m4a",
                      "ümlaut 写真.jpg", "+dfg1Zkz+rOLVF=.webp", "CONSOLE.jpg"]:
            with self.subTest(value=value):
                self.assertEqual(encode_windows_name(value).encoded, value)

    def test_invalid_trailing_and_reserved_names_are_safe_and_reversible(self) -> None:
        for value in ['a<b>:c"d/e\\f|g?h*.txt', "name. ", "CON", "LPT1.txt", "50%: done?.txt", "tab\there.jpg"]:
            with self.subTest(value=value):
                encoded = encode_windows_name(value).encoded
                self.assertNotEqual(encoded, value)
                self.assertFalse(set('<>:"/\\|?*') & set(encoded))
                self.assertFalse(encoded.endswith((" ", ".")))
                self.assertEqual(decode_windows_name(encoded), value)

    def test_long_name_is_deterministic_and_original_is_recorded(self) -> None:
        value = "a" * 400 + ".jpg"
        first = encode_windows_name(value)
        self.assertTrue(first.shortened)
        self.assertEqual(first, encode_windows_name(value))
        self.assertLessEqual(len(first.encoded), 180)
        self.assertTrue(first.encoded.endswith(".jpg"))
        self.assertEqual(first.original, value)
        self.assertNotEqual(first.encoded, encode_windows_name("a" * 399 + "b.jpg").encoded)

    def test_long_unsafe_name_is_shortened_too(self) -> None:
        self.assertLessEqual(len(encode_windows_name("x:" * 300 + ".pdf").encoded), 180)

    def test_case_and_unicode_normalization_collide(self) -> None:
        self.assertEqual(destination_collision_key("A/CAFÉ.jpg"), destination_collision_key("a/cafe\u0301.JPG"))


if __name__ == "__main__":
    unittest.main()
