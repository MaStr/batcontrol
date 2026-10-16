""" Optional bearer token authentication

The API is read-only, but its payload still describes a household: battery
level, consumption pattern, when nobody is home. Default is bind to localhost
and no token. A deployment that binds to the network can set
``api.auth_token``.

There is no half protected state: with an empty token the dependency is not
installed at all, so there is no code path that could accept a request for the
wrong reason. ``/api/v1/health`` always stays open, because container health
checks need it.

The MCP endpoint is a raw ASGI route outside FastAPI's dependency system, so
it gets the ASGI wrapper ``bearer_guard`` instead - same comparison, its own
token.
"""
import logging
from typing import List, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import ApiConfig, token_matches

logger = logging.getLogger(__name__)

BEARER_SCHEME = 'bearer'

# auto_error=False so a missing header produces our own 401 with a
# WWW-Authenticate header rather than FastAPI's 403.
_bearer = HTTPBearer(auto_error=False, scheme_name='Bearer token',
                     description='Value of api.auth_token.')


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={'WWW-Authenticate': 'Bearer'},
    )


def auth_dependencies(config: ApiConfig) -> List:
    """ The dependency list to put on the protected routers.

    Empty when no token is configured.
    """
    if not config.auth_enabled:
        return []

    def require_token(
            credentials: Optional[HTTPAuthorizationCredentials] =
            Depends(_bearer)) -> None:
        """ Verify the bearer token in constant time. """
        if credentials is None:
            raise _unauthorized('Missing bearer token.')
        if credentials.scheme.lower() != BEARER_SCHEME:
            raise _unauthorized('Expected an "Authorization: Bearer" header.')
        # Constant-time, and byte-based so a non-ASCII token is rejected
        # rather than raising; the token is never logged.
        if not token_matches(credentials.credentials, config.auth_token):
            raise _unauthorized('Invalid bearer token.')

    return [Depends(require_token)]


def bearer_guard(asgi_app, token: str):
    """ Wrap an ASGI app so it requires `Authorization: Bearer <token>`.

    The MCP endpoint is attached to the FastAPI router as a raw ASGI route,
    so FastAPI's dependency-based auth does not apply to it. A small ASGI
    wrapper keeps the check in one obvious place and lets MCP carry its own
    token, independent of the REST API's.

    An empty token means the wrapper is not installed at all; see app.py.
    """
    async def guarded(scope, receive, send):
        header = b''
        for key, value in scope.get('headers') or ():
            if key.lower() == b'authorization':
                header = value
                break
        # Split on the raw bytes: a token is compared as bytes, so there is
        # no decode step that could fail or normalise it into a match.
        parts = header.split(None, 1)
        presented = parts[1].strip() if len(parts) == 2 and \
            parts[0].lower() == b'bearer' else b''
        # Constant-time, and a wrong scheme costs the same as a wrong token;
        # the token itself is never logged.
        if not token_matches(presented, token):
            await send({
                'type': 'http.response.start',
                'status': 401,
                'headers': [
                    (b'content-type', b'application/json'),
                    (b'www-authenticate', b'Bearer'),
                ],
            })
            await send({
                'type': 'http.response.body',
                'body': b'{"error":"unauthorized",'
                        b'"detail":"Missing or invalid bearer token for /mcp."}',
            })
            return
        await asgi_app(scope, receive, send)

    return guarded
