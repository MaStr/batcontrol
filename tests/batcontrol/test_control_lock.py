"""Concurrency of the control cycle and external commands (R-A08/R-A09).

The control cycle (main thread), MQTT set callbacks (command worker) and evcc
callbacks (evcc network thread) share one control lock in Batcontrol. These
tests run the real default logic against a mocked inverter behind the
ResilientInverterWrapper and check that an external command can never be
overwritten by a decision the cycle made before the command arrived.

Every thread join and event wait has a timeout, so a deadlock fails the test
instead of hanging the suite.
"""
import dataclasses
import threading
from unittest.mock import MagicMock

import pytest

from batcontrol.core import (
    Batcontrol,
    MODE_ALLOW_DISCHARGING,
    MODE_AVOID_DISCHARGING,
    MODE_FORCE_CHARGING,
)
from batcontrol.inverter.resilient_wrapper import ResilientInverterWrapper
from batcontrol.logic import PeakShavingConfig
from batcontrol.logic.common import CommonLogic
from batcontrol.logic.default import DefaultLogic
from batcontrol.mqtt_api import MqttApi
from batcontrol.serial_executor import SerialExecutor

TIMEOUT = 5
# Long enough for an unsynchronised thread to finish its inverter call
BLOCKED_PROBE = 0.3

MODE_COMMANDS = (
    'set_mode_allow_discharge', 'set_mode_avoid_discharge',
    'set_mode_force_charge', 'set_mode_limit_battery_charge',
)


@pytest.fixture
def mock_config():
    return {
        'timezone': 'Europe/Berlin',
        'time_resolution_minutes': 60,
        'inverter': {
            'type': 'dummy',
            'max_grid_charge_rate': 5000,
            'max_pv_charge_rate': 3000,
            'min_pv_charge_rate': 0,
        },
        'utility': {'type': 'tibber', 'apikey': 'test_token'},
        'pvinstallations': [],
        'consumption_forecast': {'type': 'simple', 'value': 500},
        'battery_control': {
            'type': 'default',
            'max_charging_from_grid_limit': 0.8,
            'min_price_difference': 0.05,
        },
        'mqtt': {'enabled': False},
    }


@pytest.fixture
def setup(mock_config, mocker):
    """Batcontrol with providers that make the logic allow discharging."""
    backend = mocker.MagicMock()
    backend.max_pv_charge_rate = 3000
    backend.max_grid_charge_rate = 5000
    backend.min_soc = 10
    backend.max_soc = 100
    backend.get_max_capacity.return_value = 10000
    backend.get_SOC.return_value = 50
    backend.get_stored_energy.return_value = 5000
    backend.get_stored_usable_energy.return_value = 4500
    backend.get_free_capacity.return_value = 5000
    inverter = ResilientInverterWrapper(backend)

    tariff = mocker.MagicMock()
    tariff.get_prices.return_value = {0: 0.20, 1: 0.20, 2: 0.20}
    solar = mocker.MagicMock()
    solar.get_forecast.return_value = {0: 0, 1: 0, 2: 0}
    consumption = mocker.MagicMock()
    consumption.get_forecast.return_value = {0: 500, 1: 500, 2: 500}

    core_module = 'batcontrol.core'
    mocker.patch(f'{core_module}.tariff_factory.create_tarif_provider',
                 autospec=True, return_value=tariff)
    mocker.patch(f'{core_module}.inverter_factory.create_inverter',
                 autospec=True, return_value=inverter)
    mocker.patch(f'{core_module}.solar_factory.create_solar_provider',
                 autospec=True, return_value=solar)
    mocker.patch(f'{core_module}.consumption_factory.create_consumption',
                 autospec=True, return_value=consumption)

    CommonLogic._instance = None  # pylint: disable=protected-access
    bc = Batcontrol(mock_config)
    yield bc, backend, tariff
    bc.shutdown()
    CommonLogic._instance = None  # pylint: disable=protected-access


def _mode_commands(backend):
    """Names of the mode commands the inverter received, in order."""
    return [name for name, _args, _kwargs in backend.method_calls
            if name in MODE_COMMANDS]


def test_evcc_block_between_decision_and_apply_wins(setup):
    """Regression R-A08: the cycle has checked discharge_blocked (False)
    and is about to apply allow discharging when evcc sets the block.
    The block must end up in effect, not the stale decision."""
    bc, backend, _tariff = setup
    evcc_state = {}

    def evcc_blocks_now(*_args):
        # Called by the cycle after the discharge block check and before
        # the mode command (peak shaving state publish).
        evcc = threading.Thread(target=bc.set_discharge_blocked, args=(True,))
        evcc.start()
        evcc.join(timeout=BLOCKED_PROBE)
        evcc_state['thread'] = evcc
        evcc_state['waited_for_cycle'] = evcc.is_alive()

    bc.mqtt_api = MagicMock()
    bc.mqtt_api.publish_peak_shaving_charge_limit.side_effect = \
        evcc_blocks_now

    bc.run()
    evcc_state['thread'].join(timeout=TIMEOUT)

    assert not evcc_state['thread'].is_alive()
    assert evcc_state['waited_for_cycle']
    assert _mode_commands(backend) == [
        'set_mode_allow_discharge', 'set_mode_avoid_discharge']
    assert bc.discharge_blocked is True
    assert bc.last_mode == MODE_AVOID_DISCHARGING


def test_api_command_waits_for_cycle_and_stays_in_effect(setup):
    """An MQTT mode command that arrives during the calculation is applied
    after the cycle, so the cycle cannot overwrite it."""
    bc, backend, _tariff = setup
    api_state = {}
    original_set_params = DefaultLogic.set_calculation_parameters

    def api_arrives(logic, params):
        api = threading.Thread(
            target=bc.api_set_mode, args=(MODE_FORCE_CHARGING,))
        api.start()
        api.join(timeout=BLOCKED_PROBE)
        api_state['thread'] = api
        api_state['waited_for_cycle'] = api.is_alive()
        original_set_params(logic, params)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(DefaultLogic, 'set_calculation_parameters', api_arrives)
        bc.run()
    api_state['thread'].join(timeout=TIMEOUT)

    assert not api_state['thread'].is_alive()
    assert api_state['waited_for_cycle']
    assert _mode_commands(backend) == [
        'set_mode_allow_discharge', 'set_mode_force_charge']
    assert bc.last_mode == MODE_FORCE_CHARGING
    assert bc.api_overwrite is True


def test_forecast_fetch_does_not_hold_the_control_lock(setup):
    """Provider fetches (HTTP) run before the lock is taken: an external
    command does not wait for them."""
    bc, _backend, tariff = setup
    prices = tariff.get_prices.return_value
    setter_state = {}

    def slow_fetch(*_args, **_kwargs):
        setter = threading.Thread(
            target=bc.api_set_min_price_difference, args=(0.07,))
        setter.start()
        setter.join(timeout=TIMEOUT)
        setter_state['finished'] = not setter.is_alive()
        return prices

    tariff.get_prices.side_effect = slow_fetch

    bc.run()

    assert setter_state['finished']
    assert bc.min_price_difference == 0.07


def test_mqtt_command_during_inverter_wait_does_not_deadlock(setup):
    """The MQTT inverter delivers its SOC on paho's network thread, and the
    cycle waits for it while holding the control lock. A set message
    queued before the SOC message must not block the network thread, or
    the SOC message never arrives (deadlock until the startup window ends).
    """
    bc, backend, _tariff = setup
    executor = SerialExecutor('test-commands', lock=bc._control_lock)  # pylint: disable=protected-access
    mqtt = MagicMock(spec=MqttApi)
    mqtt.callbacks = {
        'bc/mode/set': {'function': bc.api_set_mode, 'convert': int}}
    mqtt._command_executor = executor  # pylint: disable=protected-access
    mqtt._handle_message = MqttApi._handle_message.__get__(mqtt, MqttApi)
    mqtt._dispatch = MqttApi._dispatch.__get__(mqtt, MqttApi)

    soc_received = threading.Event()
    soc_state = {}

    def network_thread():
        message = MagicMock(topic='bc/mode/set', payload=b'-1')
        mqtt._handle_message(None, None, message)
        soc_received.set()  # the SOC message right behind it

    def get_soc_waiting_for_mqtt():
        if 'network' not in soc_state:
            soc_state['network'] = threading.Thread(target=network_thread)
            soc_state['network'].start()
            soc_state['received'] = soc_received.wait(TIMEOUT)
        return 50

    backend.get_SOC.side_effect = get_soc_waiting_for_mqtt

    try:
        cycle = threading.Thread(target=bc.run)
        cycle.start()
        cycle.join(timeout=3 * TIMEOUT)
        assert not cycle.is_alive()
        assert soc_state['received']
        assert executor.wait_idle(TIMEOUT)
    finally:
        executor.shutdown()

    # The command was applied after the cycle and stays in effect
    assert _mode_commands(backend) == [
        'set_mode_allow_discharge', 'set_mode_force_charge']
    assert bc.last_mode == MODE_FORCE_CHARGING


def test_commands_after_shutdown_are_ignored(setup):
    """Once shutdown() restored the inverter, late evcc/MQTT commands must
    not command it again (they used to fail on the deleted inverter)."""
    bc, backend, _tariff = setup
    bc.shutdown()
    backend.reset_mock()

    bc.set_discharge_blocked(True)
    bc.api_set_mode(MODE_ALLOW_DISCHARGING)

    assert not bc.discharge_blocked
    assert _mode_commands(backend) == []


def test_shutdown_waits_for_running_command(setup):
    """shutdown() restores the inverter only after a running external
    command has finished its inverter call."""
    bc, backend, _tariff = setup
    entered = threading.Event()
    release = threading.Event()

    def slow_command(*_args):
        entered.set()
        assert release.wait(TIMEOUT)

    backend.set_mode_avoid_discharge.side_effect = slow_command
    evcc = threading.Thread(target=bc.set_discharge_blocked, args=(True,))
    evcc.start()
    assert entered.wait(TIMEOUT)
    stopper = threading.Thread(target=bc.shutdown)
    stopper.start()
    stopper.join(timeout=BLOCKED_PROBE)
    assert stopper.is_alive()
    backend.shutdown.assert_not_called()

    release.set()
    evcc.join(timeout=TIMEOUT)
    stopper.join(timeout=TIMEOUT)
    assert not stopper.is_alive()
    names = [name for name, _a, _k in backend.method_calls]
    assert names.index('set_mode_avoid_discharge') < names.index('shutdown')


def test_peak_shaving_config_is_read_once_per_cycle(setup, mocker):
    """Regression R-A09: the enabled flag and the rest of the peak shaving
    config handed to the logic come from the same config object."""
    bc, _backend, _tariff = setup
    original = PeakShavingConfig(enabled=True, price_limit=0.10)
    replaced = dataclasses.replace(original, enabled=False, price_limit=0.30)
    bc.peak_shaving_config = original

    class SwappingEvcc:
        """Replaces the config while the cycle evaluates evcc, the moment
        between the two reads of the old code."""
        evcc_ev_expects_pv_surplus = False

        def shutdown(self):
            """Called by Batcontrol.shutdown()"""

        @property
        def evcc_is_charging(self):
            bc.peak_shaving_config = replaced
            return False

    bc.evcc_api = SwappingEvcc()
    spy = mocker.spy(DefaultLogic, 'set_calculation_parameters')

    bc.run()

    params = spy.call_args.args[1]
    assert params.peak_shaving == original
