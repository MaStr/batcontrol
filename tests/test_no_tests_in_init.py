"""Guard: pytest only collects test_*.py, so tests in __init__.py never run."""

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent


def test_no_test_functions_in_package_init_files():
    """No tests/**/__init__.py may define test_* functions or Test* classes."""
    offenders = []
    for init_file in TESTS_ROOT.rglob('__init__.py'):
        tree = ast.parse(init_file.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name.startswith('test_'):
                offenders.append(f'{init_file}:{node.lineno} {node.name}')
            elif isinstance(node, ast.ClassDef) and node.name.startswith('Test'):
                offenders.append(f'{init_file}:{node.lineno} {node.name}')
    assert not offenders, (
        'Tests defined in __init__.py are never collected by pytest; '
        'move them into a test_*.py module:\n' + '\n'.join(offenders)
    )
