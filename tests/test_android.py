import csv
import hashlib
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backup_hard.android import (
    Adb, AndroidError, Device, Volume, CATEGORY_MINIMUMS, GLOBAL_MINIMUM,
    android_media_bucket, classify, copy_plan, create_plan, discover_volumes,
    exclusion_reason, select_device, verify_plan,
)
from backup_hard.destination import DestinationError


class DeviceAdb:
    def __init__(self, devices):
        self._devices = devices

    def devices(self):
        return self._devices


class CommandAdb:
    def __init__(self, responses):
        self.responses = responses

    def run(self, args, **kwargs):
        key = tuple(args[-4:])
        for suffix, response in self.responses:
            if tuple(args[-len(suffix):]) == suffix:
                return response
        return subprocess.CompletedProcess(args, 1, "", "unhandled")


class FileAdb:
    def __init__(self, files, failures=0):
        self.files = files
        self.failures = failures
        self.pull_calls = 0

    def pull(self, serial, source, destination):
        self.pull_calls += 1
        if self.pull_calls <= self.failures:
            raise AndroidError("transient")
        destination.write_bytes(self.files[source])

    def stream(self, serial, source):
        self.pull_calls += 1
        if self.pull_calls <= self.failures:
            raise AndroidError("transient")
        return self.files[source]

    def shell(self, serial, *args, **kwargs):
        path = args[-1]
        data = self.files[path]
        return subprocess.CompletedProcess(args, 0, f"{len(data)}|100\n", "")


def plan_row(source="/storage/emulated/0/DCIM/a.jpg", destination="media/internal/DCIM/a.jpg", data=b"x" * 10240):
    return {
        "serial": "SERIAL_A", "device_label": "phone", "volume": "internal",
        "source_root": "/storage/emulated/0", "source_path": source,
        "relative_path": source.split("/0/", 1)[-1], "destination_relative_path": destination,
        "filename": Path(source).name, "extension": Path(source).suffix, "category": "image",
        "size_bytes": str(len(data)), "mtime": "100", "collision_index": "0", "collision_reason": "",
    }


class AndroidTests(unittest.TestCase):
    def test_android_media_category_tree(self):
        cases = {
            ("Music/song.opus", "audio"): "media/audio",
            ("DCIM/Camera/clip.mp4", "video"): "media/video/camera",
            ("Android/media/com.whatsapp/clip.mp4", "video"): "media/video/social_media",
            ("Download/clip.mp4", "video"): "media/video/other",
            ("DCIM/Camera/photo.jpg", "image"): "media/photos/camera",
            ("Pictures/Screenshots/Screenshot_1.png", "image"): "media/photos/screenshots",
            ("DCIM/Screenshots/Screenshot_2.png", "image"): "media/photos/screenshots",
            ("Android/media/org.telegram.messenger/photo.jpg", "image"): "media/photos/social_media",
            ("Download/photo.jpg", "image"): "media/photos/other",
        }
        for (path, category), expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(android_media_bucket(path, category), expected)

    def test_shell_quotes_metacharacters_and_unusual_filenames(self):
        adb = Adb("adb")
        with patch.object(adb, "run", return_value=subprocess.CompletedProcess([], 0, "1|2", "")) as run:
            adb.shell("SERIAL_A", "stat", "-c", "%s|%Y", "--", "/sdcard/a file's [copy].jpg")
        command = run.call_args.args[0][-1]
        self.assertIn("'%s|%Y'", command)
        self.assertIn("a file", command)
        self.assertIn("'\"'\"'", command)

    def test_adb_not_found_is_actionable(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(AndroidError, "Platform Tools"):
                Adb().devices()

    def test_adb_text_output_uses_utf8_replacement(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "ok", "")) as run:
            Adb().run(["devices"])
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "replace")

    def test_device_states_and_selection(self):
        with self.assertRaisesRegex(AndroidError, "No Android"):
            select_device(DeviceAdb([]), None)
        with self.assertRaisesRegex(AndroidError, "unauthorized"):
            select_device(DeviceAdb([Device("one", "unauthorized", {})]), "one")
        with self.assertRaisesRegex(AndroidError, "offline"):
            select_device(DeviceAdb([Device("one", "offline", {})]), "one")
        devices = [Device("one", "device", {"model": "X10"}), Device("two", "device", {"model": "X10"})]
        self.assertEqual(select_device(DeviceAdb([devices[0]]), None).serial, "one")
        with self.assertRaisesRegex(AndroidError, "--device-serial"):
            select_device(DeviceAdb(devices), None)
        self.assertEqual(select_device(DeviceAdb(devices), "two").serial, "two")

    def test_storage_discovery_deduplicates_aliases_and_labels_sd(self):
        ok = lambda stdout="": subprocess.CompletedProcess([], 0, stdout, "")
        responses = [
            (("sm", "list-volumes", "public"), ok("public:179,65 mounted ABCD-1234\n")),
            (("readlink", "-f", "/storage/emulated/0"), ok("/storage/emulated/0\n")),
            (("test", "-r", "/storage/emulated/0"), ok()),
            (("readlink", "-f", "/storage/ABCD-1234"), ok("/mnt/media_rw/ABCD-1234\n")),
            (("test", "-r", "/storage/ABCD-1234"), ok()),
        ]
        volumes, errors = discover_volumes(CommandAdb(responses), "one")
        self.assertEqual([v.label for v in volumes], ["internal", "sd_card"])
        self.assertFalse(errors)

    def test_classification_paths_case_and_threshold_boundaries(self):
        self.assertEqual(classify(".JPG".casefold()), "image")
        self.assertEqual(classify(".docx"), "document")
        self.assertIsNone(classify(".apk"))
        self.assertIsNone(exclusion_reason("Android/media/unlisted.app/export.jpg", ".jpg", "image", 10240))
        self.assertIn("package", exclusion_reason("Android/data/app/a.jpg", ".jpg", "image", 10240))
        self.assertIsNone(exclusion_reason("Android/media/app/a.jpg", ".jpg", "image", 10240))
        self.assertIsNotNone(exclusion_reason("DCIM/a.jpg", ".jpg", "image", 10239))
        self.assertIsNone(exclusion_reason("Music/a.opus", ".opus", "audio", 2048))
        self.assertIsNotNone(exclusion_reason("Music/a.opus", ".opus", "audio", 2047))
        self.assertIsNone(exclusion_reason("Movies/a.mp4", ".mp4", "video", 1048576))
        self.assertIsNotNone(exclusion_reason("Movies/a.mp4", ".mp4", "video", 1048575))
        self.assertIsNone(exclusion_reason("Download/a.pdf", ".pdf", "document", 1024))
        self.assertIsNotNone(exclusion_reason("Download/a.pdf", ".pdf", "document", 1023))
        self.assertIn("streaming", exclusion_reason("Android/media/com.netflix.mediaclient/movie.mp4", ".mp4", "video", 2000000))

    def test_document_collisions_are_deterministic_and_plan_is_immutable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            step1, step2 = root / "step1", root / "step2"
            step1.mkdir()
            (step1 / "device.json").write_text(json.dumps({"serial": "S", "device_label": "p", "volumes": []}), encoding="utf-8")
            fields = ["serial", "device_label", "manufacturer", "model", "volume", "source_root", "source_path", "relative_path", "filename", "extension", "category", "size_bytes", "mtime", "status", "exclusion_reason"]
            with (step1 / "android_inventory.csv").open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for index in range(4):
                    writer.writerow({"serial": "S", "device_label": "p", "volume": "internal", "source_root": "/s", "source_path": f"/s/d{index}/report.pdf", "relative_path": f"d{index}/report.pdf", "filename": "report.pdf", "extension": ".pdf", "category": "document", "size_bytes": 1024, "mtime": 1, "status": "included"})
            plan = create_plan(step1, step2, "phone_20260802")
            self.assertEqual([r["destination_relative_path"] for r in plan], ["documents/pdf/report.pdf", "documents/pdf/report_1.pdf", "documents/pdf/report_2.pdf", "documents/pdf/report_3.pdf"])
            with self.assertRaisesRegex(AndroidError, "already locked"):
                create_plan(step1, step2, "phone_20260802")

    def test_media_plan_uses_origin_type_month_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            step1, step2 = root / "step1", root / "step2"
            step1.mkdir()
            (step1 / "device.json").write_text(json.dumps({"serial": "S", "device_label": "p", "volumes": []}), encoding="utf-8")
            fields = ["serial", "device_label", "manufacturer", "model", "volume", "source_root", "source_path", "relative_path", "filename", "extension", "category", "size_bytes", "mtime", "status", "exclusion_reason"]
            relatives = [
                "Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images/photo.jpg",
                "Android/media/org.telegram.messenger/Telegram Images/photo.jpg",
                "Pictures/Screenshots/Screenshot_1.png",
            ]
            with (step1 / "android_inventory.csv").open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for relative in relatives:
                    filename = Path(relative).name
                    writer.writerow({"serial": "S", "device_label": "p", "volume": "internal", "source_root": "/s", "source_path": "/s/" + relative, "relative_path": relative, "filename": filename, "extension": Path(filename).suffix, "category": "image", "size_bytes": 10240, "mtime": 1, "status": "included"})
            plan = create_plan(step1, step2, "phone_20260802")
            destinations = [str(row["destination_relative_path"]) for row in plan]
            self.assertIn("media/whatsapp/images/1970_01/photo.jpg", destinations)
            self.assertIn("media/telegram/images/1970_01/photo.jpg", destinations)
            self.assertIn("media/screenshots/images/1970_01/Screenshot_1.png", destinations)
            self.assertEqual(len(destinations), len({value.casefold() for value in destinations}))

    def test_copy_retries_is_atomic_and_resume_uses_hash(self):
        data = b"x" * 10240
        row = plan_row(data=data)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adb = FileAdb({row["source_path"]: data}, failures=1)
            results, interrupted = copy_plan(adb, "SERIAL_A", [row], root / "backup", root / "reports", 3)
            self.assertFalse(interrupted)
            self.assertEqual(results[0]["attempts"], 2)
            self.assertEqual(results[0]["status"], "copied")
            self.assertFalse(list((root / "backup").rglob("*.partial")))
            results, _ = copy_plan(adb, "SERIAL_A", [row], root / "backup", root / "reports", 3)
            self.assertEqual(results[0]["status"], "already_verified")

    def test_managed_copy_persists_locked_metadata_sqlite_and_verifies(self):
        data = b"x" * 10240
        row = plan_row(data=data)
        policy = {"backup_id": "phone_20260804", "device": {
            "serial": "SERIAL_A", "device_label": "phone", "manufacturer": "HMD",
            "model": "Nokia", "build_fingerprint": "build", "volumes": [],
        }}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "phone_20260804"
            adb = FileAdb({row["source_path"]: data})
            results, interrupted = copy_plan(
                adb, "SERIAL_A", [row], destination, root / "reports", 1,
                policy, "phone_20260804",
            )
            self.assertFalse(interrupted)
            self.assertEqual(results[0]["status"], "copied")
            metadata = destination / ".backup-hard"
            self.assertEqual(
                {"run.json", "manifest.sqlite", "policy.json", "plan.json", "policy.sha256", "plan.sha256"},
                {path.name for path in metadata.iterdir() if not path.name.endswith(("-wal", "-shm"))},
            )
            connection = sqlite3.connect(metadata / "manifest.sqlite")
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM copy_results").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT status FROM copy_results").fetchone()[0], "copied")
            finally:
                connection.close()
            discrepancies = verify_plan(
                adb, "SERIAL_A", [row], destination, root / "verify", policy=policy,
                backup_id="phone_20260804", fresh_metadata=policy["device"],
            )
            self.assertFalse(discrepancies)

    def test_resume_refuses_modified_locked_plan(self):
        data = b"x" * 10240
        row = plan_row(data=data)
        policy = {"backup_id": "phone_20260804", "device": {"serial": "SERIAL_A"}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "phone_20260804"
            copy_plan(FileAdb({row["source_path"]: data}), "SERIAL_A", [row], destination,
                      root / "reports", 1, policy, "phone_20260804")
            changed = [{**row, "size_bytes": str(len(data) + 1)}]
            with self.assertRaisesRegex(DestinationError, "different"):
                copy_plan(FileAdb({row["source_path"]: data}), "SERIAL_A", changed, destination,
                          root / "reports", 1, policy, "phone_20260804")

    def test_copy_never_overwrites_unrelated_destination(self):
        data = b"x" * 10240
        row = plan_row(data=data)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "backup" / row["destination_relative_path"]
            target.parent.mkdir(parents=True)
            target.write_bytes(b"unrelated")
            with self.assertRaisesRegex(DestinationError, "not owned"):
                copy_plan(FileAdb({row["source_path"]: data}), "SERIAL_A", [row], root / "backup", root / "reports", 1)
            self.assertEqual(target.read_bytes(), b"unrelated")

    def test_verify_detects_hash_mismatch_changed_source_and_unexpected(self):
        data = b"x" * 10240
        row = plan_row(data=data)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "backup" / row["destination_relative_path"]
            target.parent.mkdir(parents=True)
            target.write_bytes(b"y" * len(data))
            (root / "backup" / "unexpected.txt").write_bytes(b"z")
            discrepancies = verify_plan(FileAdb({row["source_path"]: data}), "SERIAL_A", [row], root / "backup", root / "reports")
            issues = {r["issue"] for r in discrepancies}
            self.assertIn("content_hash_mismatch", issues)
            self.assertIn("unexpected_destination_file", issues)

    def test_verify_detects_source_changed_after_inventory(self):
        data = b"x" * 10240
        row = plan_row(data=data)
        row["mtime"] = "99"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "backup" / row["destination_relative_path"]
            target.parent.mkdir(parents=True)
            target.write_bytes(data)
            discrepancies = verify_plan(FileAdb({row["source_path"]: data}), "SERIAL_A", [row], root / "backup", root / "reports")
            self.assertIn("source_changed_since_inventory", {r["issue"] for r in discrepancies})


if __name__ == "__main__":
    unittest.main()
