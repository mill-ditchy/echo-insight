"""Migrate, back up, or export local history without discarding the original JSON."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.reliability_db import ReliabilityDB
from src.storage_lock import interprocess_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("migrate", "backup", "export"))
    parser.add_argument("--history", type=Path, default=ROOT / "data" / "update_history.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.operation != "migrate" and not args.output:
        parser.error("backup/export requires --output")
    database = ReliabilityDB(args.history)
    with interprocess_lock(args.history.with_suffix(".worker.lock"), timeout=0), interprocess_lock(args.history):
        database.initialize()
        if args.operation == "backup":
            database.backup(args.output)
        elif args.operation == "export":
            if args.output.resolve() == database.path.resolve():
                raise ValueError("导出目标不能覆盖数据库")
            database.export_history(args.output)
        else:
            print("Migration complete. Original JSON and migration backup are retained.")
        print("Stored contents:", len(database.load_history()["items"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
