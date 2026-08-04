import io
import unittest

from backup_hard.models import SourceVolume
from backup_hard.sources.android.inventory import inventory_source


class Process:
    def __init__(self, output: bytes, returncode=0, stderr=b""):
        self.stdout, self.stderr, self.returncode = io.BytesIO(output), io.BytesIO(stderr), returncode

    def wait(self):
        return self.returncode


class Runner:
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), 0

    def open_shell(self, serial, arguments):
        self.calls += 1
        return Process(self.outputs.pop(0))


class AndroidBatchInventoryTests(unittest.TestCase):
    def test_one_adb_process_per_volume_and_unicode_paths(self):
        first = b"F\x001024\x001700000000\x00/storage/emulated/0/Pictures/caf\xc3\xa9.jpg\x00"
        second = b"F\x002048\x001700000001\x00/storage/ABCD/Music/a song.mp3\x00"
        runner = Runner([first, second])
        volumes = [SourceVolume("internal", "internal", "/storage/emulated/0"), SourceVolume("ABCD", "sd_card", "/storage/ABCD", True)]
        files, errors = inventory_source(runner, "serial", volumes)
        self.assertEqual(runner.calls, 2)
        self.assertEqual([file.filename for file in files], ["caf\xe9.jpg", "a song.mp3"])
        self.assertFalse(errors)


if __name__ == "__main__":
    unittest.main()
