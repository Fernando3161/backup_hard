import hashlib

from backup_hard.engine.verification import VerificationItem, VerificationSummary, verify_destination
from backup_hard.models import VerificationLevel


def test_scan_errors_make_shared_summary_fail():
    assert VerificationSummary().passed
    assert not VerificationSummary(scan_errors=["denied"]).passed


def test_metadata_and_hash_levels(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"content")
    item = VerificationItem("source", path, 7, hashlib.sha256(b"content").hexdigest())
    assert verify_destination(item, VerificationLevel.METADATA).passed
    assert verify_destination(item, VerificationLevel.HASH).passed
