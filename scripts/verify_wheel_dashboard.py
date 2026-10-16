#!/usr/bin/env python
"""Verify that a built wheel actually contains the dashboard.

This guards the one failure mode of the whole dashboard feature that is
otherwise silent: the Home Assistant stable add-on installs nothing but the
released wheel, so a wheel built without the npm step produces an add-on with
no dashboard - no error, no warning, just a hint page where the UI should be.

    python scripts/verify_wheel_dashboard.py dist/batcontrol-*.whl
"""
import sys
import zipfile
from pathlib import Path

# Everything the dashboard needs to actually render.
REQUIRED = (
    'batcontrol/web/static/index.html',
)
# Patterns at least one file has to match.
REQUIRED_GLOBS = (
    'batcontrol/web/static/assets/*.js',
    'batcontrol/web/static/assets/*.css',
    'batcontrol/web/static/assets/*.woff2',
    'batcontrol/web/static/licenses/LICENSE-OFL-*.txt',
    # Swagger UI is served from the package, not from a CDN, so /docs works
    # without internet egress. Apache-2.0 with a NOTICE: both ship with it.
    'batcontrol/web/static/swagger/swagger-ui-bundle.js',
    'batcontrol/web/static/swagger/swagger-ui.css',
    'batcontrol/web/static/licenses/*SwaggerUI*',
)


def verify(wheel: Path) -> list:
    """Return a list of problems; empty means the wheel is fine."""
    problems = []
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        for required in REQUIRED:
            if required not in names:
                problems.append(f'missing: {required}')
        for pattern in REQUIRED_GLOBS:
            if not any(zipfile.Path(archive, name).match(pattern)
                       for name in names):
                problems.append(f'nothing matches: {pattern}')
    return problems


def main(argv) -> int:
    """Check every wheel given on the command line."""
    if len(argv) < 2:
        print(__doc__)
        return 2

    failed = False
    for argument in argv[1:]:
        wheel = Path(argument)
        if not wheel.is_file():
            print(f'{wheel}: not a file')
            failed = True
            continue
        problems = verify(wheel)
        if problems:
            failed = True
            print(f'{wheel.name}: the dashboard is NOT in this wheel')
            for problem in problems:
                print(f'  - {problem}')
            print('  Build it first: cd frontend && npm ci && npm run build')
        else:
            print(f'{wheel.name}: dashboard present')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
