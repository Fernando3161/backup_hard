import hashlib
import tempfile
import unittest
from pathlib import Path

from backup_hard.engine.copying import CopyError, DestinationConflict, stream_to_destination


class LogicalStream:
    def __init__(self, total: int, byte=b"x"):
        self.remaining, self.byte, self.maximum_request = total, byte, 0

    def read(self, size=-1):
        self.maximum_request = max(self.maximum_request, size)
        count = min(self.remaining, size)
        self.remaining -= count
        return self.byte * count


class StreamCopyTests(unittest.TestCase):
    def test_large_logical_stream_uses_bounded_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            total, block = 25 * 1024 * 1024, 64 * 1024
            stream = LogicalStream(total)
            target = Path(temp) / "large.bin"
            result = stream_to_destination(stream, target, total, "run", block)
            self.assertEqual(result.size_bytes, total)
            self.assertEqual(stream.maximum_request, block)
            self.assertEqual(result.sha256, hashlib.sha256(b"x" * total).hexdigest())

    def test_existing_destination_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "existing.bin"
            target.write_bytes(b"existing")
            with self.assertRaises(DestinationConflict):
                stream_to_destination(LogicalStream(3), target, 3, "run")
            self.assertEqual(target.read_bytes(), b"existing")

    def test_short_stream_is_removed_not_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(CopyError):
                stream_to_destination(LogicalStream(2), root / "target", 3, "run")
            self.assertFalse((root / "target").exists())
            self.assertFalse(list(root.glob("*.partial")))

    def test_long_destination_name_uses_short_owned_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / (("x" * 190) + ".bin")
            result = stream_to_destination(LogicalStream(3), target, 3, "long_run_identifier")
            self.assertEqual(result.size_bytes, 3)
            self.assertTrue(target.is_file())
            self.assertFalse(list(Path(temp).glob("*.partial")))


if __name__ == "__main__":
    unittest.main()
