from io import BytesIO

from backup_hard.engine.copying import copy_and_record
from backup_hard.manifest import Manifest
from backup_hard.models import SourceIdentity


def test_copy_result_is_immediately_available_for_resume(tmp_path):
    with Manifest(tmp_path / "run.sqlite3") as manifest:
        manifest.create_run("run", "test", SourceIdentity("filesystem", "id", "label"))
        copy_and_record("source/a", BytesIO(b"abc"), tmp_path / "out" / "a", 3, "run", manifest)
        assert manifest.completed_source_paths() == {"source/a"}
        assert manifest.copy_result("source/a")["sha256"]
