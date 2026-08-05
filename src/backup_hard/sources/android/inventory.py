"""NUL-safe Android inventory with one bounded-stream ADB process per volume."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import BinaryIO, Iterator, cast

from backup_hard.models import SourceFile, SourceVolume
from backup_hard.sources.android.adb import AdbRunner


BATCH_SCRIPT = r'''
root="$1"
find "$root" \( -path "$root/Android/data" -o -path "$root/Android/obb" \) -prune -o -type f -print0 |
while IFS= read -r -d '' path; do
  size=$(stat -c %s -- "$path" 2>/dev/null) || { printf 'E\0%s\0stat failed\0' "$path"; continue; }
  mtime=$(stat -c %Y -- "$path" 2>/dev/null) || { printf 'E\0%s\0stat failed\0' "$path"; continue; }
  printf 'F\0%s\0%s\0%s\0' "$size" "$mtime" "$path"
done
'''.strip()


def _nul_fields(stream: BinaryIO, block_size: int = 64 * 1024) -> Iterator[bytes]:
    pending = bytearray()
    while True:
        block = stream.read(block_size)
        if not block:
            break
        pending.extend(block)
        while True:
            marker = pending.find(0)
            if marker < 0:
                break
            yield bytes(pending[:marker])
            del pending[:marker + 1]
    if pending:
        raise ValueError("ADB inventory ended with an incomplete NUL field")


def inventory_volume(runner: AdbRunner, serial: str, volume: SourceVolume) -> tuple[list[SourceFile], list[str]]:
    process = runner.open_shell(serial, ["sh", "-c", BATCH_SCRIPT, "backup-hard", volume.root])
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("ADB inventory pipes are unavailable")
    fields = iter(_nul_fields(cast(BinaryIO, process.stdout)))
    files: list[SourceFile] = []
    errors: list[str] = []
    try:
        while True:
            kind = next(fields).decode("ascii")
            if kind == "F":
                size = int(next(fields))
                mtime = int(next(fields))
                path = next(fields).decode("utf-8", errors="surrogateescape")
                relative = path[len(volume.root):].lstrip("/")
                pure = PurePosixPath(relative)
                files.append(SourceFile(
                    volume.stable_id, path, relative, pure.name, pure.suffix.casefold(),
                    size, mtime * 1_000_000_000,
                ))
            elif kind == "E":
                path = next(fields).decode("utf-8", errors="replace")
                detail = next(fields).decode("utf-8", errors="replace")
                errors.append(f"{path}: {detail}")
            else:
                raise ValueError(f"Unknown Android inventory record: {kind!r}")
    except StopIteration:
        pass
    except (ValueError, UnicodeError) as exc:
        errors.append(f"partial inventory output retained: {exc}")
    stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
    returncode = process.wait()
    if stderr:
        errors.extend(line for line in stderr.splitlines() if line.strip())
    if returncode:
        errors.append(f"inventory process exited {returncode}")
    return files, errors


def inventory_source(runner: AdbRunner, serial: str, volumes: list[SourceVolume]) -> tuple[list[SourceFile], list[str]]:
    files: list[SourceFile] = []
    errors: list[str] = []
    for volume in volumes:
        volume_files, volume_errors = inventory_volume(runner, serial, volume)
        files.extend(volume_files)
        errors.extend(volume_errors)
    return files, errors
