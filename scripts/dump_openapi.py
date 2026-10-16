#!/usr/bin/env python
"""Write the OpenAPI schema of the read-only API to a file.

Used by the documentation build so the published API reference is generated
from the code instead of being re-typed next to it - a hand-maintained copy
drifts the moment a field description changes.

No batcontrol instance is needed: the schema comes from the route and model
definitions, and nothing is called while it is generated. The service layer
is handed a stand-in that would fail loudly if that ever stopped being true.

    python scripts/dump_openapi.py [output.json]
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))

from batcontrol.web.app import create_app  # noqa: E402  pylint: disable=wrong-import-position

DEFAULT_OUTPUT = pathlib.Path('docs/assets/openapi.json')


class _NotARunningBatcontrol:
    """Stand-in for the core. Schema generation must not touch it."""

    def __getattr__(self, name):
        raise AssertionError(
            f'Generating the OpenAPI schema read {name!r} from the batcontrol'
            ' instance. The schema must be derivable without a running'
            ' instance - keep route handlers lazy.'
        )


def main() -> int:
    """Write the schema and report where it went."""
    output = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    app = create_app(_NotARunningBatcontrol())
    schema = app.openapi()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + '\n',
                      encoding='utf-8')

    print(f'{output}: {len(schema["paths"])} paths,'
          f' {len(schema["components"]["schemas"])} schemas')
    return 0


if __name__ == '__main__':
    sys.exit(main())
