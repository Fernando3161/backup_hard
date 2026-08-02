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

## Example notebook

`examples/backup_workflow.ipynb` runs all four stages against synthetic files.
It writes only to a unique directory beneath the ignored
`examples/example-output/` tree and does not use local disks or existing reports.

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
