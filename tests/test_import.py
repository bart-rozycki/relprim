import subprocess
import sys
from pathlib import Path


def test_relprim_can_be_imported() -> None:
    import relprim

    assert relprim is not None


def test_relprim_import_does_not_load_optional_validator_dependencies() -> None:
    src = Path(__file__).resolve().parents[1] / "src"
    code = (
        "import sys; import relprim; "
        "loaded = [name for name in ('jsonschema', 'pydantic') if name in sys.modules]; "
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
