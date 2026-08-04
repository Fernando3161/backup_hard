from io import BytesIO

from backup_hard.engine.copying import stream_to_destination
from backup_hard.engine.verification import VerificationItem, verify_destination
from backup_hard.models import SourceVolume, VerificationLevel
from backup_hard.policies.android_media_documents import AndroidMediaDocumentsPolicy
from backup_hard.policies.base import PolicyContext
from backup_hard.sources.android.inventory import inventory_source


class Process:
    def __init__(self, output):
        self.stdout, self.stderr = BytesIO(output), BytesIO()
    def wait(self):
        return 0


class FakeAdb:
    def open_shell(self, serial, arguments):
        return Process(b"F\0" + b"10240\0" + b"1704067200\0" + b"/sdcard/DCIM/IMG_20240101.jpg\0")


def test_fake_adb_inventory_classify_copy_verify(tmp_path):
    files, errors = inventory_source(FakeAdb(), "serial", [SourceVolume("internal", "Internal", "/sdcard")])
    assert not errors and len(files) == 1
    classification = AndroidMediaDocumentsPolicy().classify(files[0], PolicyContext("android"))
    assert classification.included and classification.media_type == "images"
    destination = tmp_path / "media" / "camera" / "photos" / "2024-01" / files[0].filename
    content = b"x" * 10240
    outcome = stream_to_destination(BytesIO(content), destination, len(content), "run")
    result = verify_destination(VerificationItem(files[0].source_path, destination, len(content), outcome.sha256),
                                VerificationLevel.FULL)
    assert result.passed
