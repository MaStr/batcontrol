""" FastAPI application factory

Assembles the read-only API: routes, error mapping, OpenAPI metadata and -
added in later steps - the dashboard's static files and the MCP endpoint.
"""
import contextlib
import logging
import pathlib
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __pkginfo__
from .auth import auth_dependencies
from .config import API_PREFIX, MCP_PATH, ApiConfig
from .routes import (
    TAG_CONFIG, TAG_DECISION, TAG_FORECAST, TAG_META, TAG_STATUS,
    error_envelope, public_router, router,
)
from .service import BatcontrolService, NoDataAvailable, NotFound

logger = logging.getLogger(__name__)

DESCRIPTION = """
Read-only access to a running batcontrol instance: control state, battery,
price/solar/consumption forecasts and the decision trace that explains why
batcontrol chose the current inverter mode.

**This API never changes anything.** Every endpoint is a GET. Runtime
overrides stay on the MQTT API.

Conventions:

* Timestamps are epoch **milliseconds** (the MQTT API uses seconds).
* Energy is **Wh per forecast slot**; energy series also carry `power_w`, the
  average power over the slot. At 15-minute resolution those differ by a
  factor of four.
* Prices are EUR/kWh with 4 decimals.
* Forecast slot 0 is the interval containing the current time. Slots that
  have passed since the last control cycle are not returned.
"""

TAGS_METADATA = [
    {'name': TAG_META, 'description': 'Version, health and runtime information.'},
    {'name': TAG_STATUS, 'description': 'What batcontrol is doing right now.'},
    {'name': TAG_FORECAST, 'description': 'Price, solar and consumption forecasts.'},
    {'name': TAG_DECISION, 'description': 'Why batcontrol decided what it did.'},
    {'name': TAG_CONFIG, 'description': 'Runtime parameters. Never credentials.'},
]


STATIC_DIR = pathlib.Path(__file__).parent / 'static'

# Shown instead of the dashboard when the static assets are absent. That is a
# normal state for a source install: the assets are produced by the npm build
# and travel inside the wheel, so a clone without Node has none. A readable
# page beats a stack trace or a bare 404.
MISSING_DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>batcontrol</title>
<style>body{background:#030a05;color:#33ff66;font-family:ui-monospace,monospace;
padding:2rem;line-height:1.6}code{color:#ffcc33}a{color:#33ff66}</style></head>
<body>
<h1>batcontrol</h1>
<p>The API is running, but the dashboard assets are not installed.</p>
<p>They are built by npm and shipped inside the wheel. For a source checkout:</p>
<p><code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code></p>
<p>The REST API at <code>api/v1/</code> is unaffected.</p>
</body></html>
"""


def _error(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code,
                        content=error_envelope(code, detail))


def create_app(batcontrol, config: Optional[ApiConfig] = None) -> FastAPI:
    """ Build the ASGI application for a live Batcontrol instance. """
    config = config or ApiConfig()
    service = BatcontrolService(batcontrol)

    # The MCP app is a Starlette application with its own lifespan, which
    # runs the MCP session manager. Mounting an ASGI app does NOT run the
    # mounted app's lifespan, so it has to be entered explicitly below.
    mcp_routes, mcp_app = ([], None)
    if config.mcp_enabled:
        mcp_routes, mcp_app = _build_mcp(service, config)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with contextlib.AsyncExitStack() as stack:
            if mcp_app is not None:
                try:
                    await stack.enter_async_context(
                        mcp_app.router.lifespan_context(mcp_app))
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    # Without its session manager /mcp cannot answer, but
                    # REST and the dashboard must still come up.
                    logger.error('MCP server could not be started (%s). The'
                                 ' REST API and dashboard are unaffected.',
                                 exc, exc_info=True)
            yield

    app = FastAPI(
        lifespan=lifespan,
        title='batcontrol',
        summary='Read-only API of a running batcontrol instance',
        description=DESCRIPTION,
        version=__pkginfo__.__version__,
        openapi_tags=TAGS_METADATA,
        # /docs is registered below so it can use the self-hosted Swagger UI
        # assets. ReDoc is not offered: it would be a second documentation UI
        # loading a second CDN bundle. Both /docs and the schema are only
        # served with api.docs_enabled; app.openapi() works either way, which
        # is what scripts/dump_openapi.py uses.
        openapi_url='/openapi.json' if config.docs_enabled else None,
        docs_url=None,
        redoc_url=None,
    )
    if config.docs_enabled:
        _add_docs(app, config)
    app.state.service = service
    # No Host header check anywhere (maintainer decision): a reverse proxy
    # forwards its own host name, and rejecting it would break a setup the
    # docs describe. api.auth_token is the protection - see the security
    # section of docs/integrations/rest-api.md.

    @app.exception_handler(NoDataAvailable)
    async def _no_data(_request: Request, exc: NoDataAvailable) -> JSONResponse:
        return _error(503, exc.code, str(exc))

    @app.exception_handler(NotFound)
    async def _not_found(_request: Request, exc: NotFound) -> JSONResponse:
        return _error(404, 'not_found', str(exc))

    # /health stays reachable without a token; everything else is behind the
    # optional bearer dependency (empty list when no token is configured).
    app.include_router(public_router, prefix=API_PREFIX)
    app.include_router(router, prefix=API_PREFIX,
                       dependencies=auth_dependencies(config))

    # Appended after the API routes so MCP can never shadow them.
    for route in mcp_routes:
        app.router.routes.append(route)
    if mcp_routes:
        logger.info('MCP server available at %s', MCP_PATH)

    # The dashboard is mounted last, at the root: routes are matched in
    # registration order, so everything above keeps precedence over it.
    if config.dashboard_enabled:
        _mount_dashboard(app)
    return app


def _add_docs(app: FastAPI, config: ApiConfig) -> None:
    """ Serve Swagger UI at /docs, from bundled assets where possible.

    FastAPI's default points at cdn.jsdelivr.net, which leaves the API
    documentation blank on a Home Assistant box without internet egress - the
    same reason the dashboard's fonts are bundled. The npm build copies the
    two files it needs into the static assets.

    The asset URLs are relative, so they resolve correctly at /, under a Home
    Assistant ingress token path and under a reverse-proxy subpath alike. If
    the assets are missing - a source checkout without a build - the CDN URLs
    are used instead, so /docs still works for a developer with internet.
    """
    swagger_dir = STATIC_DIR / 'swagger'
    bundled = (swagger_dir / 'swagger-ui-bundle.js').is_file()
    urls = {}
    if bundled:
        urls = {
            'swagger_js_url': 'swagger/swagger-ui-bundle.js',
            'swagger_css_url': 'swagger/swagger-ui.css',
            # FastAPI's default favicon lives on fastapi.tiangolo.com; use
            # the dashboard's own so /docs makes no external request at all.
            'swagger_favicon_url': 'favicon.svg',
        }
        if not config.dashboard_enabled:
            # Otherwise the root dashboard mount serves these files; without
            # it /docs would load a bundle that 404s and render blank.
            app.mount('/swagger', StaticFiles(directory=swagger_dir),
                      name='swagger')

    @app.get('/docs', include_in_schema=False)
    async def _swagger_ui() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url='openapi.json',
            title=f'{app.title} - API documentation',
            **urls,
        )


def _mount_dashboard(app: FastAPI) -> None:
    """ Serve the built dashboard at /, or a hint page when it is missing. """
    if (STATIC_DIR / 'index.html').is_file():
        # html=True serves index.html for the root. StaticFiles resolves and
        # confines every path inside the directory, so it also handles the
        # traversal attempts a public mount attracts.
        app.mount('/', StaticFiles(directory=STATIC_DIR, html=True),
                  name='dashboard')
        logger.info('Dashboard served from %s', STATIC_DIR)
        return

    @app.get('/', include_in_schema=False, response_class=HTMLResponse)
    async def _missing_dashboard() -> str:
        return MISSING_DASHBOARD_HTML

    logger.warning(
        'Dashboard assets not found in %s. Serving a hint page instead;'
        ' the REST API is unaffected. Build them with'
        ' "cd frontend && npm ci && npm run build".', STATIC_DIR)


def _build_mcp(service: BatcontrolService, config: ApiConfig):
    """ Build the MCP routes and lifespan app, or ([], None) on failure.

    A missing or broken MCP SDK must not prevent the REST API and the
    dashboard from starting: it is an optional feature of an optional API.
    """
    try:
        # Imported lazily so the MCP SDK is only loaded when it is used.
        from . import mcp as mcp_module  # pylint: disable=import-outside-toplevel
        server = mcp_module.build_server(service)
        return mcp_module.http_routes(
            server, path=MCP_PATH, host=config.host,
            token=config.effective_mcp_token)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error('MCP server could not be started (%s). The REST API and'
                     ' dashboard are unaffected.', exc, exc_info=True)
        return [], None
