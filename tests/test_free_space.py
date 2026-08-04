from backup_hard.engine.copying import remaining_bytes


def test_remaining_space_excludes_completed_files():
    assert remaining_bytes({"a": 10, "b": 20, "c": 30}, {"a", "c"}) == 20
