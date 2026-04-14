This is a Python based repository providing an application for controlling batteries. The main target is to save money with dynamic tariffs. Please follow these guidelines when contributing:

## Code Standards

### Required Before Each Commit

- Only use ASCII characters in code, log messages, and documentation. Avoid non-ASCII characters to ensure compatibility and readability across different environments.
- Remove excessive whitespaces.
- Follow PEP8 standards. Use autopep8 for that.
- Check against pylint. Target score is like 9.0-9.5, if you can achieve 10, do it.
- Build against Python 3.11 primarily, ensure compatibility with versions 3.9 to 3.13.
- Run all tests with `run_tests.sh` .
- Create new parameters in `config/batcontrol_config_dummy.yaml` .

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
7. If you have new documentation for the wiki, add files to the `docs/` folder. Prefix is `WIKI_`.
8. Ensure compatibility with supported Python versions (3.9 to 3.13)
