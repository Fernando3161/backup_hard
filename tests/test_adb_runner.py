import subprocess
import unittest
from unittest.mock import patch

from backup_hard.sources.android.adb import AdbError, AdbRunner


class AdbRunnerTests(unittest.TestCase):
    def test_shell_quotes_remote_arguments(self) -> None:
        runner = AdbRunner()
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "ok", "")) as run:
            runner.shell("serial", ["stat", "-c", "%s|%Y", "--", "/sdcard/a file's.jpg"])
        command = run.call_args.args[0][-1]
        self.assertIn("'%s|%Y'", command)
        self.assertIn("'\"'\"'", command)
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_nonzero_command_has_actionable_error(self) -> None:
        runner = AdbRunner()
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "offline")):
            with self.assertRaisesRegex(AdbError, "offline"):
                runner.run(["devices"])

    def test_file_stream_uses_popen_not_capture_output(self) -> None:
        runner = AdbRunner()
        with patch("subprocess.Popen") as popen:
            runner.open_exec_out("serial", ["cat", "--", "/large.mp4"])
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertFalse(popen.call_args.kwargs["shell"])


if __name__ == "__main__":
    unittest.main()
