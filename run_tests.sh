#!/bin/bash

uv venv --python 3.13 --allow-existing

# Install the package together with test dependencies from pyproject.toml
uv pip install -e '.[test]'

# Run tests with coverage
uv run pytest tests/ --cov=src/batcontrol --log-cli-level=DEBUG --log-cli-format="%(asctime)s [%(levelname)8s] %(name)s: %(message)s" --log-cli-date-format="%Y-%m-%d %H:%M:%S"

# Exit with the same status as pytest
exit $?
