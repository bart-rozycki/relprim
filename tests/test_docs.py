"""Keep documentation and examples from drifting away from the code.

Documentation snippets use placeholders such as `provider`, so they are not
executed. They must still be valid Python and may only import public names
that exist. Examples are executed end to end.
"""

from __future__ import annotations

import ast
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = sorted([ROOT / "README.md", *(ROOT / "docs").glob("*.md")])
EXAMPLES = sorted((ROOT / "examples").glob("*.py"))

PYTHON_BLOCK = re.compile(r"^```python\n(.*?)^```", re.MULTILINE | re.DOTALL)
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
REPOSITORY_PREFIXES = (
    "https://github.com/bart-rozycki/relprim/blob/main/",
    "https://github.com/bart-rozycki/relprim/tree/main/",
)


def python_blocks(document: Path) -> list[str]:
    return PYTHON_BLOCK.findall(document.read_text(encoding="utf-8"))


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_documented_python_snippets_compile(document: Path) -> None:
    for index, block in enumerate(python_blocks(document)):
        try:
            compile(block, f"{document.name}[{index}]", "exec", ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        except SyntaxError as exc:
            pytest.fail(f"{document.name} block {index} is not valid Python: {exc}\n{block}")


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_documented_relprim_imports_exist(document: Path) -> None:
    for block in python_blocks(document):
        for node in ast.walk(ast.parse(block)):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue

            if node.module.split(".")[0] != "relprim":
                continue

            module = importlib.import_module(node.module)

            for alias in node.names:
                assert not alias.name.startswith("_"), f"{document.name} imports {alias.name}"
                assert hasattr(module, alias.name), f"{document.name}: {node.module}.{alias.name}"


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_repository_links_resolve(document: Path) -> None:
    for target in LINK.findall(document.read_text(encoding="utf-8")):
        # README links are absolute so they also work on PyPI.
        for prefix in REPOSITORY_PREFIXES:
            if target.startswith(prefix):
                path = ROOT / target.removeprefix(prefix)
                break
        else:
            if "://" in target or target.startswith("mailto:"):
                continue

            path = document.parent / target

        assert path.exists(), f"{document.name} links to missing {target}"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.stem)
def test_example_runs(example: Path, tmp_path: Path) -> None:
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

    completed = subprocess.run(
        [sys.executable, str(example)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
