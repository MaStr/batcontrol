""" Per-inverter values for MQTT and the web API

Every inverter reads its values once per control cycle in
refresh_api_values() and keeps them as one frozen InverterValues object. MQTT
publishes from that object, and the web API reads it - an HTTP request never
talks to the inverter itself.
"""
import dataclasses
from typing import Any, Dict, Optional


@dataclasses.dataclass(frozen=True)
class InverterValues:  # pylint: disable=too-many-instance-attributes
    """ Values of one inverter, read in one go.

    Energies in Wh, rates in W, SoC values in percent. `extra` carries
    backend specific values (e.g. the Fronius energy management mode).
    """
    inverter_id: int
    inverter_type: Optional[str]
    read_at: float                    # epoch seconds
    soc: float
    capacity: float                   # gross capacity
    max_capacity: float               # capacity * max_soc
    usable_capacity: float            # capacity between min_soc and max_soc
    stored_energy: float
    stored_usable_energy: float       # above min_soc
    free_capacity: float              # up to max_soc
    min_soc: float
    max_soc: float
    max_grid_charge_rate: Optional[float] = None
    max_pv_charge_rate: Optional[float] = None
    min_pv_charge_rate: Optional[float] = None
    min_charge_rate: Optional[float] = None
    extra: Dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_reading(cls, *, soc: float, capacity: float,  # pylint: disable=too-many-arguments
                     min_soc: float, max_soc: float, **kwargs) -> 'InverterValues':
        """ Derive the energies from one SoC and one capacity reading.

        Same formulas as the InverterBaseclass getters, but from a single
        read: calling the getters one by one would query the SoC up to four
        times, which is Modbus traffic for a backend without a cache.
        """
        return cls(
            soc=soc,
            capacity=capacity,
            max_capacity=max_soc / 100 * capacity,
            usable_capacity=(max_soc - min_soc) / 100 * capacity,
            stored_energy=max(0.0, soc / 100 * capacity),
            stored_usable_energy=max(0.0, (soc - min_soc) / 100 * capacity),
            free_capacity=(max_soc - soc) / 100 * capacity,
            min_soc=min_soc,
            max_soc=max_soc,
            **kwargs,
        )
