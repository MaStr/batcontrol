"""Tests for CLI data-directory selection and explicit startup cache clearing."""

from pathlib import Path

import pytest

from batcontrol import __main__ as cli


def test_default_data_directory_is_independent_of_config(tmp_path, monkeypatch):
    """The shared data folder follows the working directory, not the config."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        'sys.argv', ['batcontrol', '--config', '/elsewhere/live.yaml'])

    args = cli.parse_arguments()

    assert args.data_dir == Path('data')
    assert not args.clear_cache


def test_cli_accepts_data_directory_and_clear_cache(tmp_path, monkeypatch):
    """HA and standalone deployments can select a persistent shared folder."""
    monkeypatch.setattr('sys.argv', [
        'batcontrol', '--data-dir', str(
            tmp_path), '--clear-cache', '--one-shot',
    ])

    args = cli.parse_arguments()

    assert args.data_dir == tmp_path
    assert args.clear_cache
    assert args.one_shot


@pytest.fixture
def runtime(mocker, monkeypatch, tmp_path):
    """Keep CLI tests away from inverter, logging files and process signals."""
    monkeypatch.setattr('sys.argv', [
        'batcontrol', '--data-dir', str(tmp_path), '--one-shot',
    ])
    mocker.patch.object(cli, 'load_config', return_value={
                        'logfile_enabled': False})
    mocker.patch.object(cli, 'setup_logging')
    mocker.patch.object(cli, 'log_startup_banner')
    mocker.patch.object(cli.signal, 'signal')
    return mocker.patch.object(cli, 'Batcontrol')


def test_cli_forwards_data_directory(runtime, tmp_path):
    """Both provider families receive their root through core initialization."""
    assert cli.main() == 0
    runtime.assert_called_once_with(
        {'logfile_enabled': False}, data_directory=tmp_path,
    )
    runtime.return_value.run.assert_called_once()
    runtime.return_value.shutdown.assert_called_once()


@pytest.mark.parametrize('clear', [False, True])
def test_clear_cache_runs_before_provider_initialization(
        runtime, tmp_path, monkeypatch, clear):
    """Only an explicit clear removes caches, including inactive providers."""
    cache = tmp_path / 'cache' / 'solar' / 'old-provider' / 'roof.json'
    cache.parent.mkdir(parents=True)
    cache.write_text('old-cache', encoding='utf-8')
    unrelated = tmp_path / 'keep.json'
    unrelated.write_text('other application data', encoding='utf-8')
    if clear:
        monkeypatch.setattr('sys.argv', [
            'batcontrol', '--data-dir', str(
                tmp_path), '--clear-cache', '--one-shot',
        ])

    def construct(*_args, **_kwargs):
        assert cache.exists() is not clear
        return runtime.return_value

    runtime.side_effect = construct
    assert cli.main() == 0
    assert unrelated.read_text(encoding='utf-8') == 'other application data'


def test_clear_missing_directory_starts_normally(runtime, tmp_path, monkeypatch):
    """A first-ever startup with --clear-cache requires no existing directory."""
    monkeypatch.setattr('sys.argv', [
        'batcontrol', '--data-dir', str(tmp_path /
                                        'new'), '--clear-cache', '--one-shot',
    ])

    assert cli.main() == 0
    runtime.return_value.run.assert_called_once()


def test_failed_clear_aborts_without_loading_old_cache(
        runtime, tmp_path, mocker, monkeypatch, caplog):
    """An explicit clear cannot silently restart with the old disk contents."""
    monkeypatch.setattr('sys.argv', [
        'batcontrol', '--data-dir', str(
            tmp_path), '--clear-cache', '--one-shot',
    ])
    mocker.patch('shutil.rmtree', side_effect=PermissionError(
        'private-file-detail'))

    assert cli.main() == 1
    runtime.assert_not_called()
    assert 'Could not clear provider cache' in caplog.text
    assert 'private-file-detail' not in caplog.text


def test_clear_does_not_follow_cache_directory_symlink(
        runtime, tmp_path, monkeypatch):
    """A symlinked cache root must never cause deletion outside the cache."""
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    protected = elsewhere / 'keep'
    protected.write_text('keep', encoding='utf-8')
    (tmp_path / 'cache').symlink_to(elsewhere, target_is_directory=True)
    monkeypatch.setattr('sys.argv', [
        'batcontrol', '--data-dir', str(
            tmp_path), '--clear-cache', '--one-shot',
    ])

    assert cli.main() == 1
    runtime.assert_not_called()
    assert protected.read_text(encoding='utf-8') == 'keep'
