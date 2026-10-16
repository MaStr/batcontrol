""" Hosting the read-only web API

The server runs uvicorn in a daemon thread next to the control loop, the same
pattern MqttApi uses. The control loop stays the owner of the process: if the
HTTP server fails to start, batcontrol logs the failure and keeps controlling
the battery.
"""
import asyncio
import logging
import threading
import time
from typing import Optional

import uvicorn

from .app import create_app
from .config import ApiConfig

logger = logging.getLogger(__name__)

# Time granted to the event loop to shut down before the process moves on.
SHUTDOWN_TIMEOUT = 5.0
# Open streams (an MCP client's GET stream never ends on its own) are cut
# after this long, so shutdown fits into SHUTDOWN_TIMEOUT and the lifespan
# shutdown still runs.
GRACEFUL_SHUTDOWN_TIMEOUT = 2
# How long start() waits for the socket to be bound before giving up.
STARTUP_TIMEOUT = 10.0


class WebServer:
    """ uvicorn in a daemon thread, serving the read-only API.

    The constructor only prepares; nothing listens until start() is called.
    """

    def __init__(self, batcontrol, config: ApiConfig):
        self._config = config
        self._thread: Optional[threading.Thread] = None
        self._server = None
        self._app = create_app(batcontrol, config)

    @property
    def app(self):
        """ The ASGI application, exposed for tests. """
        return self._app

    def start(self) -> bool:
        """ Start serving in a background thread.

        Waits until uvicorn has bound the socket and run the lifespan
        startup. Returns False (after logging why) when it did not, e.g. on
        an occupied port.
        """
        if self._thread is not None:
            logger.warning('Web API already started')
            return True

        # log_config=None keeps uvicorn from replacing batcontrol's logging
        # setup; its loggers then propagate into the batcontrol log file.
        uvicorn_config = uvicorn.Config(
            self._app,
            host=self._config.host,
            port=self._config.port,
            log_config=None,
            access_log=False,
            # The control loop owns the process lifetime.
            lifespan='on',
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_TIMEOUT,
        )
        self._server = uvicorn.Server(uvicorn_config)
        # uvicorn installs signal handlers only on the main thread; it detects
        # the thread itself, but be explicit about not touching them.
        self._server.install_signal_handlers = lambda: None

        self._thread = threading.Thread(
            target=self._serve,
            name='batcontrol-web',
            daemon=True,
        )
        self._thread.start()
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while not self._server.started and self._thread.is_alive() \
                and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            logger.error(
                'Web API could not be started on %s (see the uvicorn error'
                ' above). Battery control continues without it.',
                self._config.describe())
            self.shutdown()
            return False
        logger.info('Web API listening on %s', self._config.describe())
        return True

    def _serve(self) -> None:
        try:
            asyncio.run(self._server.serve())
        except BaseException as exc:  # pylint: disable=broad-exception-caught
            # uvicorn calls sys.exit(1) on a bind or lifespan failure, which
            # is a SystemExit, not an Exception. A failing web API must never
            # take down battery control, so catch that as well.
            logger.error('Web API stopped: %r', exc,
                         exc_info=not isinstance(exc, SystemExit))

    def shutdown(self) -> None:
        """ Ask the server to stop and wait briefly for the thread. """
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=SHUTDOWN_TIMEOUT)
            if self._thread.is_alive():
                logger.warning(
                    'Web API thread did not stop within %.0f s', SHUTDOWN_TIMEOUT)
        self._thread = None
        self._server = None
