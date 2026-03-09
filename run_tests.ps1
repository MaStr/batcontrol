# PowerShell version of run_tests.sh

# Activate virtual environment if it exists (Windows path used by venv)
if (Test-Path -Path .\.venv\Scripts\Activate.ps1) {
    . .\.venv\Scripts\Activate.ps1
}

# Ensure pytest and helpers are installed
python -m pip install --upgrade pip
python -m pip install pytest pytest-cov pytest-asyncio

# Run pytest with coverage and logging options
$params = @(
    'tests/',
    '--cov=src/batcontrol',
    '--log-cli-level=DEBUG',
    '--log-cli-format=%(asctime)s [%(levelname)8s] %(name)s: %(message)s',
    '--log-cli-date-format=%Y-%m-%d %H:%M:%S'
)

python -m pytest @params

exit $LASTEXITCODE
