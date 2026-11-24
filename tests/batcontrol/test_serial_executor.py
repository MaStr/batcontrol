"""Tests for SerialExecutor (worker for MQTT set callbacks)."""
import threading

from batcontrol.serial_executor import SerialExecutor

TIMEOUT = 5


def test_runs_calls_in_submission_order():
    executor = SerialExecutor('test')
    seen = []
    try:
        for i in range(20):
            executor.submit(seen.append, i)
        assert executor.wait_idle(TIMEOUT)
    finally:
        executor.shutdown()
    assert seen == list(range(20))


def test_runs_calls_on_worker_thread_under_the_lock():
    lock = threading.RLock()
    executor = SerialExecutor('test', lock=lock)
    seen = {}

    def probe():
        seen['thread'] = threading.current_thread().name
        # RLock: the worker owns it, so it can re-acquire without blocking
        seen['owned'] = lock.acquire(blocking=False)
        if seen['owned']:
            lock.release()

    try:
        executor.submit(probe)
        assert executor.wait_idle(TIMEOUT)
    finally:
        executor.shutdown()
    assert seen['thread'] == 'test'
    assert seen['owned']


def test_submit_does_not_block_while_lock_is_held():
    """The submitting (network) thread returns at once; the call waits for
    the lock on the worker."""
    lock = threading.RLock()
    executor = SerialExecutor('test', lock=lock)
    done = threading.Event()
    try:
        with lock:
            assert executor.submit(done.set)
            assert not done.wait(0.2)
        assert done.wait(TIMEOUT)
    finally:
        executor.shutdown()


def test_failing_call_does_not_stop_the_worker(caplog):
    executor = SerialExecutor('test')
    seen = []

    def fail():
        raise RuntimeError('boom')

    try:
        executor.submit(fail)
        executor.submit(seen.append, 'after')
        assert executor.wait_idle(TIMEOUT)
    finally:
        executor.shutdown()
    assert seen == ['after']
    assert 'boom' in caplog.text


def test_shutdown_drops_pending_and_rejects_new_calls():
    """shutdown() waits for the running call, drops the queued ones and
    refuses new ones."""
    executor = SerialExecutor('test')
    started = threading.Event()
    release = threading.Event()
    seen = []

    def first():
        started.set()
        release.wait(TIMEOUT)
        seen.append('first')

    executor.submit(first)
    assert started.wait(TIMEOUT)
    executor.submit(seen.append, 'pending')
    stopper = threading.Thread(
        target=executor.shutdown, kwargs={'timeout': TIMEOUT})
    stopper.start()
    stopper.join(0.2)
    assert stopper.is_alive()  # waits for the running call

    release.set()
    stopper.join(TIMEOUT)
    assert not stopper.is_alive()
    assert seen == ['first']
    assert not executor.submit(seen.append, 'late')
    assert not executor.wait_idle(0.1)
    assert seen == ['first']


def test_shutdown_twice_is_harmless():
    executor = SerialExecutor('test')
    executor.shutdown()
    executor.shutdown()
