"""Stable process exit codes for automation."""

SUCCESS = 0
OPERATIONAL_FAILURE = 1
USAGE_ERROR = 2
INTERRUPTED = 130


def for_results(failures: int, interrupted: bool = False) -> int:
    if interrupted:
        return INTERRUPTED
    return OPERATIONAL_FAILURE if failures else SUCCESS
