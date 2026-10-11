"""Export the installed, validated Windows/Python 3.11 dependency snapshot."""

import argparse
import importlib.metadata as metadata
import os
import struct
import sys
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def lock_contents() -> dict[str, str]:
    if os.name != "nt" or sys.version_info[:2] != (3, 11) or struct.calcsize("P") != 8:
        raise RuntimeError("Generate these locks on Windows with 64-bit Python 3.11.")
    pending = [Requirement(line.strip()) for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.startswith("#")]
    selected, extras = {}, {}
    environment = default_environment()
    while pending:
        req = pending.pop()
        key = req.name.lower().replace("_", "-")
        distribution = metadata.distribution(req.name)
        if not req.specifier.contains(distribution.version):
            raise RuntimeError(f"Installed {req.name} does not satisfy its declared dependency range.")
        requested = set(req.extras)
        if key in selected and requested <= extras[key]:
            continue
        extras.setdefault(key, set()).update(requested)
        selected[key] = distribution.version
        for item in distribution.requires or []:
            dep = Requirement(item)
            if dep.marker is None or any(dep.marker.evaluate({**environment, "extra": extra}) for extra in ["", *extras[key]]):
                pending.append(dep)
    runtime = ("# Tested runtime dependency snapshot: Windows x64 / Python 3.11\n"
               "# Install with: python -m pip install -r requirements-windows-py311.lock\n"
               "-r requirements.txt\n")
    runtime += "".join(f"{name}=={version}\n" for name, version in sorted(selected.items()))
    development = "# Development dependencies for the same tested runtime\n-r requirements-windows-py311.lock\n-r requirements-dev.txt\n"
    development += "".join(f"{name}=={metadata.version(name)}\n" for name in ("pip", "setuptools", "httpx"))
    return {"requirements-windows-py311.lock": runtime, "requirements-dev-windows-py311.lock": development}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the installed environment matches the saved locks")
    args = parser.parse_args()
    for name, content in lock_contents().items():
        path = ROOT / name
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                raise RuntimeError(f"{name} does not match the installed environment.")
        else:
            path.write_text(content, encoding="utf-8")
    print("Dependency snapshot matches." if args.check else "Dependency snapshot exported. Validate in a new environment before committing.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, metadata.PackageNotFoundError) as exc:
        print("Dependency snapshot failed:", exc, file=sys.stderr)
        raise SystemExit(1)
