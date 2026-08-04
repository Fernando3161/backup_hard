import hashlib

from backup_hard.engine.verification import VerificationItem, verify_destination
from backup_hard.models import VerificationLevel


def test_equal_size_content_mismatch_is_detected(tmp_path):
    destination = tmp_path / "copy.bin"
    destination.write_bytes(b"bad")
    item = VerificationItem("source", destination, 3, hashlib.sha256(b"good"[:3]).hexdigest())
    result = verify_destination(item, VerificationLevel.FULL)
    assert not result.passed and result.issue == "hash_mismatch"


def test_missing_destination_is_access_failure(tmp_path):
    item = VerificationItem("source", tmp_path / "missing", 1)
    result = verify_destination(item, VerificationLevel.METADATA)
    assert not result.passed and result.issue == "destination_access_error"
