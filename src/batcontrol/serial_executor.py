""" Serial executor for externally triggered commands.

paho-mqtt runs every message callback on its single network thread. A
callback that waits there - for example for batcontrol's control lock while
a control cycle talks to a slow inverter - stalls the whole client: no
keepalive, no other message, and the MQTT inverter's SOC messages, which
arrive on the same client, are not delivered either. A control cycle that
waits for such a SOC message while it holds the control lock would then wait
for a thread that waits for it.

SerialExecutor takes the work off the network thread. Callbacks are queued
and run one after another, in arrival order, on one worker thread, which
takes the given lock around each call. The network thread only enqueues and
returns at once.
"""
import logging
import queue
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_STOP = object()


class SerialExecutor:
    """ Runs submitted calls one at a time on a dedicated daemon thread.

    Calls run in submission order. When a lock is given, every call runs
    while holding it. An exception raised by a call is logged and does not
    stop the worker.
    """

    def __init__(self, name: str, lock=None):
        self._name = name
        self._lock = lock
        self._queue: queue.Queue = queue.Queue()
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._worker, name=name, daemon=True)
        self._thread.start()

    def submit(self, func: Callable, *args) -> bool:
        """ Queue func(*args). Returns False if the executor is stopped. """
        if self._stopped.is_set():
            logger.debug('%s is stopped, dropping %r', self._name, func)
            return False
        self._queue.put((func, args))
        return True

    def shutdown(self, timeout: Optional[float] = 5.0) -> None:
        """ Stop accepting calls, drop the queued ones and wait for the
            running call to finish (at most timeout seconds). """
        if self._stopped.is_set():
            return
        self._stopped.set()
        dropped = 0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            dropped += 1
        if dropped:
            logger.info('%s: dropped %d pending command(s) on shutdown',
                        self._name, dropped)
        self._queue.put((_STOP, ()))
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout)

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        """ Wait until every call queued so far has run. For tests. """
        done = threading.Event()
        if not self.submit(done.set):
            return False
        return done.wait(timeout)

    def _worker(self) -> None:
        while True:
            func, args = self._queue.get()
            if func is _STOP:
                return
            if self._stopped.is_set():
                # Queued before shutdown() drained the queue, skip it
                continue
            try:
                if self._lock is not None:
                    with self._lock:
                        func(*args)
                else:
                    func(*args)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception('%s: command %r failed', self._name, func)
