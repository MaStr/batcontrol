"""Shared fixtures for the inverter tests."""
import pytest

from batcontrol.inverter import fronius


@pytest.fixture(autouse=True)
def isolate_fronius_backup_files(tmp_path, monkeypatch):
    """Keep Fronius settings backups out of the working directory.

    FroniusWR creates its backup directory at construction time; without
    this, tests would create ./config in the checkout they run in.
    """
    monkeypatch.setattr(
        fronius, 'DEFAULT_BACKUP_DIRECTORY', str(tmp_path / 'backup-default'))
