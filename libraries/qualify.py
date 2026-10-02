"""Build one optional package and test its installed wheel in an owned environment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library", choices=("scheduling", "operations", "worktrees", "recall"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    package = Path(__file__).resolve().parent / args.library
    output = (args.output or package / "dist").resolve()
    output.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    # Always select a new consumer explicitly, never an inherited worker environment.
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("PYTHONPATH", None)

    def run(*command: str, **kwargs):
        return subprocess.run(command, check=True, env=environment, **kwargs)

    with tempfile.TemporaryDirectory(prefix=f"foundation-{args.library}-consumer-") as scratch:
        scratch = Path(scratch)
        artifacts = scratch / "artifacts"
        run("uv", "build", str(package), "--out-dir", str(artifacts))
        wheel, = artifacts.glob("*.whl")
        consumer = scratch / "consumer"
        run("uv", "venv", "--python", sys.executable, str(consumer))
        python = consumer / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run("uv", "pip", "install", "--python", str(python), str(wheel), "pytest>=8")
        module = "amplifier_" + args.library
        probe = '''import importlib, importlib.util, json, pathlib, sys
for forbidden in ("amplifier_core", "amplifier_foundation", "amplifier_web"):
    assert importlib.util.find_spec(forbidden) is None, forbidden
before = set(pathlib.Path.cwd().iterdir())
loaded = importlib.import_module(sys.argv[1])
assert pathlib.Path(loaded.__file__).is_relative_to(pathlib.Path(sys.prefix))
assert set(pathlib.Path.cwd().iterdir()) == before, "Import created state"
print(json.dumps({"module": loaded.__name__, "python": sys.version.split()[0], "nativeRuntimeInstalled": False, "importCreatedState": False}))
'''
        checked = run(str(python), "-I", "-c", probe, module, cwd=scratch, capture_output=True, text=True)
        config = scratch / "pytest.ini"
        config.write_text("[pytest]\n", encoding="utf-8")
        tests = subprocess.run(
            [str(python), "-I", "-m", "pytest", "-c", str(config), "--import-mode=importlib", str(package / "tests"), "-q", "-rA"],
            cwd=scratch, env=environment, capture_output=True, text=True,
        )
        log = tests.stdout + tests.stderr
        (output / "tests.log").write_text(log, encoding="utf-8")
        print(log, end="")
        tests.check_returncode()
        receipt = {"library": args.library, "consumer": json.loads(checked.stdout), "tests": {"exitCode": tests.returncode, "log": "tests.log"}, "artifacts": []}
        for artifact in sorted(artifacts.iterdir()):
            content = artifact.read_bytes()
            (output / artifact.name).write_bytes(content)
            receipt["artifacts"].append({"name": artifact.name, "sha256": hashlib.sha256(content).hexdigest()})
        (output / "qualification.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
