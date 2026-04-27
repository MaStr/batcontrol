import logging
from typing import Optional

from ..baseclass import DEFAULT_MAX_SOC, DEFAULT_MIN_SOC, InverterBaseclass
from .control import FroniusModbusControl
from .storage_reader import FroniusModbusStorageReader
from .types import FroniusModbusTransport

logger = logging.getLogger(__name__)


class FroniusModbusInverter(InverterBaseclass):
    def __init__(
        self,
        transport: FroniusModbusTransport,
        max_charge_rate: float,
        capacity: float = -1,
        min_soc: float = DEFAULT_MIN_SOC,
        max_soc: float = DEFAULT_MAX_SOC,
        revert_seconds: int = 0,
        grid_status_reader=None,
        extra_transports: Optional[list[FroniusModbusTransport]] = None,
    ):
        super().__init__({})
        self.transport = transport
        self.capacity = capacity
        self.min_soc = min_soc
        self.max_soc = max_soc
        self.max_grid_charge_rate = max_charge_rate
        self.control = FroniusModbusControl(
            transport,
            max_charge_rate=max_charge_rate,
            revert_seconds=revert_seconds,
            grid_status_reader=grid_status_reader,
        )
        self.storage_reader = FroniusModbusStorageReader(transport)
        self.extra_transports = extra_transports or []

    def set_mode_force_charge(self, chargerate: float):
        self.control.set_mode_force_charge(chargerate)

    def set_mode_avoid_discharge(self):
        self.control.set_mode_avoid_discharge()

    def set_mode_allow_discharge(self):
        self.control.set_mode_allow_discharge()

    def set_mode_limit_battery_charge(self, limit_charge_rate: int):
        self.control.set_mode_limit_battery_charge(limit_charge_rate)

    def get_capacity(self) -> float:
        return self.capacity

    def read_storage_status(self):
        return self.storage_reader.read_storage_status()

    def get_SOC(self) -> float:
        return self.read_storage_status().soc_pct

    def get_max_charge_rate(self) -> float:
        return self.read_storage_status().max_charge_rate_w

    def is_grid_charging_enabled(self) -> bool:
        return self.read_storage_status().grid_charging_enabled

    def get_min_reserve_soc(self) -> float:
        return self.read_storage_status().minimum_reserve_pct

    def get_charge_status(self) -> int:
        return self.read_storage_status().charge_status

    def shutdown(self):
        try:
            self.control.set_mode_allow_discharge()
        except Exception as exc:
            logger.warning(
                "Failed to restore automatic mode during shutdown: %s",
                exc,
            )
        finally:
            close = getattr(self.transport, "close", None)
            if close is not None:
                close()
            for transport in self.extra_transports:
                close = getattr(transport, "close", None)
                if close is not None:
                    close()

    def activate_mqtt(self, api_mqtt_api: object):
        pass
