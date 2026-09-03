"""Finite retry policies, separated by the external effect."""

from datetime import timedelta

from temporalio.common import RetryPolicy

READ_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=5),
    maximum_attempts=3,
)
SUBMIT_RETRY = RetryPolicy(maximum_attempts=1)


def options(*, submit: bool = False, timeout: int = 600) -> dict:
    return {
        "start_to_close_timeout": timedelta(seconds=timeout),
        "schedule_to_close_timeout": timedelta(seconds=timeout * (1 if submit else 3) + 15),
        "heartbeat_timeout": timedelta(seconds=timeout),
        "retry_policy": SUBMIT_RETRY if submit else READ_RETRY,
    }
