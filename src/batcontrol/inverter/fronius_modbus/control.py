import logging

from .commands import (
    build_allow_discharge_register_writes,
    build_avoid_discharge_register_writes,
    build_force_charge_register_writes,
    build_limit_battery_charge_register_writes,
)
from .grid_status import FroniusModbusGridStatus
from .types import FroniusModbusTransport

logger = logging.getLogger(__name__)

ON_GRID_STATUSES = {
    FroniusModbusGridStatus.ON_GRID,
    FroniusModbusGridStatus.ON_GRID_OPERATING,
}


class FroniusModbusControl:
    def __init__(
        self,
        transport: FroniusModbusTransport,
        max_charge_rate: float,
        revert_seconds: int = 0,
        grid_status_reader=None,
    ):
        self.transport = transport
        self.max_charge_rate = max_charge_rate
        self.revert_seconds = revert_seconds
        self.grid_status_reader = grid_status_reader

    def set_mode_force_charge(self, rate_watts: float):
        self._write_restrictive_mode(
            "force_charge",
            build_force_charge_register_writes(
                rate_watts,
                self.max_charge_rate,
                revert_seconds=self.revert_seconds,
            )
        )

    def set_mode_avoid_discharge(self):
        self._write_restrictive_mode(
            "avoid_discharge",
            build_avoid_discharge_register_writes(
                revert_seconds=self.revert_seconds,
            )
        )

    def set_mode_allow_discharge(self):
        self.transport.write_registers(build_allow_discharge_register_writes())

    def set_mode_limit_battery_charge(self, rate_watts: float):
        self._write_restrictive_mode(
            "limit_battery_charge",
            build_limit_battery_charge_register_writes(
                rate_watts,
                self.max_charge_rate,
                revert_seconds=self.revert_seconds,
            )
        )

    def _write_restrictive_mode(self, mode_name: str, writes):
        if self._is_restrictive_mode_allowed(mode_name):
            self.transport.write_registers(writes)
            return

        self.transport.write_registers(build_allow_discharge_register_writes())

    def _is_restrictive_mode_allowed(self, mode_name: str) -> bool:
        if self.grid_status_reader is None:
            return True

        try:
            status_read = self.grid_status_reader.read_grid_status()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Skipping Fronius Modbus %s because grid status could not be read: %s",
                mode_name,
                exc,
            )
            return False

        grid_status = getattr(status_read, "status", status_read)
        if grid_status in ON_GRID_STATUSES:
            return True

        logger.warning(
            "Skipping Fronius Modbus %s while grid status is %s; restoring allow-discharge mode",
            mode_name,
            getattr(grid_status, "value", grid_status),
        )
        return False
