"""Mode-switching logic for the Deye Modbus backend."""

from .commands import (
    build_allow_discharge_register_writes,
    build_avoid_discharge_register_writes,
    build_force_charge_register_writes,
    build_limit_battery_charge_register_writes,
)
from .storage_reader import DeyeStorageReader
from .types import DeyeModbusTransport


class DeyeControl:
    def __init__(
        self,
        transport: DeyeModbusTransport,
        nominal_battery_voltage: float,
        min_soc: int,
        max_soc: int,
    ):
        self.transport = transport
        self.nominal_battery_voltage = nominal_battery_voltage
        self.min_soc = min_soc
        self.max_soc = max_soc
        self.storage_reader = DeyeStorageReader(transport)

    def set_mode_force_charge(self, rate_watts: float):
        self.transport.write_registers(
            build_force_charge_register_writes(
                rate_watts,
                self.nominal_battery_voltage,
                self.max_soc,
            )
        )

    def set_mode_avoid_discharge(self):
        current_soc = round(self.storage_reader.read_storage_status().soc_pct)
        self.transport.write_registers(
            build_avoid_discharge_register_writes(current_soc)
        )

    def set_mode_allow_discharge(self):
        self.transport.write_registers(
            build_allow_discharge_register_writes(self.min_soc)
        )

    def set_mode_limit_battery_charge(self, rate_watts: float):
        self.transport.write_registers(
            build_limit_battery_charge_register_writes(
                rate_watts,
                self.nominal_battery_voltage,
                self.min_soc,
            )
        )
