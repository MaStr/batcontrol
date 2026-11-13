"""Execute container entrypoints with fake Python and private temporary paths."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('entrypoint', ['entrypoint.sh', 'entrypoint_ha.sh'])
def test_entrypoint_selects_persistent_data_directory_and_forwards_arguments(
        tmp_path, entrypoint):
    """Both deployment shells forward --clear-cache without running control."""
    app = tmp_path / 'app'
    ha_data = tmp_path / 'ha-data'
    for folder in ('config', 'logs', 'config_template', 'addon_config'):
        (app / folder).mkdir(parents=True)
    ha_data.mkdir()
    (app / 'config' / '.init').touch()
    (app / 'config' / 'batcontrol_config.yaml').touch()
    (ha_data / 'options.json').write_text('{}', encoding='utf-8')
    script = tmp_path / entrypoint
    body = (ROOT / entrypoint).read_text(encoding='utf-8')
    body = body.replace('/data/', f'{ha_data}/').replace('/app/', f'{app}/')
    script.write_text(body, encoding='utf-8')
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    executable = bindir / 'python'
    executable.write_text(
        f'#!{sys.executable}\n'
        'import json, os, sys\n'
        'with open(os.environ["CAPTURE_ARGV"], "w", encoding="utf-8") as target:\n'
        '    json.dump(sys.argv[1:], target)\n',
        encoding='utf-8',
    )
    executable.chmod(0o700)
    capture = tmp_path / 'argv.json'
    env = {**os.environ, 'PATH': f'{bindir}:{os.environ["PATH"]}',
           'CAPTURE_ARGV': str(capture), 'BATCONTROL_VERSION': 'test'}

    subprocess.run(
        ['sh', str(script), '--clear-cache', '--one-shot'],
        env=env, check=True, capture_output=True, text=True,
    )

    expected = (ha_data / 'data' if entrypoint == 'entrypoint_ha.sh'
                else app / 'data')
    assert json.loads(capture.read_text(encoding='utf-8')) == [
        '-m', 'batcontrol', '--data-dir', str(
            expected), '--clear-cache', '--one-shot',
    ]


def test_docker_image_declares_persistent_data_volume():
    """The standard Docker image owns the same data path its shell selects."""
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    volume_line = next(line for line in dockerfile.splitlines()
                       if line.startswith('VOLUME '))
    assert '/app/data' in json.loads(volume_line.removeprefix('VOLUME '))
    assert '/app/data' in next(line for line in dockerfile.splitlines()
                               if line.startswith('RUN mkdir -p /app'))
