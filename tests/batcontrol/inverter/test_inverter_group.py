"""Tests for InverterGroup - several inverters presented as one battery."""
import pytest

from batcontrol.inverter.baseclass import (
    DEFAULT_MIN_CHARGE_RATE,
    InverterBaseclass,
)
from batcontrol.inverter.group import (
    InverterGroup,
    distribute_rate,
    inverter_members,
    to_watts,
)


class RecordingInverter(InverterBaseclass):
    """Inverter stub that records the commands it received."""

    def __init__(
            self,
            soc,
            capacity=10000,
            min_soc=10,
            max_soc=100,
            max_grid_charge_rate=5000,
            max_pv_charge_rate=0,
            min_charge_rate=DEFAULT_MIN_CHARGE_RATE,
            min_pv_charge_rate=0,
            inverter_num=0):
        super().__init__({})
        self._soc = soc
        self.capacity = capacity
        self.min_soc = min_soc
        self.max_soc = max_soc
        self.max_grid_charge_rate = max_grid_charge_rate
        self.max_pv_charge_rate = max_pv_charge_rate
        self.min_charge_rate = min_charge_rate
        self.min_pv_charge_rate = min_pv_charge_rate
        self.inverter_num = inverter_num
        self.commands = []
        self.mqtt_activated_with = None
        self.refresh_calls = 0
        self.shutdown_calls = 0

    def set_mode_force_charge(self, chargerate: float):
        self.commands.append(('force_charge', chargerate))

    def set_mode_avoid_discharge(self):
        self.commands.append(('avoid_discharge', None))

    def set_mode_allow_discharge(self):
        self.commands.append(('allow_discharge', None))

    def set_mode_limit_battery_charge(self, limit_charge_rate: int):
        self.commands.append(('limit_battery_charge', limit_charge_rate))

    def get_capacity(self) -> float:
        return self.capacity

    def get_SOC(self) -> float:
        return self._soc

    def activate_mqtt(self, api_mqtt_api: object):
        self.mqtt_activated_with = api_mqtt_api

    def refresh_api_values(self):
        self.refresh_calls += 1

    def shutdown(self):
        self.shutdown_calls += 1


class TestDistributeRate:
    """The water-filling helper that splits one rate over several members."""

    def test_splits_proportional_to_weights(self):
        assert distribute_rate(3000, [3000, 1000], [9000, 9000]) == [2250, 750]

    def test_caps_member_and_redistributes_the_remainder(self):
        # Weights 10:1, but member 0 is capped at 5000 W, so the 1000 W that
        # do not fit have to end up on member 1.
        rates = distribute_rate(6000, [7500, 750], [5000, 3000])

        assert rates == pytest.approx([5000, 1000])

    def test_total_is_preserved_when_capacity_is_available(self):
        rates = distribute_rate(4000, [1000, 1000, 1000], [5000, 5000, 5000])

        assert sum(rates) == pytest.approx(4000)

    def test_member_without_weight_gets_nothing(self):
        assert distribute_rate(2000, [1000, 0], [5000, 5000]) == [2000, 0]

    def test_falls_back_to_caps_when_no_member_has_weight(self):
        # All batteries full: the rate is still placed, weighted by the limits.
        rates = distribute_rate(4000, [0, 0], [3000, 1000])

        assert rates == pytest.approx([3000, 1000])

    def test_cap_of_zero_means_unlimited(self):
        rates = distribute_rate(4000, [1000, 1000], [0, 0])

        assert rates == pytest.approx([2000, 2000])

    def test_clips_total_to_the_sum_of_the_caps(self):
        rates = distribute_rate(9000, [1000, 1000], [2000, 3000])

        assert rates == pytest.approx([2000, 3000])

    def test_zero_rate_gives_no_allocation(self):
        assert distribute_rate(0, [1000, 1000], [5000, 5000]) == [0, 0]

    def test_empty_group(self):
        assert distribute_rate(1000, [], []) == []


class TestGroupAggregation:
    """Reading values from a group sums/weights the members."""

    @pytest.fixture
    def group(self):
        return InverterGroup([
            RecordingInverter(soc=20, capacity=10000, min_soc=10, max_soc=100),
            RecordingInverter(soc=80, capacity=5000, min_soc=20, max_soc=90,
                              inverter_num=1),
        ])

    def test_capacity_is_summed(self, group):
        assert group.get_capacity() == 15000

    def test_stored_energy_is_summed(self, group):
        # 20% of 10000 + 80% of 5000
        assert group.get_stored_energy() == 2000 + 4000

    def test_stored_usable_energy_is_summed(self, group):
        # (20-10)% of 10000 + (80-20)% of 5000
        assert group.get_stored_usable_energy() == 1000 + 3000

    def test_max_capacity_is_summed(self, group):
        # 100% of 10000 + 90% of 5000
        assert group.get_max_capacity() == 10000 + 4500

    def test_free_capacity_is_summed(self, group):
        # (100-20)% of 10000 + (90-80)% of 5000
        assert group.get_free_capacity() == 8000 + 500

    def test_usable_capacity_is_summed(self, group):
        # (100-10)% of 10000 + (90-20)% of 5000
        assert group.get_usable_capacity() == 9000 + 3500

    def test_soc_is_capacity_weighted(self, group):
        # 6000 Wh stored of 15000 Wh installed
        assert group.get_SOC() == pytest.approx(40.0)

    def test_min_soc_is_capacity_weighted(self, group):
        # (10 * 10000 + 20 * 5000) / 15000
        assert group.min_soc == pytest.approx(200000 / 15000)

    def test_max_soc_is_capacity_weighted(self, group):
        # (100 * 10000 + 90 * 5000) / 15000
        assert group.max_soc == pytest.approx(1450000 / 15000)

    def test_max_grid_charge_rate_is_summed(self, group):
        assert group.max_grid_charge_rate == 10000

    def test_len_reports_the_member_count(self, group):
        assert len(group) == 2

    def test_soc_of_a_zero_capacity_group_is_zero(self):
        group = InverterGroup([RecordingInverter(soc=50, capacity=0)])

        assert group.get_SOC() == 0

    def test_empty_group_is_rejected(self):
        with pytest.raises(ValueError, match='at least one inverter'):
            InverterGroup([])


class TestGroupPvChargeRate:
    """max_pv_charge_rate: 0 means unlimited, so it dominates the sum."""

    def test_rates_are_summed_when_every_member_is_limited(self):
        group = InverterGroup([
            RecordingInverter(soc=50, max_pv_charge_rate=3000),
            RecordingInverter(soc=50, max_pv_charge_rate=2000),
        ])

        assert group.max_pv_charge_rate == 5000

    def test_a_single_unlimited_member_makes_the_group_unlimited(self):
        group = InverterGroup([
            RecordingInverter(soc=50, max_pv_charge_rate=3000),
            RecordingInverter(soc=50, max_pv_charge_rate=0),
        ])

        assert group.max_pv_charge_rate == 0


class TestGroupCommands:
    """Commands are forwarded to, or split across, the members."""

    def test_allow_discharge_is_forwarded_to_all(self):
        members = [RecordingInverter(soc=50), RecordingInverter(soc=50)]
        InverterGroup(members).set_mode_allow_discharge()

        for member in members:
            assert member.commands == [('allow_discharge', None)]

    def test_avoid_discharge_is_forwarded_to_all(self):
        members = [RecordingInverter(soc=50), RecordingInverter(soc=50)]
        InverterGroup(members).set_mode_avoid_discharge()

        for member in members:
            assert member.commands == [('avoid_discharge', None)]

    def test_force_charge_is_split_by_free_capacity(self):
        members = [
            RecordingInverter(soc=20, capacity=10000, max_soc=100,
                              max_grid_charge_rate=5000, min_charge_rate=0),
            RecordingInverter(soc=20, capacity=5000, max_soc=100,
                              max_grid_charge_rate=5000, min_charge_rate=0),
        ]
        # Free capacity 8000 : 4000 Wh -> 2:1
        InverterGroup(members).set_mode_force_charge(3000)

        assert members[0].commands == [('force_charge', 2000)]
        assert members[1].commands == [('force_charge', 1000)]

    def test_force_charge_floors_both_members_at_their_minimum_first(self):
        members = [
            RecordingInverter(soc=20, capacity=10000, max_soc=100,
                              max_grid_charge_rate=5000),
            RecordingInverter(soc=20, capacity=5000, max_soc=100,
                              max_grid_charge_rate=5000),
        ]
        # 500 W minimum each, the remaining 2000 W split 8000 : 4000 Wh
        InverterGroup(members).set_mode_force_charge(3000)

        assert members[0].commands == [('force_charge', 1833)]
        assert members[1].commands == [('force_charge', 1167)]

    def test_force_charge_respects_the_member_rate_limit(self):
        members = [
            RecordingInverter(soc=20, capacity=10000, max_soc=100,
                              max_grid_charge_rate=1500),
            RecordingInverter(soc=20, capacity=5000, max_soc=100,
                              max_grid_charge_rate=5000),
        ]
        # Member 0 would get 2000 W but is capped at 1500 W; the remaining
        # 500 W go to member 1.
        InverterGroup(members).set_mode_force_charge(3000)

        assert members[0].commands == [('force_charge', 1500)]
        assert members[1].commands == [('force_charge', 1500)]

    def test_force_charge_avoids_discharge_on_a_full_member(self):
        members = [
            RecordingInverter(soc=20, capacity=10000, max_soc=100,
                              max_grid_charge_rate=5000),
            RecordingInverter(soc=100, capacity=5000, max_soc=100,
                              max_grid_charge_rate=5000),
        ]
        # Member 1 is full: it must not discharge into the charging member.
        InverterGroup(members).set_mode_force_charge(3000)

        assert members[0].commands == [('force_charge', 3000)]
        assert members[1].commands == [('avoid_discharge', None)]

    def test_force_charge_is_clipped_to_the_summed_rate_limit(self):
        members = [
            RecordingInverter(soc=20, capacity=10000, max_soc=100,
                              max_grid_charge_rate=2000),
            RecordingInverter(soc=20, capacity=10000, max_soc=100,
                              max_grid_charge_rate=3000),
        ]
        InverterGroup(members).set_mode_force_charge(99000)

        assert members[0].commands == [('force_charge', 2000)]
        assert members[1].commands == [('force_charge', 3000)]

    def test_limit_battery_charge_of_zero_blocks_every_member(self):
        members = [
            RecordingInverter(soc=20, capacity=10000),
            RecordingInverter(soc=80, capacity=5000),
        ]
        InverterGroup(members).set_mode_limit_battery_charge(0)

        for member in members:
            assert member.commands == [('limit_battery_charge', 0)]

    def test_limit_battery_charge_is_split_by_free_capacity(self):
        members = [
            RecordingInverter(soc=20, capacity=10000, max_soc=100),
            RecordingInverter(soc=20, capacity=5000, max_soc=100),
        ]
        # Free capacity 8000 : 4000 Wh -> 2:1, no own PV limits
        InverterGroup(members).set_mode_limit_battery_charge(3000)

        assert members[0].commands == [('limit_battery_charge', 2000)]
        assert members[1].commands == [('limit_battery_charge', 1000)]

    def test_limit_battery_charge_respects_the_member_pv_limit(self):
        members = [
            RecordingInverter(soc=20, capacity=10000, max_soc=100,
                              max_pv_charge_rate=1500),
            RecordingInverter(soc=20, capacity=5000, max_soc=100,
                              max_pv_charge_rate=5000),
        ]
        InverterGroup(members).set_mode_limit_battery_charge(3000)

        assert members[0].commands == [('limit_battery_charge', 1500)]
        assert members[1].commands == [('limit_battery_charge', 1500)]


class TestGroupPlumbing:
    """MQTT activation, refresh and shutdown reach every member."""

    def test_activate_mqtt_is_forwarded_to_all(self):
        members = [RecordingInverter(soc=50), RecordingInverter(soc=50)]
        api = object()

        InverterGroup(members).activate_mqtt(api)

        for member in members:
            assert member.mqtt_activated_with is api

    def test_refresh_api_values_is_forwarded_to_all(self):
        members = [RecordingInverter(soc=50), RecordingInverter(soc=50)]
        InverterGroup(members).refresh_api_values()

        for member in members:
            assert member.refresh_calls == 1

    def test_refresh_continues_after_a_failing_member(self, caplog):
        failing = RecordingInverter(soc=50)
        failing.refresh_api_values = lambda: (_ for _ in ()).throw(
            RuntimeError('boom'))
        healthy = RecordingInverter(soc=50, inverter_num=1)

        InverterGroup([failing, healthy]).refresh_api_values()

        assert healthy.refresh_calls == 1

    def test_shutdown_is_forwarded_to_all(self):
        members = [RecordingInverter(soc=50), RecordingInverter(soc=50)]
        InverterGroup(members).shutdown()

        for member in members:
            assert member.shutdown_calls == 1

    def test_shutdown_continues_after_a_failing_member(self):
        failing = RecordingInverter(soc=50)
        failing.shutdown = lambda: (_ for _ in ()).throw(RuntimeError('boom'))
        healthy = RecordingInverter(soc=50, inverter_num=1)

        InverterGroup([failing, healthy]).shutdown()

        assert healthy.shutdown_calls == 1

    def test_inverters_property_returns_a_copy(self):
        members = [RecordingInverter(soc=50)]
        group = InverterGroup(members)

        group.inverters.append('not an inverter')

        assert len(group.inverters) == 1


class TestInverterMembers:
    """The helper that unwraps a possibly grouped inverter."""

    def test_group_is_unwrapped(self):
        members = [RecordingInverter(soc=50), RecordingInverter(soc=50)]

        assert inverter_members(InverterGroup(members)) == members

    def test_single_inverter_is_wrapped_in_a_list(self):
        inverter = RecordingInverter(soc=50)

        assert inverter_members(inverter) == [inverter]


class TestToWatts:
    """Allocated rates are rounded, not truncated."""

    def test_rounds_up_instead_of_truncating(self):
        # Regression: 999.99 W used to be forwarded as 999 W, so a 1000 W
        # group share silently lost a watt.
        assert to_watts(999.9999, 3000) == 1000

    def test_rounds_down_below_the_half_watt(self):
        assert to_watts(999.4, 3000) == 999

    def test_never_exceeds_the_cap(self):
        assert to_watts(1000.6, 1000) == 1000

    def test_cap_of_zero_means_unlimited(self):
        assert to_watts(1000.6, 0) == 1001

    def test_never_returns_a_negative_rate(self):
        assert to_watts(-5, 1000) == 0


class TestGroupRateRounding:
    """The rounding reaches the member commands."""

    def test_force_charge_does_not_lose_a_watt(self):
        # Free capacity 7500 : 250 Wh, limits 5000 / 3000 W.
        # Inverter 0 is capped at 5000 W, the remaining 1000 W go to inverter 1.
        members = [
            RecordingInverter(soc=20, capacity=10000, max_soc=95,
                              max_grid_charge_rate=5000),
            RecordingInverter(soc=90, capacity=5000, max_soc=95,
                              max_grid_charge_rate=3000, inverter_num=1),
        ]

        InverterGroup(members).set_mode_force_charge(6000)

        assert members[0].commands == [('force_charge', 5000)]
        assert members[1].commands == [('force_charge', 1000)]


class TestMinimumChargeRate:
    """A small rate is concentrated instead of smeared below the minimum."""

    def test_default_mirrors_the_logic_layer_minimum(self):
        """The two constants must not drift apart."""
        # pylint: disable=import-outside-toplevel
        from batcontrol.logic.common import MIN_CHARGE_RATE

        assert DEFAULT_MIN_CHARGE_RATE == MIN_CHARGE_RATE

    def test_small_rate_goes_to_one_member_only(self):
        # 250/250 would leave both below the 500 W minimum.
        rates = distribute_rate(500, [7500, 250], [5000, 3000], [500, 500])

        assert rates == pytest.approx([500, 0])

    def test_minimum_comes_first_then_the_remainder_by_weight(self):
        # A pure proportional split would be 1161 / 39 W and the 39 W would be
        # unusable. Instead both get their 500 W minimum and the remaining
        # 200 W are split 7500 : 250.
        rates = distribute_rate(1200, [7500, 250], [5000, 3000], [500, 500])

        assert rates == pytest.approx([693.55, 506.45], abs=0.01)
        assert sum(rates) == pytest.approx(1200)

    def test_two_minima_are_served_as_soon_as_the_rate_pays_for_them(self):
        # 500 W reaches one member, 1000 W reaches both.
        assert distribute_rate(
            500, [7500, 250], [5000, 3000], [500, 500]) == pytest.approx([500, 0])
        assert distribute_rate(
            1000, [7500, 250], [5000, 3000], [500, 500]) == pytest.approx([500, 500])

    def test_both_members_are_used_once_each_reaches_the_minimum(self):
        rates = distribute_rate(2000, [1000, 1000], [5000, 5000], [500, 500])

        assert rates == pytest.approx([1000, 1000])

    def test_the_requested_total_is_never_exceeded(self):
        for total in (300, 500, 700, 999, 1000, 1500, 2000):
            rates = distribute_rate(
                total, [7500, 250], [5000, 3000], [500, 500])
            assert sum(rates) == pytest.approx(total), total

    def test_the_member_with_most_free_capacity_is_preferred(self):
        rates = distribute_rate(600, [100, 9000], [5000, 5000], [500, 500])

        assert rates == pytest.approx([0, 600])

    def test_total_below_the_minimum_still_charges_one_member(self):
        """The logic layer already enforced the minimum on the group total."""
        rates = distribute_rate(300, [7500, 250], [5000, 3000], [500, 500])

        assert rates == pytest.approx([300, 0])

    def test_a_member_whose_limit_is_below_its_minimum_is_avoided(self):
        # Inverter 1 can never reach 500 W, so it is not used at all.
        rates = distribute_rate(2000, [9000, 9000], [5000, 300], [500, 500])

        assert rates == pytest.approx([2000, 0])

    def test_only_as_many_members_as_the_rate_can_pay_minima_for(self):
        # 1000 W buys exactly two minima; the third member (least free
        # capacity) gets nothing, and there is no remainder left to spread.
        rates = distribute_rate(
            1000, [9000, 5000, 1000], [5000, 5000, 5000], [500, 500, 500])

        assert rates == pytest.approx([500, 500, 0])

    def test_remainder_is_spread_only_over_the_served_members(self):
        # 1400 W buys two minima, the remaining 400 W split 9000 : 5000
        rates = distribute_rate(
            1400, [9000, 5000, 1000], [5000, 5000, 5000], [500, 500, 500])

        assert rates == pytest.approx([757.14, 642.86, 0], abs=0.01)
        assert sum(rates) == pytest.approx(1400)

    def test_no_minimum_keeps_the_plain_proportional_split(self):
        rates = distribute_rate(500, [7500, 250], [5000, 3000])

        assert rates == pytest.approx([483.87, 16.13], abs=0.01)


class TestGroupMinimumChargeRate:
    """The group applies the minimum through the inverter attributes."""

    @staticmethod
    def _members(**kwargs):
        return [
            RecordingInverter(soc=20, capacity=10000, max_soc=95,
                              max_grid_charge_rate=5000, **kwargs),
            RecordingInverter(soc=90, capacity=5000, max_soc=95,
                              max_grid_charge_rate=3000, inverter_num=1,
                              **kwargs),
        ]

    def test_force_charge_of_500_w_uses_one_inverter(self):
        members = self._members()

        InverterGroup(members).set_mode_force_charge(500)

        assert members[0].commands == [('force_charge', 500)]
        # The idle inverter must not discharge into the charging one
        assert members[1].commands == [('avoid_discharge', None)]

    def test_force_charge_respects_a_configured_higher_minimum(self):
        members = self._members(min_charge_rate=1500)

        # 2000 W would be 1935 / 65 W proportionally; inverter 1 cannot reach
        # its 1500 W minimum, so inverter 0 takes everything.
        InverterGroup(members).set_mode_force_charge(2000)

        assert members[0].commands == [('force_charge', 2000)]
        assert members[1].commands == [('avoid_discharge', None)]

    def test_large_force_charge_still_uses_both_inverters(self):
        members = self._members()

        InverterGroup(members).set_mode_force_charge(6000)

        assert members[0].commands == [('force_charge', 5000)]
        assert members[1].commands == [('force_charge', 1000)]

    def test_pv_limit_has_no_minimum_unless_configured(self):
        """min_pv_charge_rate defaults to 0, keeping the proportional split."""
        members = self._members(max_pv_charge_rate=4000)

        InverterGroup(members).set_mode_limit_battery_charge(600)

        assert members[0].commands == [('limit_battery_charge', 581)]
        assert members[1].commands == [('limit_battery_charge', 19)]

    def test_configured_pv_minimum_concentrates_the_limit(self):
        members = self._members(max_pv_charge_rate=4000,
                                min_pv_charge_rate=500)

        InverterGroup(members).set_mode_limit_battery_charge(600)

        assert members[0].commands == [('limit_battery_charge', 600)]
        assert members[1].commands == [('limit_battery_charge', 0)]
