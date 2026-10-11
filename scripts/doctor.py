"""Local environment diagnostics; never read or print account credentials."""

import argparse
import importlib.metadata
import json
import os
import platform
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = (
    "feedparser", "requests", "urllib3", "faster-whisper", "av", "fastapi",
    "uvicorn", "pywebview", "qrcode", "rapidocr-onnxruntime", "numpy", "Pillow", "onnxruntime",
)


def webview_available() -> bool:
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        folder = os.environ.get(variable)
        if folder:
            directory = Path(folder) / "Microsoft" / "EdgeWebView" / "Application"
            if any(directory.glob("*/msedgewebview2.exe")):
                return True
    return False


def diagnose() -> dict:
    errors, warnings, versions = [], [], {}
    if os.name != "nt":
        errors.append("This release supports Windows 10/11 only.")
    if sys.version_info[:2] != (3, 11) or struct.calcsize("P") != 8:
        errors.append("Use 64-bit Python 3.11 for the tested dependency lock.")
    for name in DEPENDENCIES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            errors.append(f"Missing dependency: {name}. Run scripts/setup.ps1.")
    for name in ("VERSION", "web/static/index.html", "web/static/app.js", "web/static/styles.css"):
        if not (ROOT / name).is_file():
            errors.append(f"Missing application resource: {name}. Extract the complete source archive.")
    webview = webview_available() if os.name == "nt" else False
    if not webview:
        warnings.append("WebView2 Runtime was not detected. Install it for the desktop UI: https://developer.microsoft.com/microsoft-edge/webview2/")
    return {"python": platform.python_version(), "platform": platform.system(),
            "bits": struct.calcsize("P") * 8, "dependencies": versions,
            "webview2_detected": webview, "errors": errors, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print a shareable report without account data")
    parser.add_argument("--strict", action="store_true", help="Exit with failure when required checks fail")
    args = parser.parse_args()
    report = diagnose()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Python {report['python']} / {report['platform']} / {report['bits']}-bit")
        print(f"Runtime dependencies installed: {len(report['dependencies'])}/{len(DEPENDENCIES)}")
        for message in report["warnings"]:
            print("WARNING:", message)
        for message in report["errors"]:
            print("ERROR:", message)
        if not report["errors"]:
            print("Required environment checks passed.")
    return 1 if args.strict and report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
