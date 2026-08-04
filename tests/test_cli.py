from backup_hard.cli import build_parser


def test_unified_cli_exposes_workflow_commands():
    assert build_parser().parse_args(["copy", "--help"]).command == "copy"


def test_legacy_entry_points_route_to_unified_cli():
    import backup_hard.cli as cli
    assert callable(cli.inventory_main) and callable(cli.verify_main)
