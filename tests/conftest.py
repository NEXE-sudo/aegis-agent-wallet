"""Shared test isolation for process-local API middleware state."""

import pytest

from app import main


@pytest.fixture(autouse=True)
def isolate_process_local_rate_limit_state():
    """Prevent one test client's write requests from throttling unrelated tests."""
    with main._rate_lock:
        main._rate_windows.clear()
    yield
    with main._rate_lock:
        main._rate_windows.clear()
