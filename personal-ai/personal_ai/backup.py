"""Consistent local SQLite backups; original recordings are never included."""

import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile

from .app import DEFAULT_DB, database


def backup(destination, source=None):
    original_path = Path(source or os.environ.get("PERSONAL_AI_DB", DEFAULT_DB)).expanduser()
    if not original_path.is_file():
        raise FileNotFoundError("バックアップ元DBが見つかりません")
    target = Path(destination).expanduser().resolve()
    if target.exists():
        raise FileExistsError("バックアップ先は既に存在します")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".personal-ai-backup-", suffix=".sqlite3", dir=target.parent)
    os.close(fd)
    try:
        with closing(database(original_path)) as original, closing(sqlite3.connect(temporary)) as copy:
            original.backup(copy)
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise FileExistsError("バックアップ先は既に存在します") from exc
        Path(temporary).unlink()
        return target
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description="個人向けAIのSQLiteバックアップ")
    parser.add_argument("destination")
    args = parser.parse_args()
    try:
        print(backup(args.destination))
    except (OSError, sqlite3.Error) as exc:
        parser.exit(1, f"エラー: {exc}\n")


if __name__ == "__main__":
    main()
