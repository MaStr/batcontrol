"""Energy that cheaper slots are too short to charge.

A cheaper slot ahead is the place to charge for the more expensive slots
behind it, but only for what the inverter can charge there. Both the
discharge reserve and the grid recharge walk over the upcoming slots and ask
the same question: how much of the demand behind the cheaper slots has to be
in the battery already?

    shortfall = max over demand slots h of
                (demand up to h) - (charge capacity of the cheaper slots
                                    before h)

Energy that is in the battery now only helps as long as the battery has room
for it on top of what the cheaper slots charge. That room is the energy the
battery takes from the grid minus the net charge of the cheaper slots so
far. The shortfall never exceeds it, and once it is used up the battery is
full from there on, whatever is kept or charged now.

The two rules share this balance only. Which slots count as demand and as
cheaper slot, and which PV surplus is credited, differs between them, so the
caller classifies the slots and feeds them in order, nearest slot first:

                      discharge reserve          grid recharge
    demand slot       more expensive than now    more expensive than now
                                                 plus the minimum price
                                                 difference
    cheaper slot      cheaper than now minus     not more expensive than
                      the minimum price          now (with "soften": a
                      difference                 little below)
    PV surplus        from slot 0 on             from slot 1 on
    credited

The asymmetry is intended. The reserve is the cautious side in both
directions: it sees more demand and fewer chances to recharge, because
keeping energy that is already in the battery costs nothing. Charging from
the grid costs money and has losses, so it only pays off for slots that are
more expensive by the minimum price difference, and any slot that is not
more expensive than now is a reason to wait. PV of the current slot only
replaces grid power while charging from the grid, so the recharge does not
credit it.
"""
from dataclasses import dataclass


@dataclass
class CheaperSlotShortfall:  # pylint: disable=too-many-instance-attributes
    """Running balance of demand against the charge capacity of cheaper
    slots. All energies in Wh."""
    # Energy one cheaper slot charges at the maximum grid charge rate
    slot_charge_capacity: float
    # Usable energy the battery holds when charged from the grid, see
    # DefaultLogic._grid_chargeable_energy
    grid_chargeable_energy: float

    demand: float = 0.0
    charge_capacity: float = 0.0
    # Room for energy that is in the battery now. Unlimited until the first
    # cheaper slot: before it nothing else has charged the battery.
    headroom: float = float('inf')

    # The largest shortfall so far, and the balance at the slot causing it
    energy: float = 0.0
    demand_at_shortfall: float = 0.0
    charge_capacity_at_shortfall: float = 0.0
    # Room in the battery if that room limits the shortfall, otherwise 0
    battery_room_at_shortfall: float = 0.0

    def add_cheaper_slot(self) -> bool:
        """Count one slot that can charge the battery.

        Returns False once the battery is full from here on: the walk over
        the slots ends there.
        """
        self.charge_capacity += self.slot_charge_capacity
        self.headroom = min(
            self.headroom,
            self.grid_chargeable_energy - (self.charge_capacity - self.demand))
        return self.headroom > 0

    def add_demand(self, energy: float) -> bool:
        """Count the demand of one slot.

        Returns True if this slot raises the shortfall.
        """
        self.demand += float(energy)
        uncovered = self.demand - self.charge_capacity
        if min(uncovered, self.headroom) <= self.energy:
            return False
        limited_by_room = self.headroom < uncovered
        self.energy = float(min(uncovered, self.headroom))
        self.demand_at_shortfall = self.demand
        self.charge_capacity_at_shortfall = self.charge_capacity
        self.battery_room_at_shortfall = (
            float(self.headroom) if limited_by_room else 0.0)
        return True
