#!/bin/bash

# Activate virtual environment if it exists (if running outside of container)
if [ -f "./venv/activate" ]; then
    source ./venv/activate
fi

# Install the package together with test dependencies from pyproject.toml
pip install -e '.[test]'

# Run tests with coverage
python -m pytest tests/ --cov=src/batcontrol --log-cli-level=DEBUG --log-cli-format="%(asctime)s [%(levelname)8s] %(name)s: %(message)s" --log-cli-date-format="%Y-%m-%d %H:%M:%S"

# Exit with the same status as pytest
exit $?
