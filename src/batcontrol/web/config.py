""" Configuration of the read-only web API

Parses the `api:` block of the batcontrol configuration. Home Assistant form
fields may deliver strings for booleans and integers, so every value goes
through an explicit conversion.
"""
import dataclasses
import logging
import secrets

from ..value_utils import parse_bool_flag

logger = logging.getLogger(__name__)

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8080

# Path prefixes served by the application
API_PREFIX = '/api/v1'
MCP_PATH = '/mcp'

# Bind addresses that mean "this machine only": no warning about an
# unauthenticated bind for these.
LOOPBACK_HOSTS = ('127.0.0.1', 'localhost', '::1', '[::1]')


def token_matches(presented, expected: str) -> bool:
    """ Constant-time comparison of a presented bearer token.

    Compares **bytes**, not strings, on purpose: ``secrets.compare_digest``
    raises ``TypeError`` for a string containing non-ASCII characters, so a
    request with ``Authorization: Bearer <umlaut>`` would come back as a 500
    with a traceback instead of a plain 401. It fails closed either way, but
    an authentication check must reject, not crash.

    ``presented`` may be raw header bytes (the ASGI path) or the string
    Starlette decoded from them, which is latin-1 and round-trips exactly.
    """
    if not presented or not expected:
        return False
    if isinstance(presented, str):
        presented = presented.encode('latin-1', 'replace')
    return secrets.compare_digest(presented, expected.encode('utf-8'))


@dataclasses.dataclass(frozen=True)
class ApiConfig:  # pylint: disable=too-many-instance-attributes
    """ Validated `api:` configuration block.

    Two independent tokens, so the MCP token can be handed to an assistant
    without giving away access to the REST API:

      auth_token      - protects /api/v1/** (except /health)
      mcp_auth_token  - protects /mcp; falls back to auth_token when empty,
                        so securing the API never leaves MCP wide open

    An empty token means no authentication at all for that surface - there is
    deliberately no half protected state.
    """
    enabled: bool = False
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    dashboard_enabled: bool = True
    # Swagger UI (/docs) and the OpenAPI schema (/openapi.json). Off by
    # default: a running installation does not need its API described to
    # every visitor, and the schema is published with the documentation.
    docs_enabled: bool = False
    mcp_enabled: bool = False
    auth_token: str = ''
    mcp_auth_token: str = ''

    @property
    def auth_enabled(self) -> bool:
        """ True when the REST API requires a bearer token. """
        return bool(self.auth_token)

    @property
    def effective_mcp_token(self) -> str:
        """ The token /mcp requires, inheriting auth_token when unset. """
        return self.mcp_auth_token or self.auth_token

    @property
    def mcp_auth_enabled(self) -> bool:
        """ True when /mcp requires a bearer token. """
        return bool(self.effective_mcp_token)

    @property
    def mcp_token_is_separate(self) -> bool:
        """ True when MCP uses its own token rather than the API's. """
        return bool(self.mcp_auth_token) and \
            self.mcp_auth_token != self.auth_token

    def describe(self) -> str:
        """ One line summary for the startup log. Never includes a token. """
        if not self.mcp_enabled:
            mcp = 'off'
        elif not self.mcp_auth_enabled:
            mcp = 'on (auth: off)'
        elif self.mcp_token_is_separate:
            mcp = 'on (auth: own token)'
        else:
            mcp = 'on (auth: shared with api)'
        return (
            f'http://{f"[{self.host}]" if ":" in self.host else self.host}'
            f':{self.port}'
            f' (dashboard: {"on" if self.dashboard_enabled else "off"},'
            f' docs: {"on" if self.docs_enabled else "off"},'
            f' api auth: {"on" if self.auth_enabled else "off"},'
            f' mcp: {mcp})'
        )


def _unset(value) -> bool:
    """ True for a value that means "not configured".

    Home Assistant submits an empty string for an optional form field that
    was left blank, and a commented-out YAML key arrives as None. Both mean
    the same thing: use the default. Treating the empty string as a *value*
    turned a blank field into a startup failure.
    """
    return value is None or (isinstance(value, str) and not value.strip())


def _as_bool(value, key: str, default: bool) -> bool:
    if _unset(value):
        return default
    try:
        return parse_bool_flag(value)
    except ValueError as exc:
        raise ValueError(f'api.{key} must be true or false, got {value!r}') from exc


def _as_port(value) -> int:
    if _unset(value):
        return DEFAULT_PORT
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'api.port must be a number, got {value!r}') from exc
    if not 1 <= port <= 65535:
        raise ValueError(f'api.port must be between 1 and 65535, got {port}')
    return port


def _as_host(value) -> str:
    if _unset(value):
        return DEFAULT_HOST
    host = str(value).strip()
    # '[::1]' is URL notation; uvicorn needs the bare address to bind it.
    if host.startswith('[') and host.endswith(']'):
        host = host[1:-1]
    return host


def from_config(configdict: dict) -> ApiConfig:
    """ Build an ApiConfig from the batcontrol configuration dictionary.

    A missing `api:` block yields a disabled API, which is the default for
    every existing installation.
    """
    section = configdict.get('api') or {}
    if not isinstance(section, dict):
        raise ValueError(
            f'api must be a configuration block, got {type(section).__name__}')

    config = ApiConfig(
        enabled=_as_bool(section.get('enabled'), 'enabled', False),
        host=_as_host(section.get('host')),
        port=_as_port(section.get('port')),
        dashboard_enabled=_as_bool(
            section.get('dashboard_enabled'), 'dashboard_enabled', True),
        docs_enabled=_as_bool(
            section.get('docs_enabled'), 'docs_enabled', False),
        mcp_enabled=_as_bool(section.get('mcp_enabled'), 'mcp_enabled', False),
        auth_token=str(section.get('auth_token') or '').strip(),
        mcp_auth_token=str(section.get('mcp_auth_token') or '').strip(),
    )

    if config.enabled and config.host not in LOOPBACK_HOSTS:
        if not config.auth_enabled:
            logger.warning(
                'API is bound to %s without an auth_token. Anyone on that'
                ' network can read your battery, price and consumption data.'
                ' Set api.auth_token or put a reverse proxy in front of it.',
                config.host
            )
        if config.mcp_enabled and not config.mcp_auth_enabled:
            logger.warning(
                'MCP is bound to %s without a token. Set api.mcp_auth_token'
                ' (or api.auth_token, which it inherits).', config.host
            )

    return config
