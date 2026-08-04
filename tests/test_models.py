import unittest
from pathlib import PurePosixPath

from backup_hard.models import CopyStatus, DestinationMapping, SourceIdentity


class ModelTests(unittest.TestCase):
    def test_source_identity_is_immutable(self) -> None:
        identity = SourceIdentity("android", "serial-hash", "phone")
        with self.assertRaises(AttributeError):
            identity.label = "changed"  # type: ignore[misc]

    def test_destination_mapping_retains_source_and_locked_position(self) -> None:
        mapping = DestinationMapping(
            "/storage/emulated/0/DCIM/a.jpg",
            PurePosixPath("media/camera/images/2026_08/a.jpg"),
            "camera/images/2026_08", 1, None, 0, "a.jpg",
        )
        self.assertEqual(mapping.position, 1)
        self.assertTrue(str(mapping.destination_path).endswith("a.jpg"))

    def test_copy_status_values_match_exit_contract_vocabulary(self) -> None:
        self.assertEqual(CopyStatus.DESTINATION_CONFLICT.value, "destination_conflict")


if __name__ == "__main__":
    unittest.main()
