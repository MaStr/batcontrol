"""Tests for the per-inverter backup files of the Fronius GEN24 inverter.

Regression for R-I01: all Fronius inverters shared one backup file, so with
two inverters only the first one saved its time-of-use schedule and the
second one was left on batcontrol's rules after shutdown.
"""
import json
import os
import re
from unittest.mock import Mock, patch

import pytest

from batcontrol.inverter import fronius
from batcontrol.inverter.fronius import FroniusWR, backup_file_id

# Captured at import, before the autouse conftest fixture patches it.
ORIGINAL_DEFAULT_BACKUP_DIRECTORY = fronius.DEFAULT_BACKUP_DIRECTORY

TOU_ORIGINAL = [{
    'Active': True, 'Power': 1234, 'ScheduleType': 'DISCHARGE_MAX',
    'TimeTable': {'Start': '00:00', 'End': '06:00'},
    'Weekdays': {d: True for d in
                 ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']},
}]


def _bare_inverter(address, backup_directory):
    """FroniusWR without running __init__ (no HTTP)."""
    inverter = FroniusWR.__new__(FroniusWR)
    inverter.address = address
    inverter._setup_backup_files(backup_directory)
    return inverter


class TestBackupFileId:
    def test_is_stable_and_filename_safe(self):
        file_id = backup_file_id('192.168.1.100')
        assert file_id == backup_file_id('192.168.1.100')
        assert len(file_id) == 16
        assert re.fullmatch(r'[A-Za-z0-9_-]+', file_id)

    def test_normalizes_case_and_whitespace(self):
        assert backup_file_id(' Fronius.LAN ') == backup_file_id('fronius.lan')

    def test_differs_per_address(self):
        assert backup_file_id('192.168.1.100') != backup_file_id('192.168.1.101')


class TestBackupLocation:
    def test_files_below_backup_directory_named_by_address(self, tmp_path):
        inverter = _bare_inverter('192.168.1.100', tmp_path / 'fronius')
        file_id = backup_file_id('192.168.1.100')

        assert inverter.timeofuse_config_filename == str(
            tmp_path / 'fronius' / f'timeofuse_config_{file_id}.json')
        assert inverter.battery_config_filename == str(
            tmp_path / 'fronius' / f'battery_config_{file_id}.json')
        assert (tmp_path / 'fronius').is_dir()

    def test_without_directory_falls_back_to_default(self):
        inverter = _bare_inverter('192.168.1.100', None)
        assert os.path.dirname(inverter.timeofuse_config_filename) == \
            fronius.DEFAULT_BACKUP_DIRECTORY

    def test_default_directory_is_config(self, tmp_path, monkeypatch):
        """Without a data directory the backups stay in ./config."""
        monkeypatch.setattr(fronius, 'DEFAULT_BACKUP_DIRECTORY',
                            ORIGINAL_DEFAULT_BACKUP_DIRECTORY)
        monkeypatch.chdir(tmp_path)

        inverter = _bare_inverter('192.168.1.100', None)

        assert os.path.dirname(inverter.timeofuse_config_filename) == 'config'
        assert (tmp_path / 'config').is_dir()

    def test_unusable_backup_directory_fails_fast(self, tmp_path):
        blocker = tmp_path / 'not-a-dir'
        blocker.write_text('x', encoding='utf-8')
        with pytest.raises(RuntimeError, match='backup directory'):
            _bare_inverter('192.168.1.100', blocker / 'fronius')

    def test_battery_config_written_to_per_inverter_file(self, tmp_path):
        inverter = _bare_inverter('192.168.1.100', tmp_path / 'fronius')
        inverter.api_config = Mock(config_battery_path='/config/batteries')
        inverter.send_request = Mock(
            return_value=Mock(text='{"BAT_M0_SOC_MIN": 5}'))

        assert inverter.get_battery_config() == {'BAT_M0_SOC_MIN': 5}
        with open(inverter.battery_config_filename, encoding='utf-8') as f:
            assert json.load(f) == {'BAT_M0_SOC_MIN': 5}

    def test_init_resolves_backup_files_before_talking_to_inverter(
            self, tmp_path):
        """get_battery_config/backup_time_of_use write the files, so the
        names must be set before the first request."""
        config = {
            'address': '192.168.1.100', 'user': 'customer',
            'password': 'pw', 'max_grid_charge_rate': 5000,
            'max_pv_charge_rate': 0,
            'backup_directory': str(tmp_path / 'fronius'),
        }
        seen = {}

        def stop(inverter):
            seen['filename'] = inverter.timeofuse_config_filename
            raise RuntimeError('stop')

        with patch.object(FroniusWR, 'get_firmware_version',
                          autospec=True, side_effect=stop):
            with pytest.raises(RuntimeError, match='stop'):
                FroniusWR(config)

        assert seen['filename'].startswith(str(tmp_path / 'fronius'))

    def test_two_inverters_keep_separate_time_of_use_backups(self, tmp_path):
        """Regression R-I01: each inverter restores its own schedule."""
        backup_dir = tmp_path / 'fronius'
        first = _bare_inverter('192.168.1.100', backup_dir)
        second = _bare_inverter('192.168.1.101', backup_dir)
        second_original = [dict(TOU_ORIGINAL[0], Power=4321)]
        first.get_time_of_use = Mock(return_value=TOU_ORIGINAL)
        second.get_time_of_use = Mock(return_value=second_original)

        first.backup_time_of_use()
        second.backup_time_of_use()

        first.set_time_of_use = Mock()
        second.set_time_of_use = Mock()
        first.restore_time_of_use_config()
        second.restore_time_of_use_config()

        assert first.set_time_of_use.call_args.args[0][0]['Power'] == 1234
        assert second.set_time_of_use.call_args.args[0][0]['Power'] == 4321
        assert not os.path.exists(first.timeofuse_config_filename)
        assert not os.path.exists(second.timeofuse_config_filename)
