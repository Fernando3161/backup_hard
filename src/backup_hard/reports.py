"""Deterministic CSV, JSON, and Markdown exports from SQLite state."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path

REPORT_TABLES = ("runs", "inventory_files", "classifications", "destination_mappings",
                 "copy_results", "verification_results", "errors")


def export_reports(database: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    payload: dict[str, list[dict[str, object]]] = {}
    for table in REPORT_TABLES:
        rows = [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
        payload[table] = rows
        with (output / f"{table}.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            fields = list(rows[0]) if rows else []
            writer = csv.DictWriter(handle, fieldnames=fields)
            if fields:
                writer.writeheader()
                writer.writerows(rows)
    connection.close()
    (output / "report.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Backup report", ""] + [f"- {name}: {len(rows)}" for name, rows in payload.items()]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("output", type=Path)
    options = parser.parse_args(argv)
    export_reports(options.database, options.output)
    return 0
