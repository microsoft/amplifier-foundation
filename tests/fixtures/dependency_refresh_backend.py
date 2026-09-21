"""Tiny in-tree PEP 517 fixture: build real wheels without network/build dependencies."""

import tomllib
from pathlib import Path
from zipfile import ZipFile


def _build(output, editable=False):
    root = Path.cwd()
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    name = project["name"].replace("-", "_")
    version = project["version"]
    info = f"{name}-{version}.dist-info"
    filename = f"{name}-{version}-py3-none-any.whl"
    metadata = f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n"
    for dep in project.get("dependencies", []):
        metadata += f"Requires-Dist: {dep}\n"
    files = {
        f"{info}/METADATA": metadata,
        f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    if editable:
        files[f"{name}.pth"] = str(root) + "\n"
    else:
        files[f"{name}/__init__.py"] = (root / name / "__init__.py").read_text()
    files[f"{info}/RECORD"] = "".join(
        f"{path},,\n" for path in [*files, f"{info}/RECORD"]
    )
    Path(output).mkdir(parents=True, exist_ok=True)
    with ZipFile(Path(output) / filename, "w") as wheel:
        for path, content in files.items():
            wheel.writestr(path, content)
    return filename


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    return _build(wheel_directory)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return _build(wheel_directory, editable=True)
