import csv
import hashlib
import json
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

    def test_media_plan_uses_fixed_tree_and_deterministic_filename_conflicts(self):
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
            self.assertIn("media/photos/social_media/photo.jpg", destinations)
            self.assertIn("media/photos/social_media/photo_1.jpg", destinations)
            self.assertIn("media/photos/screenshots/Screenshot_1.png", destinations)
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
            self.assertEqual(results[0]["status"], "already_complete")

    def test_copy_never_overwrites_unrelated_destination(self):
        data = b"x" * 10240
        row = plan_row(data=data)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "backup" / row["destination_relative_path"]
            target.parent.mkdir(parents=True)
            target.write_bytes(b"unrelated")
            results, _ = copy_plan(FileAdb({row["source_path"]: data}), "SERIAL_A", [row], root / "backup", root / "reports", 1)
            self.assertEqual(results[0]["status"], "failed")
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


class ListPathsPermissionTests(unittest.TestCase):
    class _FindAdb:
        def __init__(self, returncode, stdout, stderr):
            self.result = (returncode, stdout, stderr)

        def shell(self, serial, *args, **kwargs):
            code, out, err = self.result
            return subprocess.CompletedProcess(args, code, out, err)

    def test_denied_subfolder_keeps_partial_listing(self):
        from backup_hard.android import _list_paths
        adb = self._FindAdb(
            1, b"/storage/emulated/0/DCIM/a.jpg\0/storage/emulated/0/Music/b.mp3\0",
            b"find: /storage/emulated/0/Android/data/com.yoosee/files: Permission denied\n",
        )
        paths, errors = _list_paths(adb, "S", "/storage/emulated/0")
        self.assertEqual(len(paths), 2)
        self.assertEqual(errors[0]["path"], "/storage/emulated/0/Android/data/com.yoosee/files")

    def test_other_failures_still_raise(self):
        from backup_hard.android import _list_paths
        adb = self._FindAdb(1, b"", b"find: /nope: No such file or directory\n")
        with self.assertRaises(AndroidError):
            _list_paths(adb, "S", "/nope")


class BatchStatTests(unittest.TestCase):
    """The batched size/date lookup that replaces one ADB call per file."""

    class _StdoutAdb:
        def __init__(self, stdout, returncode=0):
            self.stdout, self.returncode, self.calls = stdout, returncode, []

        def shell(self, serial, *args, **kwargs):
            self.calls.append(args)
            return subprocess.CompletedProcess(args, self.returncode, self.stdout, b"")

    class _LocalShellAdb:
        """Runs the exact quoted command `adb shell` would send, in a local POSIX shell."""

        def __init__(self, sh):
            self.sh, self.calls = sh, []

        def shell(self, serial, *args, binary=False, check=True):
            import shlex
            self.calls.append(args)
            command = " ".join(shlex.quote(arg) for arg in args)
            result = subprocess.run([self.sh, "-c", command], capture_output=True)
            out, err = result.stdout, result.stderr
            if not binary:
                out, err = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
            return subprocess.CompletedProcess(args, result.returncode, out, err)

    def test_parses_sizes_dates_and_pipes_in_names(self):
        from backup_hard.android import _stat_many
        adb = self._StdoutAdb(b"10|100|/r/a.jpg\n20|200|/r/odd|name.mp4\n0|5|/r/empty\n")
        stats = _stat_many(adb, "S", "/r")
        self.assertEqual(stats, {"/r/a.jpg": (10, 100), "/r/odd|name.mp4": (20, 200), "/r/empty": (0, 5)})
        self.assertEqual(len(adb.calls), 1)
        self.assertEqual(adb.calls[0][:2], ("sh", "-c"))
        self.assertIn("xargs -0", adb.calls[0][2])
        self.assertEqual(adb.calls[0][3:], ("sh", "/r"))

    def test_crlf_line_endings_from_device(self):
        from backup_hard.android import _stat_many
        adb = self._StdoutAdb(b"21901912|1751332827|/r/Music/song.m4a\r\n5|6|/r/b\r\n")
        self.assertEqual(_stat_many(adb, "S", "/r"), {"/r/Music/song.m4a": (21901912, 1751332827), "/r/b": (5, 6)})

    def test_newline_names_and_duplicates_are_left_out(self):
        from backup_hard.android import _stat_many
        # "/r/a\nb" spills onto two lines; its first half collides with a real "/r/a".
        adb = self._StdoutAdb(b"1|1|/r/a\n9|9|/r/a\nb\n3|3|/r/c\ngarbage\n")
        self.assertEqual(_stat_many(adb, "S", "/r"), {"/r/c": (3, 3)})

    def test_failure_returns_empty_mapping(self):
        from backup_hard.android import _stat_many
        self.assertEqual(_stat_many(self._StdoutAdb(b"", returncode=1), "S", "/r"), {})

    def test_inventory_uses_batch_and_falls_back_per_file(self):
        from backup_hard import android
        listing = b"/r/DCIM/a.jpg\0/r/DCIM/b.jpg\0/r/Music/c.mp3\0"
        stats = b"20000|100|/r/DCIM/a.jpg\n30000|200|/r/Music/c.mp3\n"  # b.jpg missing -> fallback
        calls = []

        class Adb:
            def shell(self, serial, *args, **kwargs):
                calls.append(args)
                if "-print0" in args:
                    return subprocess.CompletedProcess(args, 0, listing, b"")
                if args[0] == "sh":
                    return subprocess.CompletedProcess(args, 0, stats, b"")
                if args[0] == "stat":
                    return subprocess.CompletedProcess(args, 0, "40000|300\n", "")
                raise AssertionError(args)

        with patch.object(android, "select_device", return_value=Device("S", "device", {})), \
             patch.object(android, "discover_volumes", return_value=([Volume("internal", "/r", "internal")], [])), \
             patch.object(android, "_prop", return_value="x"):
            _, rows, errors = android.inventory_device(Adb(), None, "phone")
        by_path = {row["source_path"]: (row["size_bytes"], row["mtime"]) for row in rows}
        self.assertEqual(by_path, {"/r/DCIM/a.jpg": (20000, 100), "/r/DCIM/b.jpg": (40000, 300), "/r/Music/c.mp3": (30000, 200)})
        self.assertEqual(errors, [])
        self.assertEqual(sum(1 for args in calls if args[0] == "stat"), 1)

    def test_real_shell_matches_per_file_stat(self):
        import shutil
        from backup_hard.android import _list_paths, _stat, _stat_many
        sh = shutil.which("sh")
        if not sh or not shutil.which("find", path=str(Path(sh).parent)):
            self.skipTest("no POSIX sh with find/stat available")
        adb = self._LocalShellAdb(sh)
        names = ["plain.jpg", "with space.mp4", "it's quoted.png", "$dollar & amp.txt", "ümlaut ñ 写真.jpg", "[brackets] (1).heic"]
        with tempfile.TemporaryDirectory() as tmp:
            for index, name in enumerate(names):
                sub = Path(tmp, "sub dir" if index % 2 else "", name)
                sub.parent.mkdir(exist_ok=True)
                sub.write_bytes(b"x" * (index * 1000 + 7))
            root = Path(tmp).as_posix()
            paths, errors = _list_paths(adb, "S", root)
            stats = _stat_many(adb, "S", root)
            self.assertEqual(errors, [])
            self.assertEqual(len(paths), len(names))
            self.assertEqual(set(stats), set(paths))
            for path in paths:
                self.assertEqual(stats[path], _stat(adb, "S", path), path)

    def test_real_shell_spans_multiple_chunks(self):
        import shutil
        from backup_hard import android
        sh = shutil.which("sh")
        if not sh or not shutil.which("xargs", path=str(Path(sh).parent)):
            self.skipTest("no POSIX sh with find/xargs/stat available")
        count = android.STAT_BATCH_SIZE * 2 + 37
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(count):
                Path(tmp, f"f {index:05d}.jpg").write_bytes(b"x" * (index % 50))
            stats = android._stat_many(self._LocalShellAdb(sh), "S", Path(tmp).as_posix())
        self.assertEqual(len(stats), count)
        self.assertEqual(stats[f"{Path(tmp).as_posix()}/f 00049.jpg"][0], 49)


class FileFormatsOrderTests(unittest.TestCase):
    def test_file_formats_sorted_by_total_size_descending(self):
        from backup_hard.android import write_inventory
        rows = [{"extension": ext, "size_bytes": size} for ext, size in
                [(".aaa", 5), (".mp4", 900), (".jpg", 300), (".jpg", 400), (".bbb", 5), ("", 50)]]
        with tempfile.TemporaryDirectory() as tmp:
            write_inventory(Path(tmp), {}, rows, [])
            with Path(tmp, "file_formats.csv").open(encoding="utf-8-sig") as handle:
                order = [row["extension"] for row in csv.DictReader(handle)]
        self.assertEqual(order, [".mp4", ".jpg", "[no extension]", ".aaa", ".bbb"])
