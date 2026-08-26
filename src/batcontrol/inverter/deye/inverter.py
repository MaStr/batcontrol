import logging

from ..baseclass import DEFAULT_MAX_SOC, DEFAULT_MIN_SOC, InverterBaseclass
from .control import DeyeControl
from .storage_reader import DeyeStorageReader
from .types import DeyeModbusTransport

logger = logging.getLogger(__name__)


class DeyeInverter(InverterBaseclass):
    def __init__(
        self,
        transport: DeyeModbusTransport,
        capacity: float,
        nominal_battery_voltage: float,
        min_soc: float = DEFAULT_MIN_SOC,
        max_soc: float = DEFAULT_MAX_SOC,
    ):
        super().__init__({})
        self.transport = transport
        self.capacity = capacity
        self.min_soc = min_soc
        self.max_soc = max_soc
        self.control = DeyeControl(
            transport,
            nominal_battery_voltage=nominal_battery_voltage,
            min_soc=min_soc,
            max_soc=max_soc,
        )
        self.storage_reader = DeyeStorageReader(transport)

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

    def get_SOC(self) -> float:  # pylint: disable=invalid-name
        return self.read_storage_status().soc_pct

    def shutdown(self):
        try:
            self.control.set_mode_allow_discharge()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Failed to restore automatic mode during shutdown: %s",
                exc,
            )
        finally:
            close = getattr(self.transport, "close", None)
            if close is not None:
                close()

    def activate_mqtt(self, api_mqtt_api: object):
        pass
