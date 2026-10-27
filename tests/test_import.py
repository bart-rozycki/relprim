import subprocess
import sys
from pathlib import Path

import pytest


def test_relprim_can_be_imported() -> None:
    import relprim

    assert relprim is not None


def test_relprim_import_does_not_load_optional_dependencies() -> None:
    src = Path(__file__).resolve().parents[1] / "src"
    code = (
        "import sys; import relprim; "
        "optional = ('jsonschema', 'pydantic', 'opentelemetry'); "
        "loaded = [name for name in optional if name in sys.modules]; "
        "assert not loaded, loaded"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=src,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_package_ships_pep_561_marker() -> None:
    from importlib.resources import files

    assert files("relprim").joinpath("py.typed").is_file()


def test_public_api_is_exactly_the_documented_surface() -> None:
    import relprim

    exported = set(relprim.__all__)

    assert all(hasattr(relprim, name) for name in exported)
    assert not any(name.startswith("_") for name in exported)
    assert "RetryAttempt" not in exported


@pytest.mark.parametrize(
    ("module", "dependency", "extra"),
    [
        ("relprim.opentelemetry", "opentelemetry", "otel"),
        ("relprim.jsonschema", "jsonschema", "jsonschema"),
        ("relprim.pydantic", "pydantic", "pydantic"),
    ],
)
def test_optional_module_without_dependency_names_the_extra(
    module: str,
    dependency: str,
    extra: str,
) -> None:
    src = Path(__file__).resolve().parents[1] / "src"
    # Mapping a module to None in sys.modules makes importing it fail.
    code = (
        "import sys, importlib; "
        f"sys.modules[{dependency!r}] = None; "
        "import relprim; "
        f"importlib.import_module({module!r})"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=src,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "ModuleNotFoundError" in completed.stderr
    assert f"pip install 'relprim[{extra}]'" in completed.stderr
