"""Type-check downstream code under strict mypy.

tests/type_checking holds typing fixtures; the maintained examples are checked
as well, because they are what users copy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

mypy_api = pytest.importorskip("mypy.api")

ROOT = Path(__file__).resolve().parents[1]
CHECKED = sorted(
    [
        *(ROOT / "tests" / "type_checking").glob("*.py"),
        *(ROOT / "examples").glob("*.py"),
    ]
)


def test_downstream_code_type_checks_strictly(tmp_path: Path) -> None:
    stdout, stderr, exit_status = mypy_api.run(
        [
            "--strict",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            "--config-file",
            str(ROOT / "pyproject.toml"),
            *map(str, CHECKED),
        ]
    )

    assert exit_status == 0, stdout + stderr
