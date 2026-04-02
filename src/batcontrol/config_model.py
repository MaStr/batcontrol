"""Pydantic configuration models for Batcontrol.

These models validate and coerce types at config load time,
eliminating scattered type conversion code throughout the codebase.
They also fix HA addon issues where numeric values arrive as strings.
"""
from typing import List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _parse_semicolon_int_list(v):
    """Parse a semicolon-separated string into a list of ints.

    Handles HA addon config where lists are passed as strings like
    "-7;-14;-21". Also accepts regular lists and converts items to int.
    Returns None if input is None.
    """
    if v is None:
        return None
    if isinstance(v, str):
        return [int(x.strip()) for x in v.split(';')]
    if isinstance(v, list):
        return [int(x) for x in v]
    return v


class BatteryControlConfig(BaseModel):
    """Battery control parameters."""
    model_config = ConfigDict(extra='allow')

    min_price_difference: float = 0.05
    min_price_difference_rel: float = 0.10
    always_allow_discharge_limit: float = 0.90
    max_charging_from_grid_limit: float = 0.89
    min_recharge_amount: float = 100.0


class BatteryControlExpertConfig(BaseModel):
    """Expert tuning parameters for battery control."""
    model_config = ConfigDict(extra='allow')

    charge_rate_multiplier: float = 1.1
    soften_price_difference_on_charging: bool = False
    soften_price_difference_on_charging_factor: int = 5
    round_price_digits: int = 4
    production_offset_percent: float = 1.0


class InverterConfig(BaseModel):
    """Inverter configuration."""
    model_config = ConfigDict(extra='allow')

    type: str = 'dummy'
    address: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = None
    max_grid_charge_rate: float = 5000
    max_pv_charge_rate: float = 0
    min_pv_charge_rate: float = 0
    fronius_inverter_id: Optional[str] = None
    fronius_controller_id: Optional[str] = None
    enable_resilient_wrapper: bool = False
    outage_tolerance_minutes: float = 24
    retry_backoff_seconds: float = 60
    # MQTT inverter fields
    capacity: Optional[int] = None
    min_soc: Optional[int] = None
    max_soc: Optional[int] = None
    base_topic: Optional[str] = None
    cache_ttl: int = 120

    @model_validator(mode='before')
    @classmethod
    def handle_max_charge_rate_rename(cls, data):
        """Support legacy config key max_charge_rate -> max_grid_charge_rate."""
        if isinstance(data, dict):
            if 'max_charge_rate' in data and 'max_grid_charge_rate' not in data:
                data = dict(data)
                data['max_grid_charge_rate'] = data.pop('max_charge_rate')
        return data


class UtilityConfig(BaseModel):
    """Dynamic tariff provider configuration."""
    model_config = ConfigDict(extra='allow')

    type: str
    apikey: Optional[str] = None
    url: Optional[str] = None
    # vat/fees/markup are Optional so that downstream required_fields checks
    # (in dynamictariff.py) can detect missing config for providers that need them.
    # With exclude_none=True, absent keys won't appear in the output dict.
    vat: Optional[float] = None
    fees: Optional[float] = None
    markup: Optional[float] = None
    # Tariff zone fields
    tariff_zone_1: Optional[float] = None
    zone_1_hours: Optional[str] = None
    tariff_zone_2: Optional[float] = None
    zone_2_hours: Optional[str] = None
    tariff_zone_3: Optional[float] = None
    zone_3_hours: Optional[str] = None


class MqttConfig(BaseModel):
    """MQTT API configuration."""
    model_config = ConfigDict(extra='allow')

    enabled: bool = False
    logger: bool = False
    broker: str = 'localhost'
    port: int = 1883
    topic: str = 'house/batcontrol'
    username: Optional[str] = None
    password: Optional[str] = None
    retry_attempts: int = 5
    retry_delay: int = 10
    tls: bool = False
    cafile: Optional[str] = None
    certfile: Optional[str] = None
    keyfile: Optional[str] = None
    tls_version: Optional[str] = None
    auto_discover_enable: bool = True
    auto_discover_topic: str = 'homeassistant'


class EvccConfig(BaseModel):
    """EVCC connection configuration."""
    model_config = ConfigDict(extra='allow')

    enabled: bool = False
    broker: str = 'localhost'
    port: int = 1883
    status_topic: str = 'evcc/status'
    loadpoint_topic: Union[str, List[str]] = 'evcc/loadpoints/1/charging'
    block_battery_while_charging: bool = True
    battery_halt_topic: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    tls: bool = False
    cafile: Optional[str] = None
    certfile: Optional[str] = None
    keyfile: Optional[str] = None
    tls_version: Optional[str] = None


class PvInstallationConfig(BaseModel):
    """Single PV installation configuration."""
    model_config = ConfigDict(extra='allow')

    name: str = ''
    type: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    declination: Optional[float] = None
    azimuth: Optional[float] = None
    kWp: Optional[float] = None  # pylint: disable=invalid-name
    url: Optional[str] = None
    horizon: Optional[str] = None
    apikey: Optional[str] = None
    algorithm: Optional[str] = None
    item: Optional[str] = None
    token: Optional[str] = None
    # HA Solar Forecast ML fields
    base_url: Optional[str] = None
    api_token: Optional[str] = None
    entity_id: Optional[str] = None
    sensor_unit: Optional[str] = None
    cache_ttl_hours: Optional[float] = None


class ConsumptionForecastConfig(BaseModel):
    """Consumption forecast configuration."""
    model_config = ConfigDict(extra='allow')

    type: str = 'csv'
    # CSV fields
    annual_consumption: Optional[float] = None
    load_profile: Optional[str] = None
    csv: Optional[dict] = None
    # HomeAssistant API fields
    homeassistant_api: Optional[dict] = None
    base_url: Optional[str] = None
    apitoken: Optional[str] = None
    entity_id: Optional[str] = None
    history_days: Optional[Union[str, List[int]]] = None
    history_weights: Optional[Union[str, List[int]]] = None
    cache_ttl_hours: Optional[float] = None
    multiplier: Optional[float] = None
    sensor_unit: Optional[str] = None

    @field_validator('history_days', mode='before')
    @classmethod
    def parse_history_days(cls, v):
        """Parse semicolon-separated string to list of ints."""
        return _parse_semicolon_int_list(v)

    @field_validator('history_weights', mode='before')
    @classmethod
    def parse_history_weights(cls, v):
        """Parse semicolon-separated string to list of ints."""
        return _parse_semicolon_int_list(v)


class BatcontrolConfig(BaseModel):
    """Top-level Batcontrol configuration model.

    Validates and coerces all configuration values at load time.
    Uses extra='allow' to preserve unknown fields for forward compatibility.
    """
    model_config = ConfigDict(extra='allow')

    timezone: str = 'Europe/Berlin'
    time_resolution_minutes: int = 60
    loglevel: str = 'info'
    logfile_enabled: bool = True
    log_everything: bool = False
    max_logfile_size: int = 200
    logfile_path: str = 'logs/batcontrol.log'
    solar_forecast_provider: str = 'fcsolarapi'

    battery_control: BatteryControlConfig = Field(default_factory=BatteryControlConfig)
    battery_control_expert: Optional[BatteryControlExpertConfig] = None
    inverter: InverterConfig = Field(default_factory=InverterConfig)
    utility: UtilityConfig
    mqtt: Optional[MqttConfig] = None
    evcc: Optional[EvccConfig] = None
    pvinstallations: List[PvInstallationConfig]
    consumption_forecast: ConsumptionForecastConfig = Field(
        default_factory=ConsumptionForecastConfig
    )

    @field_validator('time_resolution_minutes')
    @classmethod
    def validate_time_resolution(cls, v):
        """Validate time_resolution_minutes is 15 or 60."""
        if v not in (15, 60):
            raise ValueError(
                f"time_resolution_minutes must be 15 or 60, got {v}"
            )
        return v

    @field_validator('loglevel')
    @classmethod
    def validate_loglevel(cls, v):
        """Validate loglevel is a recognized level."""
        valid = ('debug', 'info', 'warning', 'error')
        if v.lower() not in valid:
            raise ValueError(
                f"loglevel must be one of {valid}, got '{v}'"
            )
        return v.lower()


def validate_config(config_dict: dict) -> dict:
    """Validate and coerce a raw config dict using Pydantic models.

    Args:
        config_dict: Raw configuration dictionary (from YAML/JSON).

    Returns:
        Validated and type-coerced configuration dictionary.

    Raises:
        pydantic.ValidationError: If validation fails.
    """
    validated = BatcontrolConfig(**config_dict)
    return validated.model_dump(exclude_none=True)
