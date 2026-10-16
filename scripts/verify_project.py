"""Run the full repeatable release checks; stop on the first failure."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.release import check


def run_checks(root: Path, clean: bool = False, *, include_companion: bool = False) -> None:
    commands = [
        [sys.executable, "-m", "pip", "check"],
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        ["node", "tests/test_frontend_security.js"],
        ["node", "tests/test_ai_provider_frontend.js"],
        ["node", "tests/test_ai_errors_frontend.js"],
        ["node", "tests/test_video_understanding_frontend.js"],
        ["node", "tests/test_xiaohongshu_frontend.js"],
        ["node", "tests/test_douyin_frontend.js"],
        ["node", "tests/test_source_navigation_frontend.js"],
        ["node", "tests/test_collection_host_frontend.js"],
        ["node", "tests/test_source_avatars_frontend.js"],
        ["node", "tests/test_video_lab_frontend.js"],
    ]
    if include_companion:
        commands.extend([
            [sys.executable, "-m", "unittest", "discover", "-s", "android/tests", "-v"],
            ["node", "android/tests/test_native_frontend.js"],
        ])
    if clean:
        commands.append([sys.executable, "scripts/smoke_test.py"])
    for command in commands:
        print("Running:", " ".join(command[1:]), flush=True)
        subprocess.run(command, cwd=root, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-source", action="store_true", help="Verify only staged Git contents, excluding all local data")
    parser.add_argument("--include-companion", action="store_true", help="Also run deferred companion checks")
    args = parser.parse_args()
    if args.clean_source:
        blobs = check(ROOT, include_companion=args.include_companion)
        temp_root = ROOT / ".test-tmp"
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="verify-source-", dir=temp_root) as directory:
            snapshot = Path(directory)
            for name, content in blobs.items():
                target = snapshot / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            run_checks(snapshot, clean=True, include_companion=args.include_companion)
    else:
        run_checks(ROOT, include_companion=args.include_companion)
    print("All project checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, FileNotFoundError, subprocess.CalledProcessError) as exc:
        print("Verification failed:", exc, file=sys.stderr)
        raise SystemExit(1)
