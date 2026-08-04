import unittest

from backup_hard.sources.android.adb import AdbError, CommandResult
from backup_hard.sources.android.device import connected_devices, select_device
from backup_hard.sources.android.discovery import discover_volumes


class Runner:
    def __init__(self, devices="List of devices attached\n", volume=""):
        self.device_output, self.volume_output = devices, volume

    def run(self, arguments, check=True):
        return CommandResult(self.device_output, "", 0)

    def shell(self, serial, arguments, check=True):
        if arguments[:2] == ["sm", "list-volumes"]:
            return CommandResult(self.volume_output, "", 0)
        if arguments[:2] == ["readlink", "-f"]:
            root = arguments[-1]
            canonical = "/canonical/internal" if root == "/storage/emulated/0" else root
            return CommandResult(canonical + "\n", "", 0)
        return CommandResult("", "", 0)


class AndroidSourceTests(unittest.TestCase):
    def test_device_selection_states(self):
        with self.assertRaisesRegex(AdbError, "No Android"):
            select_device(Runner(), None)
        with self.assertRaisesRegex(AdbError, "unauthorized"):
            select_device(Runner("List of devices attached\na unauthorized\n"), "a")
        devices = connected_devices(Runner("List of devices attached\na device model:X\nb device model:X\n"))
        self.assertEqual([device.serial for device in devices], ["a", "b"])

    def test_internal_and_removable_volumes(self):
        volumes, errors = discover_volumes(Runner(volume="public:1 mounted ABCD-1234\n"), "a")
        self.assertEqual([volume.label for volume in volumes], ["internal", "sd_card"])
        self.assertFalse(errors)


if __name__ == "__main__":
    unittest.main()
