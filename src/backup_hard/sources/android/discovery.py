"""Accessible Android shared-storage volume discovery."""

from __future__ import annotations

import re

from backup_hard.models import SourceVolume
from backup_hard.sources.android.adb import AdbRunner


def discover_volumes(runner: AdbRunner, serial: str) -> tuple[list[SourceVolume], list[str]]:
    candidates = [("internal", "/storage/emulated/0", "emulated", False)]
    result = runner.shell(serial, ["sm", "list-volumes", "public"], check=False)
    if result.returncode == 0:
        for identifier in sorted(set(re.findall(r"mounted\s+([0-9A-Fa-f-]{4,})", result.stdout))):
            candidates.append(("sd_card", f"/storage/{identifier}", identifier, True))
    volumes: list[SourceVolume] = []
    errors: list[str] = []
    seen: set[str] = set()
    removable_count = sum(removable for _, _, _, removable in candidates)
    for label, root, identifier, removable in candidates:
        canonical_result = runner.shell(serial, ["readlink", "-f", root], check=False)
        canonical = canonical_result.stdout.strip() if canonical_result.returncode == 0 else root
        if canonical in seen:
            continue
        access = runner.shell(serial, ["test", "-r", root], check=False)
        if access.returncode:
            errors.append(f"inaccessible volume: {root}")
            continue
        seen.add(canonical)
        readable = label if not removable or removable_count == 1 else f"sd_card_{re.sub('[^a-z0-9]', '', identifier.casefold())[:8]}"
        volumes.append(SourceVolume(identifier, readable, root, removable))
    return volumes, errors
