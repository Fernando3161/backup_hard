"""Read-only ADB process execution isolated from backup policy."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Sequence


class AdbError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


class AdbRunner:
    def __init__(self, executable: str = "adb") -> None:
        self.executable = executable

    def run(self, arguments: Sequence[str], *, check: bool = True) -> CommandResult:
        try:
            completed = subprocess.run(
                [self.executable, *arguments], shell=False, capture_output=True,
                text=True, encoding="utf-8", errors="replace", check=False,
            )
        except FileNotFoundError as exc:
            raise AdbError("adb was not found; install Android SDK Platform Tools") from exc
        result = CommandResult(completed.stdout, completed.stderr, completed.returncode)
        if check and result.returncode:
            raise AdbError(result.stderr.strip() or "ADB command failed without diagnostics")
        return result

    def shell(self, serial: str, arguments: Sequence[str], *, check: bool = True) -> CommandResult:
        command = " ".join(shlex.quote(argument) for argument in arguments)
        return self.run(["-s", serial, "shell", command], check=check)

    def open_exec_out(self, serial: str, arguments: Sequence[str]) -> subprocess.Popen:
        command = " ".join(shlex.quote(argument) for argument in arguments)
        try:
            return subprocess.Popen(
                [self.executable, "-s", serial, "exec-out", command], shell=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise AdbError("adb was not found; install Android SDK Platform Tools") from exc
