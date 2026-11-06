# Backup Hard

Staged, resumable, and verified file backups of a folder, a drive, or an
Android phone. Each backup runs in four steps: inventory, lock a plan, copy,
and verify. Every step writes reviewable reports to `reports/<backup-id>/step_N/`.

It uses only Python's standard library. It is a file-level backup tool, not a
disk-imaging tool.

## Installation

Requires Python 3.9 or newer. Install into a local virtual environment in the
repository root, never into your base Python.

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

macOS and Linux:

```shell
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Activate the environment in every new terminal before running any command
below; the prompt shows `(.venv)` while it is active. If PowerShell blocks
activation, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

## Placeholders

Replace each placeholder, including the angle brackets, with your own value.
Quote paths that contain spaces.

| Placeholder | Meaning |
|---|---|
| `<backup-id>` | Name for one backup: `<source-label>_<YYYYMMDD>`, lowercase letters, digits, and underscores |
| `<source-path>` | Folder or drive to back up |
| `<destination-root>` | Writable folder that receives the backup |
| `<device-serial>` | Phone's serial, from `adb devices -l` |
| `<device-label>` | Readable name you choose for the phone |

## Filesystem backup

The backup is written to `<destination-root>/backup_<backup-id>/`.

```shell
# 1. Inventory. Review step_1/scan_errors.csv afterwards.
python -m backup_hard.inventory --backup-id <backup-id> --source "<source-path>"

# 2. Lock the formats to copy: all formats...
python -m backup_hard.select_formats --backup-id <backup-id> --lock-all
#    ...or only media and documents from an OS disk
python -m backup_hard.select_formats --backup-id <backup-id> --os-media-documents

# 3. Copy. Safe to rerun; finished files are skipped.
python -m backup_hard.copy_backup --backup-id <backup-id> --source "<source-path>" --destination-root "<destination-root>"

# 4. Verify
python -m backup_hard.verify_backup --backup-id <backup-id> --source "<source-path>" --destination-root "<destination-root>"
```

The OS media/document policy skips system, program, temporary, and log
folders. Add `--exclude-relative-prefix "<folder>"` to skip more; it can be
repeated.

## Android backup

Install Google's Android SDK Platform Tools so `adb` is on `PATH`. On the phone,
enable USB debugging, connect it, unlock it, and accept the authorization
prompt. `adb devices -l` then lists its serial. Use a separate `<backup-id>`
for each phone.

The backup is written to `<destination-root>/<backup-id>/`.

```shell
# 1. Inventory, read-only. Review android_inventory.csv and scan_errors.csv.
python -m backup_hard.inventory --source-type android --backup-id <backup-id> --device-serial <device-serial> --device-label <device-label>

# 2. Lock the plan. Offline; runs once per backup ID.
python -m backup_hard.select_formats --backup-id <backup-id> --android-media-documents

# 3. Copy. Safe to rerun; finished files are skipped.
python -m backup_hard.copy_backup --source-type android --backup-id <backup-id> --device-serial <device-serial> --destination-root "<destination-root>"

# 4. Verify
python -m backup_hard.verify_backup --source-type android --backup-id <backup-id> --device-serial <device-serial> --destination-root "<destination-root>"
```

**What is copied.** Images, video, audio, and documents from all readable
shared storage, above a minimum size: 10 KiB for images, 2 KiB for audio, 1 MiB
for video, and 1 KiB for everything else. Caches, thumbnails, trash,
`Android/data`, `Android/obb`, streaming caches, and WhatsApp sticker folders
are skipped. Private app data cannot be read over ADB. Unreadable folders are
listed in `scan_errors.csv` and the rest of the scan continues.

**How files are arranged.** Media is sorted by where it came from, its type,
and its month. Documents go in `documents/<extension>/`.

```text
media/<origin>/<images|videos|audio>/<YYYY_MM>/<file>
media/whatsapp/images/2026_01/IMG-20260109-WA0018.jpg
media/camera/images/2026_08/part_002/20260815_101010.jpg
documents/pdf/report.pdf
```

- **Origin** is the app or place, such as `whatsapp`, `telegram`, `camera`,
  `screenshots`, `downloads`, or a DCIM or Pictures subfolder name. Unmatched
  files go to `other`. Step 2's report lists the file count per origin.
- **Month** comes from a date in the file name, else the file's modified date.
- **No media folder holds more than 100 files.** A busier month is split into
  `part_001`, `part_002`, and so on, in date order.
- **Names** are kept as on the phone. Names Windows cannot store are escaped,
  and duplicates get `_1`, `_2` suffixes. The plan records every original path.

**How it is checked.** Copy and verify compare SHA-256 hashes computed on the
phone with hashes of the copied files. Verify also rescans the phone and fails
on new, changed, missing, or extra files. Folders already listed as unreadable
in Step 1 are reported but do not fail verification. Copy and verify accept
`--workers N`, which defaults to 4.

**Each backup folder belongs to one run.** Copy writes `.backup-hard/run.json`
with the backup ID, phone, and plan fingerprint. It resumes only into a folder
with a matching marker, and refuses any other non-empty folder without touching
it. Free space is checked only for files still to copy.

To redo Step 2, delete `reports/<backup-id>/step_2/` first.

## Troubleshooting

- **Verify reports changed or new files:** the source changed after Step 1.
  Rerun Step 3, then Step 4.
- **Verify reports unexpected files:** the destination holds files outside the
  plan, often from an earlier policy. Use a clean destination.
- **Copy refuses the destination:** the folder holds another run's files, such
  as a backup made with an older layout. Use a new `<backup-id>`, or move the
  old folder away.
- **Access denied:** check `scan_errors.csv`, then run with the needed
  permissions or exclude that path.

## Development

```shell
python -m unittest discover -s tests -v
```

`examples/` holds a synthetic end-to-end notebook and an Android demo that need
no real disk or phone. See [CONTRIBUTING.md](CONTRIBUTING.md), and never commit
real reports, personal paths, or device serials.

## License

Proprietary. No permission to copy, modify, or redistribute is granted.
