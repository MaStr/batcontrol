from .commands import (
    build_allow_discharge_register_writes,
    build_avoid_discharge_register_writes,
    build_force_charge_register_writes,
    build_limit_battery_charge_register_writes,
)
from .types import FroniusModbusTransport


class FroniusModbusControl:
    def __init__(
        self,
        transport: FroniusModbusTransport,
        max_charge_rate: float,
        revert_seconds: int = 0,
    ):
        self.transport = transport
        self.max_charge_rate = max_charge_rate
        self.revert_seconds = revert_seconds

    def set_mode_force_charge(self, rate_watts: float):
        self.transport.write_registers(
            build_force_charge_register_writes(
                rate_watts,
                self.max_charge_rate,
                revert_seconds=self.revert_seconds,
            )
        )

    def set_mode_avoid_discharge(self):
        self.transport.write_registers(
            build_avoid_discharge_register_writes(
                revert_seconds=self.revert_seconds,
            )
        )

    def set_mode_allow_discharge(self):
        self.transport.write_registers(build_allow_discharge_register_writes())

    def set_mode_limit_battery_charge(self, rate_watts: float):
        self.transport.write_registers(
            build_limit_battery_charge_register_writes(
                rate_watts,
                self.max_charge_rate,
                revert_seconds=self.revert_seconds,
            )
        )
