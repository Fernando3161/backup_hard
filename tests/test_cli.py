from backup_hard.cli import build_parser


def test_unified_cli_exposes_workflow_commands():
    assert build_parser().parse_args(["copy", "--help"]).command == "copy"
