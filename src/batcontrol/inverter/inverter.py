""" Factory for inverter providers """

import logging
from contextlib import suppress
from .baseclass import (
    DEFAULT_MAX_SOC,
    DEFAULT_MIN_CHARGE_RATE,
    DEFAULT_MIN_SOC,
)
from .fronius_modbus import FroniusModbusGridStatusReader
from .fronius_modbus import FroniusModbusInverter
from .fronius_modbus import FroniusModbusTcpTransport
from .group import InverterGroup
from .inverter_interface import InverterInterface
from .resilient_wrapper import (
    ResilientInverterWrapper,
    DEFAULT_OUTAGE_TOLERANCE_SECONDS,
)

logger = logging.getLogger(__name__)


class Inverter:
    """ Factory for inverter providers """
    # Instances of the inverter classes are created here
    num_inverters = 0

    @staticmethod
    def create_inverters(config) -> InverterInterface:
        """ Create the inverter(s) described by the 'inverter' config section.

        The section is either a single mapping (one inverter) or a list of
        mappings (several inverters). Several inverters are combined into an
        InverterGroup, which behaves like a single, larger battery.

        Returns:
            InverterInterface: a single inverter or an InverterGroup.
        """
        if isinstance(config, dict):
            return Inverter.create_inverter(config)

        if not isinstance(config, list):
            raise RuntimeError(
                '[Inverter] The inverter configuration must be a mapping or a '
                f'list of mappings, got {type(config).__name__}'
            )
        if not config:
            raise RuntimeError(
                '[Inverter] The inverter configuration list is empty'
            )
        for index, entry in enumerate(config):
            if not isinstance(entry, dict):
                raise RuntimeError(
                    f'[Inverter] Inverter entry {index} must be a mapping, '
                    f'got {type(entry).__name__}'
                )

        Inverter._check_unique_mqtt_topics(config)

        if len(config) == 1:
            return Inverter.create_inverter(config[0], inverter_num=0)

        inverters = []
        try:
            for num, entry in enumerate(config):
                inverters.append(
                    Inverter.create_inverter(entry, inverter_num=num))
        except Exception:
            # Release whatever was already opened before re-raising.
            for created in inverters:
                with suppress(Exception):
                    created.shutdown()
            raise

        return InverterGroup(inverters)

    @staticmethod
    def _check_unique_mqtt_topics(config: list) -> None:
        """ Reject MQTT inverters that would share the same base topic.

        Two MQTT inverters on one topic would read the same SOC and send
        their commands to the same receiver, which is always a config error.
        """
        seen = {}
        for index, entry in enumerate(config):
            if str(entry.get('type', '')).lower() != 'mqtt':
                continue
            base_topic = entry.get('base_topic', 'default')
            if base_topic == 'default':
                # Resolved per inverter_num later, so always unique.
                continue
            base_topic = str(base_topic).rstrip('/')
            if base_topic in seen:
                raise RuntimeError(
                    f'[Inverter] MQTT inverters {seen[base_topic]} and {index} '
                    f'share the base_topic "{base_topic}". Every MQTT '
                    'inverter needs its own base_topic.'
                )
            seen[base_topic] = index

    @staticmethod
    def create_inverter(config: dict, inverter_num: int = None) -> InverterInterface:
        """ Select and configure an inverter based on the given configuration

        Args:
            config: the configuration of this single inverter.
            inverter_num: explicit index used for the MQTT topic of this
                inverter. If omitted, a process-wide counter is used.
        """
        # renaming of parameters max_charge_rate -> max_grid_charge_rate
        if not 'max_grid_charge_rate' in config.keys():
            config['max_grid_charge_rate'] = config['max_charge_rate']

        # introducing parameter max_pv_charge_rate. Assign default value here,
        # in case there is no value defined in the config file to avoid a KeyError
        if not 'max_pv_charge_rate' in config.keys():
            config['max_pv_charge_rate'] = 0

        inverter = None

        if config['type'].lower() == 'fronius_gen24':
            from .fronius import FroniusWR

            iv_config = {
                'address': config['address'],
                'user': config['user'],
                'password': config['password'],
                'max_grid_charge_rate': config['max_grid_charge_rate'],
                'max_pv_charge_rate': config['max_pv_charge_rate'],
                'fronius_inverter_id': config.get('fronius_inverter_id', 1),
                'fronius_controller_id': config.get('fronius_controller_id', 0),
                'capacity': config.get('capacity', -1)
            }
            inverter=FroniusWR(iv_config)
        elif config['type'].lower() == 'dummy':
            from .dummy import Dummy
            iv_config = {
                'max_grid_charge_rate': config['max_grid_charge_rate'],
                'max_pv_charge_rate': config['max_pv_charge_rate'],
            }
            # Optional overrides of the simulated battery, used to demo
            # several differently sized/charged batteries without hardware.
            for key in ('capacity', 'soc', 'min_soc', 'max_soc'):
                if config.get(key) is not None:
                    iv_config[key] = config[key]
            inverter=Dummy(iv_config)
        elif config['type'].lower() == 'mqtt':
            from .mqtt_inverter import MqttInverter
            iv_config = {
                'base_topic': config.get('base_topic', 'default'),
                'capacity': config['capacity'],
                'min_soc': config.get('min_soc', DEFAULT_MIN_SOC),
                'max_soc': config.get('max_soc', DEFAULT_MAX_SOC),
                'max_grid_charge_rate': config['max_grid_charge_rate'],
                **(
                    {'cache_ttl': config['cache_ttl']}
                    if config.get('cache_ttl') is not None else {}
                )
            }
            inverter=MqttInverter(iv_config)
        elif config['type'].lower() == 'fronius-modbus':
            inverter = Inverter._create_fronius_modbus(config)
        else:
            raise RuntimeError(f'[Inverter] Unknown inverter type {config["type"]}')

        if inverter_num is None:
            inverter.inverter_num = Inverter.num_inverters
            Inverter.num_inverters += 1
        else:
            inverter.inverter_num = inverter_num

        # Apply the rate limits for every backend in one place. InverterGroup
        # needs them on the inverter to split a group-wide rate correctly, and
        # not every backend reads them from its own config.
        inverter.max_pv_charge_rate = config['max_pv_charge_rate']
        inverter.min_charge_rate = config.get(
            'min_charge_rate', DEFAULT_MIN_CHARGE_RATE)
        inverter.min_pv_charge_rate = config.get('min_pv_charge_rate', 0)

        return Inverter._wrap_resilient(inverter, config)

    @staticmethod
    def _create_fronius_modbus(config: dict):
        """ Build a Fronius Modbus inverter, closing transports on failure. """
        transport = None
        extra_transports = []
        try:
            transport = FroniusModbusTcpTransport(
                config['address'],
                port=config.get('port', 502),
                unit_id=config.get('unit_id', 1),
            )
            grid_status_reader = None
            if config.get('backup_mode_safety_enabled', False):
                meter_transport = FroniusModbusTcpTransport(
                    config['address'],
                    port=config.get('port', 502),
                    unit_id=config.get('meter_unit_id', 200),
                )
                extra_transports.append(meter_transport)
                grid_status_reader = FroniusModbusGridStatusReader(
                    transport,
                    meter_transport,
                )
            return FroniusModbusInverter(
                transport,
                max_charge_rate=config['max_grid_charge_rate'],
                capacity=config['capacity'],
                min_soc=config.get('min_soc', DEFAULT_MIN_SOC),
                max_soc=config.get('max_soc', DEFAULT_MAX_SOC),
                revert_seconds=config.get('revert_seconds', 0),
                grid_status_reader=grid_status_reader,
                extra_transports=extra_transports,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            for opened_transport in [transport, *extra_transports]:
                close = getattr(opened_transport, 'close', None)
                if close is not None:
                    with suppress(Exception):
                        close()
            raise

    @staticmethod
    def _wrap_resilient(inverter, config: dict) -> InverterInterface:
        """ Wrap an inverter for graceful outage handling, if enabled.

        The wrapper stays around the individual inverter, also when several
        inverters are combined into a group, so every device keeps its own
        outage clock.
        """
        # Check if resilient wrapper is enabled (default: True)
        if not config.get('enable_resilient_wrapper', True):
            logger.info('Resilient wrapper disabled by configuration')
            return inverter

        # Get outage tolerance from config (default: 24 minutes)
        outage_tolerance = config.get(
            'outage_tolerance_minutes',
            DEFAULT_OUTAGE_TOLERANCE_SECONDS / 60
        ) * 60  # Convert to seconds

        logger.info(
            'Wrapping inverter with resilient wrapper '
            '(outage tolerance: %.1f min)',
            outage_tolerance / 60,
        )
        return ResilientInverterWrapper(
            inverter,
            outage_tolerance_seconds=outage_tolerance,
        )
