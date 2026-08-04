import tempfile
import unittest
from pathlib import Path

from backup_hard.destination import DestinationError, RunMarker, initialize_destination, require_owned_destination


class DestinationTests(unittest.TestCase):
    def marker(self) -> RunMarker:
        return RunMarker("phone_20260804", "source-id", "policy", "plan")

    def test_new_destination_is_marked_and_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "phone_20260804"
            initialize_destination(destination, self.marker())
            self.assertEqual(require_owned_destination(destination, self.marker()).name, ".backup-hard")

    def test_unmarked_existing_destination_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "existing"
            destination.mkdir()
            with self.assertRaisesRegex(DestinationError, "already exists"):
                initialize_destination(destination, self.marker())
            with self.assertRaisesRegex(DestinationError, "not owned"):
                require_owned_destination(destination, self.marker())

    def test_changed_identity_or_digest_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "backup"
            initialize_destination(destination, self.marker())
            changed = RunMarker("phone_20260804", "other-source", "policy", "plan")
            with self.assertRaisesRegex(DestinationError, "different"):
                require_owned_destination(destination, changed)


if __name__ == "__main__":
    unittest.main()
