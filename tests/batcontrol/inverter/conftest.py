"""Shared fixtures for the inverter tests."""
import pytest

from batcontrol.inverter import fronius


@pytest.fixture(autouse=True)
def isolate_fronius_backup_files(tmp_path, monkeypatch):
    """Keep Fronius settings backups out of the working directory.

    FroniusWR creates its backup directory and migrates legacy files at
    construction time; without this, tests would touch ./config of the
    checkout they run in.
    """
    monkeypatch.setattr(
        fronius, 'DEFAULT_BACKUP_DIRECTORY', str(tmp_path / 'backup-default'))
    monkeypatch.setattr(
        fronius, 'LEGACY_TIMEOFUSE_CONFIG_FILENAME',
        str(tmp_path / 'legacy-config' / 'timeofuse_config.json'))
    monkeypatch.setattr(
        fronius, 'LEGACY_BATTERY_CONFIG_FILENAME',
        str(tmp_path / 'legacy-config' / 'battery_config.json'))
