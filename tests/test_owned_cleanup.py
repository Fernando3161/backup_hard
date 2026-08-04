from backup_hard.engine.copying import cleanup_owned_partials


def test_cleanup_removes_only_current_run_partials(tmp_path):
    own = tmp_path / ".a.backup-hard-run1-token.partial"
    other = tmp_path / ".a.backup-hard-run2-token.partial"
    unrelated = tmp_path / "notes.partial"
    for path in (own, other, unrelated):
        path.write_text("x")
    assert cleanup_owned_partials(tmp_path, "run1") == 1
    assert not own.exists()
    assert other.exists() and unrelated.exists()
