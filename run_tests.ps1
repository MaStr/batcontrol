# PowerShell version of run_tests.sh

uv venv --python 3.13 --allow-existing

# Activate the virtual environment created by uv
. .\.venv\Scripts\Activate.ps1

# Install the package together with test dependencies from pyproject.toml
uv pip install -e '.[test]'

# Run pytest with coverage and logging options
$params = @(
    'tests/',
    '--cov=src/batcontrol',
    '--log-cli-level=DEBUG',
    '--log-cli-format=%(asctime)s [%(levelname)8s] %(name)s: %(message)s',
    '--log-cli-date-format=%Y-%m-%d %H:%M:%S'
)

uv run pytest @params

exit $LASTEXITCODE
