"""Shared Deye Modbus value objects and protocols."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RegisterWrite:
    """Single holding-register write."""

    register: int
    value: int


@dataclass(frozen=True)
class RegisterRead:
    """Register block read result."""

    start_register: int
    values: list[int]


class DeyeModbusTransport(Protocol):
    """Minimal transport seam for Deye Modbus register access."""

    def read_registers(self, register: int, count: int) -> RegisterRead:
        """Read a contiguous register block."""

    def write_registers(self, writes: list[RegisterWrite]):
        """Write one ordered batch of register values."""
