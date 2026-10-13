#!/usr/bin/env python3
"""
Automated Database Backup Utility for CondoManager.
Supports both SQLite database snapshots and PostgreSQL SQL dumps.
"""

import os
import shutil
import subprocess
from datetime import datetime


def backup_database(db_path: str = None):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    backup_dir = os.path.join(base_dir, "backups")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_type = os.environ.get("DB_TYPE", "sqlite").lower()

    if db_type == "sqlite":
        if not db_path or not os.path.exists(db_path):
            db_path = os.path.join(base_dir, "condominium.db")
            if not os.path.exists(db_path):
                db_path = os.path.join(base_dir, "condomanager.db")

        if os.path.exists(db_path):
            dest_backup = os.path.join(backup_dir, f"backup_sqlite_{timestamp}.db")
            shutil.copy2(db_path, dest_backup)
            print(f"[Backup] SQLite database successfully backed up to: {dest_backup}")
            return dest_backup
        else:
            print("[Backup] No local SQLite database file found to backup.")
            return None

    elif db_type == "postgres":
        host = os.environ.get("DB_HOST", "localhost")
        port = os.environ.get("DB_PORT", "5432")
        dbname = os.environ.get("DB_NAME", "condomanager")
        user = os.environ.get("DB_USER", "postgres")
        pwd = os.environ.get("DB_PASSWORD", "")

        dest_backup = os.path.join(backup_dir, f"backup_pg_{dbname}_{timestamp}.sql")
        env = os.environ.copy()
        if pwd:
            env["PGPASSWORD"] = pwd

        cmd = [
            "pg_dump",
            "-h", host,
            "-p", str(port),
            "-U", user,
            "-d", dbname,
            "-f", dest_backup
        ]
        try:
            subprocess.run(cmd, env=env, check=True)
            print(f"[Backup] PostgreSQL database successfully dumped to: {dest_backup}")
            return dest_backup
        except Exception as e:
            print(f"[Backup Error] Failed to dump PostgreSQL database: {e}")
            return None


if __name__ == "__main__":
    backup_database()
