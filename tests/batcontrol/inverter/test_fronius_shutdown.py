"""Tests for the shutdown (settings restore) of the Fronius GEN24 inverter.

Regression for R-I17: a failed restore of the battery config or the
time-of-use schedule was only logged at debug level by the
ResilientInverterWrapper, and a failed battery config restore also skipped
the time-of-use restore and the logout.
"""
import logging
from unittest.mock import Mock

import pytest

from batcontrol.inverter.fronius import FroniusWR
from batcontrol.inverter.group import InverterGroup
from batcontrol.inverter.resilient_wrapper import ResilientInverterWrapper


def _bare_inverter(address='192.168.1.100', inverter_num=0):
    """FroniusWR without running __init__ (no HTTP), restore steps mocked."""
    inverter = FroniusWR.__new__(FroniusWR)
    inverter.address = address
    inverter.inverter_num = inverter_num
    inverter.inverter_type = 'fronius_gen24'
    inverter.restore_battery_config = Mock()
    inverter.restore_time_of_use_config = Mock()
    inverter.logout = Mock()
    return inverter


class TestFroniusShutdown:
    def test_all_steps_run_on_success(self):
        inverter = _bare_inverter()

        inverter.shutdown()

        inverter.restore_battery_config.assert_called_once()
        inverter.restore_time_of_use_config.assert_called_once()
        inverter.logout.assert_called_once()

    def test_failed_battery_restore_does_not_skip_later_steps(self):
        inverter = _bare_inverter()
        inverter.restore_battery_config.side_effect = RuntimeError(
            'failed to restore battery config')

        with pytest.raises(RuntimeError) as excinfo:
            inverter.shutdown()

        inverter.restore_time_of_use_config.assert_called_once()
        inverter.logout.assert_called_once()
        message = str(excinfo.value)
        assert '192.168.1.100' in message
        assert 'battery config restore' in message
        assert 'failed to restore battery config' in message
        assert 'time of use' not in message

    def test_all_failures_are_reported_together(self):
        inverter = _bare_inverter()
        inverter.restore_battery_config.side_effect = RuntimeError('bat')
        inverter.restore_time_of_use_config.side_effect = KeyError('Power')
        inverter.logout.side_effect = ConnectionError('offline')

        with pytest.raises(RuntimeError) as excinfo:
            inverter.shutdown()

        message = str(excinfo.value)
        assert 'battery config restore: RuntimeError: bat' in message
        assert "time of use restore: KeyError: 'Power'" in message
        assert 'logout: ConnectionError: offline' in message


class TestFroniusShutdownLogging:
    """The wrapped inverter must never raise but log the failure as error."""

    @staticmethod
    def _errors(caplog):
        return [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_wrapped_failed_restore_is_logged_with_identity(self, caplog):
        inverter = _bare_inverter()
        inverter.restore_time_of_use_config.side_effect = RuntimeError(
            'Failed to set time of use configuration')

        with caplog.at_level(logging.DEBUG):
            ResilientInverterWrapper(inverter).shutdown()

        errors = self._errors(caplog)
        assert len(errors) == 1
        message = errors[0].getMessage()
        assert 'Inverter 0 (fronius_gen24)' in message
        assert '192.168.1.100' in message
        assert 'Failed to set time of use configuration' in message

    def test_group_of_wrapped_fronius_logs_each_failure(self, caplog):
        first = _bare_inverter('10.0.0.1', inverter_num=0)
        first.restore_battery_config.side_effect = RuntimeError('bat down')
        second = _bare_inverter('10.0.0.2', inverter_num=1)
        second.restore_battery_config.side_effect = RuntimeError('bat down')

        group = InverterGroup.__new__(InverterGroup)
        group._inverters = [ResilientInverterWrapper(first),
                            ResilientInverterWrapper(second)]
        with caplog.at_level(logging.DEBUG):
            group.shutdown()

        messages = [r.getMessage() for r in self._errors(caplog)]
        assert len(messages) == 2
        assert 'Inverter 0 (fronius_gen24)' in messages[0]
        assert '10.0.0.1' in messages[0]
        assert 'Inverter 1 (fronius_gen24)' in messages[1]
        assert '10.0.0.2' in messages[1]
        second.logout.assert_called_once()


class TestLogout:
    def test_no_response_does_not_raise(self):
        inverter = FroniusWR.__new__(FroniusWR)
        inverter.api_config = Mock(commands_logout_path='/logout')
        inverter.send_request = Mock(return_value=None)

        assert inverter.logout() is None


class TestRestoreTimeOfUseBackup:
    """restore_time_of_use_config must not hide a broken backup (PR #513)."""

    def _inverter(self, path):
        inverter = FroniusWR.__new__(FroniusWR)
        inverter.timeofuse_config_filename = str(path)
        inverter.set_time_of_use = Mock()
        return inverter

    def test_missing_backup_is_nothing_to_restore(self, tmp_path):
        inverter = self._inverter(tmp_path / 'missing.json')
        inverter.restore_time_of_use_config()
        inverter.set_time_of_use.assert_not_called()

    def test_unreadable_backup_raises_with_path(self, tmp_path):
        backup = tmp_path / 'tou.json'
        backup.mkdir()  # a directory cannot be read as a file
        inverter = self._inverter(backup)
        with pytest.raises(RuntimeError, match='not readable'):
            inverter.restore_time_of_use_config()
        inverter.set_time_of_use.assert_not_called()

    def test_invalid_json_raises_with_path(self, tmp_path):
        backup = tmp_path / 'tou.json'
        backup.write_text('{broken', encoding='utf-8')
        inverter = self._inverter(backup)
        with pytest.raises(RuntimeError, match='tou.json is not valid JSON'):
            inverter.restore_time_of_use_config()

    def test_broken_backup_is_reported_by_shutdown(self, tmp_path):
        backup = tmp_path / 'tou.json'
        backup.write_text('{broken', encoding='utf-8')
        inverter = _bare_inverter()
        inverter.timeofuse_config_filename = str(backup)
        del inverter.restore_time_of_use_config  # use the real method
        with pytest.raises(RuntimeError, match='time of use restore: .*not valid JSON'):
            inverter.shutdown()
        inverter.logout.assert_called_once()
