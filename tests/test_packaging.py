import fnmatch
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# A real (non-editable) install, like the Docker image's, copies only the non-Python files
# that pyproject.toml names. One that is missing imports fine and fails later, on the page
# that needs it -- so every such file in the package has to match a pattern.
def test_every_non_python_file_in_the_package_is_installed():
    patterns = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"]["setuptools"]["package-data"]["attendance"]
    package = ROOT / "attendance"

    missing = [
        path.relative_to(package).as_posix()
        for path in package.rglob("*")
        if path.is_file() and path.suffix != ".py" and "__pycache__" not in path.parts
        and not any(fnmatch.fnmatch(path.relative_to(package).as_posix(), p) for p in patterns)
    ]

    assert missing == []
