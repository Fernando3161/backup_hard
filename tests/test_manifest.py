import tempfile
import unittest
from pathlib import Path

from backup_hard.manifest import Manifest, ManifestError
from backup_hard.models import CopyResult, CopyStatus, SourceIdentity


class ManifestTests(unittest.TestCase):
    def test_completed_file_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.sqlite"
            with Manifest(path) as manifest:
                manifest.create_run("phone_20260804", "0.4.0.dev0", SourceIdentity("android", "id", "phone"))
                manifest.record_copy_result(CopyResult("/a", "media/a", CopyStatus.COPIED, 1, 3, "abc"))
            with Manifest(path) as reopened:
                self.assertEqual(reopened.copy_result("/a")["sha256"], "abc")

    def test_duplicate_run_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with Manifest(Path(temp) / "manifest.sqlite") as manifest:
                identity = SourceIdentity("filesystem", "id", "source")
                manifest.create_run("source_20260804", "test", identity)
                with self.assertRaises(ManifestError):
                    manifest.create_run("source_20260804", "test", identity)

    def test_transaction_rolls_back_on_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with Manifest(Path(temp) / "manifest.sqlite") as manifest:
                with self.assertRaises(KeyboardInterrupt):
                    with manifest.transaction() as connection:
                        connection.execute("INSERT INTO errors(stage,path,operation,detail) VALUES('x','x','x','x')")
                        raise KeyboardInterrupt
                self.assertEqual(manifest.connection.execute("SELECT COUNT(*) FROM errors").fetchone()[0], 0)

    def test_policy_and_plan_digests_are_canonical_and_locked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with Manifest(Path(temp) / "manifest.sqlite") as manifest:
                identity = SourceIdentity("android", "id", "phone")
                manifest.create_run("phone_20260804", "test", identity)
                first = manifest.lock_policy_and_plan("phone_20260804", {"b": 2, "a": 1}, [{"x": 1}])
                second = manifest.lock_policy_and_plan("phone_20260804", {"a": 1, "b": 2}, [{"x": 1}])
                self.assertEqual(first, second)
                with self.assertRaisesRegex(ManifestError, "changed"):
                    manifest.lock_policy_and_plan("phone_20260804", {"a": 9}, [{"x": 1}])


if __name__ == "__main__":
    unittest.main()
