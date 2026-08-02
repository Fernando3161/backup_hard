# Backup Hard

Backup Hard is a dependency-free Python application for staged, resumable, and
independently verified filesystem backups. It inventories a source tree, locks
a reviewable format policy, copies selected files while preserving relative
paths, and validates the result with fresh source and destination scans.

> This is a file-level backup tool, not a bootable disk-imaging solution.

## Features

- Four auditable stages: inventory, policy locking, copy, and verification.
- Stable `source_label_YYYYMMDD` backup IDs.
- Source-relative directory preservation for every selected file.
- Atomic file replacement, retries, and safe resumability.
- CSV, JSON, and Markdown reports grouped under `reports/<backup-id>/step_N`.
- Full-format and strict OS media/document policies.
- Android shared-storage media/document backups over ADB.
- OS path exclusions and content-specific minimum file sizes.
- Standard-library-only runtime; Windows long-path handling is included.

## Repository structure

```text
.
├── .github/workflows/ci.yml     # automated checks
├── examples/backup_workflow.ipynb
├── src/backup_hard/             # application modules
├── tests/                       # unittest suite and notebook checks
├── pyproject.toml               # packaging and CLI entry points
└── reports/                     # generated locally; ignored by Git
```

## Prerequisites and installation

- Python 3.9 or newer.
- Read access to the source tree.
- A separate destination with enough free space.

From the repository root:

```shell
python -m pip install --upgrade pip
python -m pip install -e .
```

No runtime packages beyond Python's standard library are required.

## Android prerequisites and scope

Install Google's Android SDK Platform Tools so `adb` is on `PATH`. Enable
Developer options and USB debugging on the phone, connect through USB or
configured wireless ADB, unlock the phone, and accept its authorization prompt.

```shell
adb devices -l
```

The ADB serial is the stable identity. This distinguishes two phones with the
same model; give each serial a readable label such as `nokia_x10_1` and
`nokia_x10_2`. Labels, serials, and destination paths are not stored in tracked
configuration.

Android mode scans every accessible shared-storage volume rather than an app
whitelist. Internal storage is `internal`; removable storage is `sd_card` (or
`sd_card_<short-id>` for multiple cards). Storage aliases are deduplicated.
Eligible media under `Android/media`, including files from messaging, social,
and editing apps, is included based on its file type.

Supported types cover common/modern images, video, audio, office documents,
text, and e-books. Matching is case-insensitive. Exact minimums are:

- All files: 1,024 bytes.
- Images: 10,240 bytes.
- Audio: 2,048 bytes.
- Video: 1,048,576 bytes.
- Documents: 1,024 bytes.

Files exactly at a threshold are included. Android mode excludes unsupported
types, temporary/incomplete downloads, cache/thumbnail/trash directories,
`Android/data`, `Android/obb`, package/expansion data, and known offline
streaming caches. It never uses root, installs software, deletes source data,
or prunes destinations.

Android scoped storage limits what ADB can read. Private app data, Samsung
Secure Folder content, protected system data, and other inaccessible content
cannot be backed up without elevated access and are outside this tool's scope.

## Configuration

A backup ID must contain a lowercase source label and an eight-digit date, for
example `archive_disk_20260801`. Commands default to `./reports`; use
`--reports-root` to place generated reports elsewhere.

The destination is derived automatically:

```text
<destination-root>/backup_<backup-id>/
```

For an OS media/document policy, `--exclude-relative-prefix` may be repeated to
exclude source-relative application or report trees that are specific to your
machine. Use forward slashes or your platform's normal path separator.

## Basic workflow

The examples below use repository-relative sample locations. Create the source
and destination-root directories before running them.

### 1. Inventory

```shell
python -m backup_hard.inventory --backup-id archive_disk_20260801 --source ./example-source
```

Review `reports/archive_disk_20260801/step_1/scan_errors.csv` before continuing.

### 2. Lock formats

Include all inventoried extensions except explicit temporary exclusions:

```shell
python -m backup_hard.select_formats --backup-id archive_disk_20260801 --lock-all
```

For an OS disk containing only media and documents:

```shell
python -m backup_hard.select_formats --backup-id system_media_20260801 --os-media-documents --exclude-relative-prefix path/to/generated/reports
```

The OS policy excludes common system, program, temporary, log, Anaconda, and
gaming directories. Its minimums are 1 KiB globally, 10 KiB for images/audio,
and 1 MiB for documents/videos.

### 3. Copy

```shell
python -m backup_hard.copy_backup --backup-id archive_disk_20260801 --source ./example-source --destination-root ./example-output --retries 3
```

Rerunning the same command is safe. Files with matching size and modification
time are reported as already present.

### 4. Verify

```shell
python -m backup_hard.verify_backup --backup-id archive_disk_20260801 --source ./example-source --destination-root ./example-output
```

A zero exit status means the fresh scans, locked policy, plan, and destination
agree. Review every nonzero result in `step_4/validation_discrepancies.csv`.

## Android four-stage workflow

Repeat all four steps with a unique backup ID for each phone. Step 1 inventories
without copying:

```shell
python -m backup_hard.inventory --source-type android \
  --backup-id nokia_x10_1_20260802 --device-serial <adb-serial> \
  --device-label nokia_x10_1
```

Review Step 1, then lock the immutable file plan and deterministic document
collision names:

```shell
python -m backup_hard.select_formats \
  --backup-id nokia_x10_1_20260802 --android-media-documents
```

Copy to a user-selected root:

```shell
python -m backup_hard.copy_backup --source-type android \
  --backup-id nokia_x10_1_20260802 --device-serial <adb-serial> \
  --destination-root <destination-root> --retries 3
```

The destination is `<destination-root>/nokia_x10_1_20260802/` with `media/`,
`documents/`, and `metadata/`. Android media uses exactly one app-aware folder
below its volume: `media/<volume>/<folder>/<file>`. For example, WhatsApp images
map to `media/internal/WhatsAppImages/`. Distinct source folders that request the
same readable name receive deterministic `_1`, `_2`, and later suffixes. Media
filename collisions are similarly suffixed before the extension. Documents are
flattened by normalized extension. All mappings are frozen in Step 2, so reruns
reuse the same names; unrelated files are never overwritten.

```shell
python -m backup_hard.verify_backup --source-type android \
  --backup-id nokia_x10_1_20260802 --device-serial <adb-serial> \
  --destination-root <destination-root>
```

Step 4 performs a fresh full scan and fresh source-to-destination SHA-256 checks.
It fails nonzero for inaccessible/new/changed sources, missing or unexpected
destinations, size differences, or hash differences, and modifies neither side.

## Example notebook

`examples/backup_workflow.ipynb` runs all four stages against synthetic files.
It writes only to a unique directory beneath the ignored
`examples/example-output/` tree and does not use local disks or existing reports.

`examples/android_mock_workflow.py` demonstrates Android classification and
exclusion decisions using synthetic paths and needs neither ADB nor a phone.

## Testing and validation

```shell
python -m compileall -q src tests
python -m unittest discover -s tests -v
python -m backup_hard.inventory --help
python -m backup_hard.select_formats --help
python -m backup_hard.copy_backup --help
python -m backup_hard.verify_backup --help
```

CI runs these checks on supported Python versions and executes the notebook
through the standard-library notebook test harness.

## Troubleshooting

- **Access denied:** review `scan_errors.csv`. Run with appropriate permissions,
  or explicitly exclude paths that are outside the desired backup scope.
- **Insufficient space:** Step 3 checks free space before creating the backup.
- **Validation changed after copying:** active source files may have changed or
  new matching files may have appeared. Rerun Step 3, then Step 4.
- **Unexpected destination files:** use a clean destination for a changed policy;
  resumable copying does not automatically delete files excluded later.
- **Reparse points skipped:** directory links are deliberately not traversed to
  avoid loops and crossing into unrelated volumes.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Do not commit real backup reports,
personal paths, credentials, or user data. Changes to copy or verification logic
should include focused tests.

## License

No open-source license has been selected. The repository is currently marked
proprietary; no permission to copy, modify, or redistribute is granted. Select
and add an explicit license before publishing for third-party reuse.
