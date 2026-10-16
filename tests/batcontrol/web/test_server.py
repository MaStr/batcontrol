"""The uvicorn thread that hosts the API.

This is the one part of the web layer a route test cannot reach: thread
start-up, shutdown, and the promise that a failing web server never takes the
control loop with it. It is therefore exercised against a real socket rather
than a mock.
"""
import socket
import threading
import time

import pytest

from batcontrol.web.config import ApiConfig
from batcontrol.web.server import WebServer

# Generous: a cold uvicorn start on a loaded CI runner is not instant.
STARTUP_TIMEOUT = 20.0


def free_port() -> int:
    """An ephemeral port the OS just confirmed is free."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def wait_for(predicate, timeout=STARTUP_TIMEOUT):
    """Poll until predicate() is truthy, or give up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture(name='running')
def fixture_running(fake_bc):
    """A WebServer actually listening on a free port."""
    config = ApiConfig(enabled=True, host='127.0.0.1', port=free_port())
    server = WebServer(fake_bc, config)
    server.start()
    try:
        yield server, config
    finally:
        server.shutdown()


def test_it_serves_requests(running):
    import httpx  # pylint: disable=import-outside-toplevel

    _server, config = running
    url = f'http://{config.host}:{config.port}/api/v1/health'

    def reachable():
        try:
            return httpx.get(url, timeout=1.0).status_code == 200
        except httpx.HTTPError:
            return False

    assert wait_for(reachable), 'the server never started listening'
    body = httpx.get(url, timeout=5.0).json()
    assert body['data']['status'] == 'ok'


def test_it_runs_as_a_daemon_thread(running):
    """A hung web thread must never keep the process alive."""
    server, _config = running
    thread = next(t for t in threading.enumerate() if t.name == 'batcontrol-web')
    assert thread.daemon is True
    assert server.app is not None


def test_shutdown_stops_the_thread(fake_bc):
    config = ApiConfig(enabled=True, host='127.0.0.1', port=free_port())
    server = WebServer(fake_bc, config)
    server.start()
    assert wait_for(lambda: any(
        t.name == 'batcontrol-web' for t in threading.enumerate()))

    server.shutdown()

    assert wait_for(lambda: not any(
        t.name == 'batcontrol-web' for t in threading.enumerate()))


def test_shutdown_without_start_is_a_no_op(fake_bc):
    WebServer(fake_bc, ApiConfig()).shutdown()


def test_starting_twice_is_refused_not_duplicated(fake_bc, caplog):
    config = ApiConfig(enabled=True, host='127.0.0.1', port=free_port())
    server = WebServer(fake_bc, config)
    server.start()
    try:
        with caplog.at_level('WARNING'):
            assert server.start() is True
        assert 'already started' in caplog.text
        threads = [t for t in threading.enumerate() if t.name == 'batcontrol-web']
        assert len(threads) == 1
    finally:
        server.shutdown()


def test_an_occupied_port_is_reported_not_hidden(fake_bc, caplog):
    """Regression: uvicorn sys.exit()s on a bind error. SystemExit slipped
    past the thread's except clause, the thread died silently and
    "listening" had already been logged."""
    with socket.socket() as blocker:
        blocker.bind(('127.0.0.1', 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        server = WebServer(fake_bc, ApiConfig(enabled=True, port=port))
        with caplog.at_level('INFO'):
            assert server.start() is False
    assert 'could not be started' in caplog.text
    assert 'Web API listening' not in caplog.text
    assert not [t for t in threading.enumerate() if t.name == 'batcontrol-web']



def test_the_startup_log_never_contains_the_token(fake_bc, caplog):
    config = ApiConfig(enabled=True, host='127.0.0.1', port=free_port(),
                       auth_token='super-secret', mcp_auth_token='also-secret')
    server = WebServer(fake_bc, config)
    with caplog.at_level('INFO'):
        server.start()
    try:
        assert 'super-secret' not in caplog.text
        assert 'also-secret' not in caplog.text
        assert 'Web API listening' in caplog.text
    finally:
        server.shutdown()
