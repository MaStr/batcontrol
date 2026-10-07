""" Helpers for running asyncio coroutines from synchronous code.

batcontrol is a synchronous application, but a few providers talk to
HomeAssistant over a WebSocket and are therefore implemented as coroutines.
Those providers need a bridge from sync code into asyncio.

Historically that bridge was ``asyncio.get_event_loop()`` with a
``RuntimeError`` fallback that created a loop and never closed it. Up to
Python 3.13 ``get_event_loop()`` silently created a loop when none was set;
since Python 3.14 it raises ``RuntimeError`` instead, so the fallback ran on
every call and left a loop registered as the thread's current loop.

The helpers here delegate the loop lifecycle to ``asyncio.Runner``, which
cancels leftover tasks, flushes async generators and closes the loop on exit.
"""

import asyncio
import contextlib
import logging
from typing import Iterator

logger = logging.getLogger(__name__)


def _assert_no_running_loop(context: str) -> None:
    """Raise RuntimeError if the calling thread already runs an event loop.

    Args:
        context: Name used in the error message, usually the caller.

    Raises:
        RuntimeError: If an event loop is already running in this thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Expected case: no running loop, so we are free to create one.
        return

    raise RuntimeError(
        f"{context} cannot be used from a running event loop; "
        "await the coroutine directly instead"
    )


@contextlib.contextmanager
def managed_event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """Provide a private event loop for several run_until_complete() calls.

    Use this when a single connection has to be reused across multiple
    coroutine invocations. The loop is closed when the block exits, also on
    error.

    Yields:
        A fresh event loop, set as the current loop for this thread.

    Raises:
        RuntimeError: If an event loop is already running in this thread.
    """
    _assert_no_running_loop("managed_event_loop()")

    with asyncio.Runner() as runner:
        loop = runner.get_loop()
        # Register the loop so libraries that call get_event_loop() outside of
        # a running loop still find it. Runner.close() resets this.
        asyncio.set_event_loop(loop)
        try:
            yield loop
        finally:
            asyncio.set_event_loop(None)


def run_coroutine(coro):
    """Run a single coroutine from synchronous code and return its result.

    Args:
        coro: The coroutine to run.

    Returns:
        Whatever the coroutine returns.

    Raises:
        RuntimeError: If an event loop is already running in this thread. The
            coroutine is closed in that case, so it does not trigger a
            "coroutine was never awaited" warning.
    """
    try:
        _assert_no_running_loop("run_coroutine()")
    except RuntimeError:
        coro.close()
        raise

    with managed_event_loop() as loop:
        return loop.run_until_complete(coro)
