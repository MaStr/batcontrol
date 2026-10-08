This is a Python based repository providing an application for controlling batteries. The main target is to save money with dynamic tariffs. Please follow these guidelines when contributing:

## Code Standards

### Required Before Each Commit

- Only use ASCII characters in source code and log messages. Avoid non-ASCII characters to ensure compatibility and readability across different environments. This rule does not apply to documentation in `docs/`.
- Remove excessive whitespaces.
- Follow PEP8 standards. Use autopep8 for that.
- Check against pylint. Target score is like 9.0-9.5, if you can achieve 10, do it.
- Build against Python 3.14 primarily, ensure compatibility with versions 3.11 to 3.14.
- Run all tests with `run_tests.sh` .
- Do not add new config parameters on your own: ask the maintainer first and wait for approval. A new parameter is permanent surface - `config/batcontrol_config_dummy.yaml` is also the first-run template for new Docker users, and every key has to be mirrored into the Home Assistant add-on and supported long-term. Propose it with its intended default; a hardcoded default or an existing key is often the better answer.
- Once approved, create the parameter in `config/batcontrol_config_dummy.yaml` with a short one-line comment (what it does, unit, default). Keep the long explanation out of that file - it is a parameter list, not a manual.
- Every new or changed config parameter must also be documented in `docs/` (`docs/configuration/*` for config sections, `docs/features/*` for behaviour). The documentation entry is mandatory: a parameter that exists only in the YAML is not finished.

### Development Flow

- Create a venv based of pip & venv for the module.
- Create pytests and run new/changed pytests via command line.
- Avoid large Python commands in CLI for testing, please create test files for verification in a `tmp` folder
- Run `run_tests.sh` .

### Gitflow

- Use feature branches for new features or bug fixes.
- Use descriptive commit messages.
- Create pull requests for code reviews. You can assign copilots for that.
- Branches you create should be named 'copilot/feature-name' or 'copilot/bugfix-name'. Avoid cryptic names.

## Repository Structure
- `config/`: Configuration files and templates
- `scripts/`: Store tests to verfiy logic or stand-alone tests & helpers here.
- `src/`: Batcontrol source code
- `tests/`: pytests tests for automatic testing
- `tmp/`: Folder for test scripts, which will be never committed.
- `docs/`: Documentation for technical documentation.
- `.github/`: GitHub specific files like issue templates and workflows.



## Key Guidelines
1. Follow Python best practices and idiomatic patterns
2. Maintain existing code structure and organization
3. Write pytests for new functionality. If you fix bugs, add tests to cover the bug.
4. Document public APIs and complex logic. Suggest changes to the `docs/` folder when appropriate
5. Lay test scripts for verification and simple testing into the folder `scripts`.
6. Never commit content of `tmp`.
7. User documentation lives in the `docs/` folder and is published via MkDocs to https://mastr.github.io/batcontrol/ — add or update pages there and register new pages in `mkdocs.yml`.
8. Ensure compatibility with supported Python versions (3.11 to 3.14)
9. A change that affects which inverter mode is chosen, or why (`core.py`, `logic/default.py`,
   `logic/next.py`), must stay traceable: add or extend a `DecisionRecord` with a plain-language
   explanation (`logic/decision_trace.py`, `logic/decision_records.py`). This feeds the MQTT
   "Decision" sensor and the in-memory decision journal — see `docs/development/decision-trace.md`.
