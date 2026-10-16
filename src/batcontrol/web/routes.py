""" HTTP routes of the read-only API

Every route in here is a GET. There is no POST, PUT, PATCH or DELETE, and
tests/batcontrol/web/test_readonly.py enforces that: the API reads batcontrol,
it never steers it. Control stays with the MQTT API.

Two routers:
  public_router    - /health only, reachable without a token
  router           - everything else, behind the optional bearer token
"""
import logging
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Path, Query, Request

from .models import (
    Battery, Configuration, Current, Decision, DecisionHistory,
    Envelope, ForecastBundle, ForecastSeries, Health, Status, Version,
)
from .service import HISTORY_MAX, WINDOW_MAX, BatcontrolService

logger = logging.getLogger(__name__)

TAG_META = 'meta'
TAG_STATUS = 'status'
TAG_FORECAST = 'forecast'
TAG_DECISION = 'decision'
TAG_CONFIG = 'config'

# Default and maximum number of traces returned by the history endpoint. The
# journal keeps 200 by default; a full dump is large, so the default is small.
HISTORY_DEFAULT = 20

public_router = APIRouter()
router = APIRouter()

# REST clients default to the whole horizon - the dashboard wants it - while
# the MCP tools default to 24 hours and page for more.
LIMIT_QUERY = Query(
    None, ge=1, le=WINDOW_MAX,
    description='Maximum number of slots to return. Omit for the whole'
                ' horizon.')
OFFSET_QUERY = Query(
    0, ge=0, le=WINDOW_MAX,
    description='Slot index to start at, for paging through the horizon.')


def now_ms() -> int:
    """ Current server time in epoch milliseconds. """
    return int(time.time() * 1000)


def envelope(data: Any) -> Dict[str, Any]:
    """ Wrap a payload in the common response envelope. """
    return {'ok': True, 'timestamp': now_ms(), 'data': data}


def error_envelope(code: str, detail: str) -> Dict[str, Any]:
    """ The envelope of every non-2xx response. """
    return {'ok': False, 'timestamp': now_ms(), 'error': code,
            'detail': detail}


def service(request: Request) -> BatcontrolService:
    """ The service layer instance attached to the application. """
    return request.app.state.service


@public_router.get(
    '/health',
    response_model=Envelope[Health],
    tags=[TAG_META],
    summary='Liveness of the control loop',
    description='Answers even before the first control cycle has completed,'
                ' so it is usable as a container health check. This is the'
                ' only endpoint that stays reachable without a token when'
                ' api.auth_token is configured.',
)
def get_health(request: Request) -> Dict[str, Any]:
    """ Liveness and freshness of the control loop. """
    return envelope(service(request).health())


@router.get(
    '/version',
    response_model=Envelope[Version],
    tags=[TAG_META],
    summary='Version and runtime identification',
)
def get_version(request: Request) -> Dict[str, Any]:
    """ Version, build and runtime environment. """
    return envelope(service(request).version())


@router.get(
    '/status',
    response_model=Envelope[Status],
    tags=[TAG_STATUS],
    summary='Current control state',
    description='The inverter mode batcontrol has set, who set it, and when'
                ' the next evaluation is due.',
)
def get_status(request: Request) -> Dict[str, Any]:
    """ What batcontrol is doing right now. """
    return envelope(service(request).status())


@router.get(
    '/battery',
    response_model=Envelope[Battery],
    tags=[TAG_STATUS],
    summary='Battery state and limits',
    description='State of charge, capacities and the SoC limits. Values are'
                ' the ones cached by the last control cycle - reading this'
                ' endpoint never queries the inverter.',
)
def get_battery(request: Request) -> Dict[str, Any]:
    """ Battery state and the limits needed to render it. """
    return envelope(service(request).battery())


@router.get(
    '/current',
    response_model=Envelope[Current],
    tags=[TAG_STATUS],
    summary='Current price and SoC in one call',
    description='Convenience endpoint for dashboards: the current price is'
                ' always populated, independent of which decision path ran.'
                ' Returns 503 until the first control cycle has completed.',
)
def get_current(request: Request) -> Dict[str, Any]:
    """ Current price situation plus SoC and its limits. """
    return envelope(service(request).current())


@router.get(
    '/forecast',
    response_model=Envelope[ForecastBundle],
    tags=[TAG_FORECAST],
    summary='All forecast series of the last cycle',
    description='Price, consumption, production and net consumption in one'
                ' response, so a dashboard needs a single request. All series'
                ' share the same slot grid.',
)
def get_forecast(
        request: Request,
        limit: Optional[int] = LIMIT_QUERY,
        offset: int = OFFSET_QUERY,
) -> Dict[str, Any]:
    """ All four forecast series of the last control cycle. """
    return envelope(service(request).forecast_all(limit=limit, offset=offset))


@router.get(
    '/forecast/{series}',
    response_model=Envelope[ForecastSeries],
    tags=[TAG_FORECAST],
    summary='One forecast series',
    description='`price` is EUR/kWh per slot. `consumption`, `production` and'
                ' `net_consumption` are Wh per slot and additionally carry'
                ' `power_w`, the average power over the slot. Negative'
                ' net_consumption means surplus solar energy.',
)
def get_forecast_series(
        request: Request,
        series: str = Path(
            ...,
            description='price, consumption, production or net_consumption',
            examples=['price'],
        ),
        limit: Optional[int] = LIMIT_QUERY,
        offset: int = OFFSET_QUERY,
) -> Dict[str, Any]:
    """ A single forecast series. """
    return envelope(
        service(request).forecast(series, limit=limit, offset=offset))


@router.get(
    '/decision',
    response_model=Envelope[Decision],
    tags=[TAG_DECISION],
    summary='Why batcontrol chose the current mode',
    description='The latest decision trace with its decisive step and a'
                ' plain language explanation. The record shapes are'
                ' documented in the decision trace developer docs.',
)
def get_decision(request: Request) -> Dict[str, Any]:
    """ The latest decision trace. """
    return envelope(service(request).decision())


@router.get(
    '/decision/history',
    response_model=Envelope[DecisionHistory],
    tags=[TAG_DECISION],
    summary='Recent decisions',
    description='Traces from the in-memory journal, oldest first. The journal'
                ' is not persisted: it starts empty after a restart.',
)
def get_decision_history(
        request: Request,
        limit: int = Query(
            HISTORY_DEFAULT, ge=1, le=HISTORY_MAX,
            description='Maximum number of traces to return.'),
) -> Dict[str, Any]:
    """ The last decisions from the journal. """
    return envelope(service(request).decision_history(limit=limit))


@router.get(
    '/config',
    response_model=Envelope[Configuration],
    tags=[TAG_CONFIG],
    summary='Runtime parameters',
    description='The parameters that explain batcontrol\'s behaviour. This is'
                ' an explicit allow-list of runtime values, not a dump of the'
                ' configuration file: inverter passwords, tariff tokens and'
                ' MQTT credentials are not reachable from here.',
)
def get_config(request: Request) -> Dict[str, Any]:
    """ Runtime parameters, never credentials. """
    return envelope(service(request).configuration())
