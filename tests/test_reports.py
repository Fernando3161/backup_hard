import json

from backup_hard.manifest import Manifest
from backup_hard.models import SourceIdentity
from backup_hard.reports import export_reports


def test_exports_csv_json_and_markdown(tmp_path):
    database = tmp_path / "state.sqlite3"
    with Manifest(database) as manifest:
        manifest.create_run("id", "test", SourceIdentity("filesystem", "disk", "Disk"))
    output = tmp_path / "reports"
    export_reports(database, output)
    assert (output / "runs.csv").exists()
    assert json.loads((output / "report.json").read_text())["runs"][0]["backup_id"] == "id"
    assert "runs: 1" in (output / "report.md").read_text()
