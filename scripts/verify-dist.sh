#!/usr/bin/env bash
# Verify built RelPrim distributions in clean virtual environments.
#
# Usage: scripts/verify-dist.sh [dist-dir]
#
# Everything runs from a temporary directory outside the repository so the
# source tree can never shadow or supplement the installed package.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$(cd "${1:-$REPO/dist}" && pwd)"
PYTHON="${PYTHON:-python3}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

shopt -s nullglob
wheels=("$DIST"/relprim-*.whl)
sdists=("$DIST"/relprim-*.tar.gz)
[ "${#wheels[@]}" -eq 1 ] || { echo "expected exactly one wheel in $DIST"; exit 1; }
[ "${#sdists[@]}" -eq 1 ] || { echo "expected exactly one sdist in $DIST"; exit 1; }
WHEEL="${wheels[0]}"
SDIST="${sdists[0]}"

echo "== artifact contents"
"$PYTHON" - "$WHEEL" "$SDIST" <<'PY'
import sys
import tarfile
import zipfile

wheel, sdist = sys.argv[1:]
wheel_names = zipfile.ZipFile(wheel).namelist()
sdist_names = tarfile.open(sdist).getnames()

assert "relprim/py.typed" in wheel_names, "wheel is missing relprim/py.typed"
assert any(name.endswith("/licenses/LICENSE") for name in wheel_names), "wheel is missing LICENSE"
assert all(name.startswith(("relprim/", "relprim-")) for name in wheel_names), wheel_names
assert any(name.endswith("/src/relprim/py.typed") for name in sdist_names), "sdist is missing py.typed"
for required in ("LICENSE", "README.md", "pyproject.toml", "tests/type_checking", "docs", "examples"):
    assert any(f"/{required}" in name for name in sdist_names), f"sdist is missing {required}"
print("ok   wheel and sdist contents")
PY

new_env() {
  "$PYTHON" -m venv "$WORK/$1"
  "$WORK/$1/bin/python" -m pip install -q --upgrade pip
}

run_example() {
  local env="$1" example="$2"
  mkdir -p "$WORK/run-$env"
  if ! (cd "$WORK/run-$env" && "$WORK/$env/bin/python" -I "$REPO/examples/$example" > "$WORK/out.log" 2>&1); then
    cat "$WORK/out.log"
    echo "FAIL $example ($env)"
    exit 1
  fi
  echo "ok   $example ($env)"
}

echo "== base install without extras"
new_env base
"$WORK/base/bin/python" -m pip install -q "$WHEEL"
extra_packages="$("$WORK/base/bin/python" -m pip list --format=freeze | grep -viE '^(relprim|pip|setuptools|wheel)==' || true)"
[ -z "$extra_packages" ] || { echo "FAIL unexpected base dependencies: $extra_packages"; exit 1; }
"$WORK/base/bin/python" -I - <<'PY'
import importlib
import sys
from importlib.resources import files

import relprim
import relprim.sqlite_events

assert "site-packages" in (relprim.__file__ or ""), relprim.__file__
assert files("relprim").joinpath("py.typed").is_file()

optional = ("opentelemetry", "jsonschema", "pydantic")
loaded = [name for name in optional if name in sys.modules]
assert not loaded, loaded

for module, extra in (
    ("relprim.opentelemetry", "otel"),
    ("relprim.jsonschema", "jsonschema"),
    ("relprim.pydantic", "pydantic"),
):
    try:
        importlib.import_module(module)
    except ModuleNotFoundError as exc:
        assert f"relprim[{extra}]" in str(exc), exc
    else:
        raise AssertionError(f"{module} imported without its extra")

print("ok   base import")
PY
for example in "$REPO"/examples/*.py; do
  case "$(basename "$example")" in
    opentelemetry_integration.py | json_schema_validation.py | pydantic_validation.py) ;;
    *) run_example base "$(basename "$example")" ;;
  esac
done

for extra in otel jsonschema pydantic; do
  echo "== extra [$extra]"
  new_env "$extra"
  "$WORK/$extra/bin/python" -m pip install -q "$WHEEL[$extra]"
  module="relprim.${extra/otel/opentelemetry}"
  "$WORK/$extra/bin/python" -I -c "import relprim, $module; print('ok   import $module')"
done

echo "== all extras, every example, downstream typing"
new_env all
"$WORK/all/bin/python" -m pip install -q "$WHEEL[otel,jsonschema,pydantic]" opentelemetry-sdk mypy types-jsonschema
for example in "$REPO"/examples/*.py; do
  run_example all "$(basename "$example")"
done
mkdir "$WORK/downstream"
cp "$REPO"/tests/type_checking/*.py "$WORK/downstream/"
(cd "$WORK/downstream" && "$WORK/all/bin/python" -m mypy --strict --no-incremental .)
echo "ok   downstream mypy against the installed wheel"
