"""Download and validate the local character models (about 590 MiB)."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.processors.character_models import download_models

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--without-wd", action="store_true")
    parser.add_argument("--mirror", action="store_true", help="Use hf-mirror.com with the same pinned SHA256 checks")
    args = parser.parse_args()
    print(download_models(not args.without_wd, progress=lambda message: print(message, flush=True), use_mirror=args.mirror))
