"""Tests for the sync-to-asyncio bridge in batcontrol.async_utils.

These cover the behaviour that broke on Python 3.14, where
asyncio.get_event_loop() raises RuntimeError instead of silently creating a
loop. The helpers must own the loop lifecycle themselves and never leak a loop.
"""

import asyncio
import gc
import warnings

import pytest

from batcontrol.async_utils import managed_event_loop, run_coroutine


def _current_loop():
    """Return the thread's current event loop, or None if there is none."""
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        return None


def _open_loops():
    """Count event loop objects that are alive and not closed."""
    gc.collect()
    return sum(
        1 for obj in gc.get_objects()
        if isinstance(obj, asyncio.AbstractEventLoop) and not obj.is_closed()
    )


class TestRunCoroutine:
    """run_coroutine() runs a coroutine from synchronous code."""

    def test_returns_result(self):
        async def work():
            await asyncio.sleep(0)
            return 42

        assert run_coroutine(work()) == 42

    def test_propagates_exception(self):
        async def work():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            run_coroutine(work())

    def test_leaves_no_current_loop_behind(self):
        async def work():
            return None

        run_coroutine(work())
        assert _current_loop() is None

    def test_does_not_leak_loops_across_calls(self):
        """The 3.14 regression: one leaked loop per call."""
        async def work():
            return 1

        run_coroutine(work())  # warm up, ignore one-off allocations
        before = _open_loops()
        for _ in range(5):
            run_coroutine(work())
        assert _open_loops() <= before

    def test_closes_loop_even_when_coroutine_raises(self):
        async def work():
            raise RuntimeError("inner")

        before = _open_loops()
        with pytest.raises(RuntimeError, match="inner"):
            run_coroutine(work())
        assert _open_loops() <= before
        assert _current_loop() is None

    def test_cancels_leftover_tasks(self):
        """A task the coroutine never awaited must not keep the loop busy."""
        leftover = {}

        async def work():
            async def forever():
                await asyncio.sleep(3600)

            leftover["task"] = asyncio.ensure_future(forever())
            await asyncio.sleep(0)
            return "done"

        assert run_coroutine(work()) == "done"
        assert leftover["task"].cancelled() or leftover["task"].done()


class TestRunCoroutineFromAsyncContext:
    """run_coroutine() must refuse to nest inside a running loop."""

    def test_raises_inside_running_loop(self):
        async def inner():
            return 1

        async def outer():
            with pytest.raises(RuntimeError, match="running event loop"):
                run_coroutine(inner())

        asyncio.run(outer())

    def test_refusal_does_not_warn_about_unawaited_coroutine(self):
        async def inner():
            return 1

        async def outer():
            coro = inner()
            with pytest.raises(RuntimeError):
                run_coroutine(coro)

        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            asyncio.run(outer())
            gc.collect()


class TestManagedEventLoop:
    """managed_event_loop() hands out a loop for repeated use."""

    def test_loop_is_usable_for_several_calls(self):
        async def work(value):
            await asyncio.sleep(0)
            return value * 2

        with managed_event_loop() as loop:
            results = [loop.run_until_complete(work(i)) for i in range(3)]

        assert results == [0, 2, 4]

    def test_loop_is_current_inside_block(self):
        with managed_event_loop() as loop:
            assert _current_loop() is loop

    def test_loop_is_closed_after_block(self):
        with managed_event_loop() as loop:
            pass

        assert loop.is_closed()
        assert _current_loop() is None

    def test_loop_is_closed_when_block_raises(self):
        with pytest.raises(ValueError, match="failed"):
            with managed_event_loop() as loop:
                raise ValueError("failed")

        assert loop.is_closed()
        assert _current_loop() is None

    def test_does_not_leak_loops_across_blocks(self):
        with managed_event_loop():
            pass
        before = _open_loops()
        for _ in range(5):
            with managed_event_loop():
                pass
        assert _open_loops() <= before

    def test_raises_inside_running_loop(self):
        async def outer():
            with pytest.raises(RuntimeError, match="running event loop"):
                with managed_event_loop():
                    pass

        asyncio.run(outer())
