from backup_hard.exit_codes import INTERRUPTED, OPERATIONAL_FAILURE, SUCCESS, for_results


def test_exit_codes_reflect_outcomes():
    assert for_results(0) == SUCCESS
    assert for_results(1) == OPERATIONAL_FAILURE
    assert for_results(0, interrupted=True) == INTERRUPTED
