#!/usr/bin/env python3
"""Utility to back up and restore Shiye storage files."""

import argparse
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import List


DB_FILES: List[str] = ["shiye.db", "shiye.faiss"]
DEFAULT_DATA_DIR = Path(os.getenv("SHIYE_DATA_DIR", Path.home() / ".shiye"))
DEFAULT_BACKUP_ROOT = DEFAULT_DATA_DIR / "backups"


def backup(data_dir: Path, dest: Path | None) -> Path:
    """Copy the storage files into a timestamped backup folder."""
    if dest is None:
        dest = DEFAULT_BACKUP_ROOT / datetime.now().strftime("backup-%Y%m%d-%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)

    for name in DB_FILES:
        src = data_dir / name
        if not src.exists():
            print(f"[warn] missing source file: {src}")
            continue
        shutil.copy2(src, dest / name)
        print(f"[ok] copied {src} -> {dest/name}")

    return dest


def restore(data_dir: Path, src: Path) -> None:
    """Restore storage files from a backup folder."""
    if not src.exists():
        raise FileNotFoundError(f"backup path not found: {src}")

    data_dir.mkdir(parents=True, exist_ok=True)
    for name in DB_FILES:
        src_file = src / name
        if not src_file.exists():
            print(f"[warn] missing {name} in backup: {src_file}")
            continue
        shutil.copy2(src_file, data_dir / name)
        print(f"[ok] restored {src_file} -> {data_dir/name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Back up or restore Shiye storage files.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Path to Shiye data dir (default: %(default)s)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="Create a backup of shiye.db and shiye.faiss")
    backup_parser.add_argument("--dest", type=Path, help="Destination directory for the backup (default: data_dir/backups/<timestamp>)")

    restore_parser = subparsers.add_parser("restore", help="Restore shiye.db and shiye.faiss from a backup folder")
    restore_parser.add_argument("source", type=Path, help="Path to the backup folder containing the files")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = args.data_dir.expanduser()

    if args.command == "backup":
        dest = args.dest.expanduser() if args.dest else None
        backup_path = backup(data_dir, dest)
        print(f"[done] backup stored in: {backup_path}")
    elif args.command == "restore":
        restore(data_dir, args.source.expanduser())
        print(f"[done] restored to: {data_dir}")


if __name__ == "__main__":
    main()
