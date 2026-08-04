from backup_hard.engine.verification import VerificationSummary


def test_scan_errors_make_shared_summary_fail():
    assert VerificationSummary().passed
    assert not VerificationSummary(scan_errors=["denied"]).passed
