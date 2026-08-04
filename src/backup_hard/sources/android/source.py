"""Android ADB implementation of the shared BackupSource protocol."""

from __future__ import annotations

from typing import BinaryIO, Iterable, cast

from backup_hard.models import SourceFile, SourceIdentity, SourceVolume
from backup_hard.sources.android.adb import AdbRunner
from backup_hard.sources.android.device import ConnectedDevice, read_identity
from backup_hard.sources.android.discovery import discover_volumes


class AndroidAdbSource:
    def __init__(self, runner: AdbRunner, device: ConnectedDevice, label: str) -> None:
        self.runner, self.device, self.label = runner, device, label
        self._identity: SourceIdentity | None = None
        self._volumes: list[SourceVolume] | None = None

    def identity(self) -> SourceIdentity:
        if self._identity is None:
            self._identity = read_identity(self.runner, self.device, self.label)
        return self._identity

    def list_volumes(self) -> list[SourceVolume]:
        if self._volumes is None:
            self._volumes, errors = discover_volumes(self.runner, self.device.serial)
            if errors and not self._volumes:
                raise RuntimeError("; ".join(errors))
        return list(self._volumes)

    def inventory(self) -> Iterable[SourceFile]:
        from backup_hard.sources.android.inventory import inventory_source
        files, _ = inventory_source(self.runner, self.device.serial, self.list_volumes())
        return files

    def stat(self, file: SourceFile) -> SourceFile:
        result = self.runner.shell(self.device.serial, ["stat", "-c", "%s|%Y", "--", file.source_path])
        size, mtime = result.stdout.strip().split("|", 1)
        return SourceFile(file.volume_id, file.source_path, file.relative_path, file.filename,
                          file.extension, int(size), int(mtime) * 1_000_000_000)

    def open_stream(self, file: SourceFile) -> BinaryIO:
        process = self.runner.open_exec_out(self.device.serial, ["cat", "--", file.source_path])
        if process.stdout is None:
            raise RuntimeError("ADB stream has no stdout pipe")
        return cast(BinaryIO, process.stdout)
