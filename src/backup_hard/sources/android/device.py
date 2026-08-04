"""Android device selection and identity discovery."""

from __future__ import annotations

from dataclasses import dataclass

from backup_hard.models import SourceIdentity
from backup_hard.sources.android.adb import AdbError, AdbRunner


@dataclass(frozen=True)
class ConnectedDevice:
    serial: str
    state: str
    details: dict[str, str]


def connected_devices(runner: AdbRunner) -> list[ConnectedDevice]:
    devices: list[ConnectedDevice] = []
    for line in runner.run(["devices", "-l"]).stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2:
            devices.append(ConnectedDevice(
                fields[0], fields[1],
                dict(token.split(":", 1) for token in fields[2:] if ":" in token),
            ))
    return devices


def select_device(runner: AdbRunner, serial: str | None) -> ConnectedDevice:
    devices = connected_devices(runner)
    if serial is None:
        ready = [device for device in devices if device.state == "device"]
        if not devices:
            raise AdbError("No Android device is connected")
        if len(ready) != 1:
            raise AdbError("Pass --device-serial when multiple or no ready devices are present")
        selected = ready[0]
    else:
        selected = next((device for device in devices if device.serial == serial), None)
        if selected is None:
            raise AdbError("Requested Android device is disconnected")
    if selected.state == "unauthorized":
        raise AdbError("Device is unauthorized; accept the USB debugging prompt")
    if selected.state == "offline":
        raise AdbError("Device is offline")
    if selected.state != "device":
        raise AdbError(f"Device is not ready: {selected.state}")
    return selected


def read_identity(runner: AdbRunner, device: ConnectedDevice, label: str) -> SourceIdentity:
    def prop(name: str) -> str:
        return runner.shell(device.serial, ["getprop", name]).stdout.strip()

    return SourceIdentity(
        "android", device.serial, label,
        prop("ro.product.manufacturer"), prop("ro.product.model"),
        prop("ro.build.fingerprint"),
    )
