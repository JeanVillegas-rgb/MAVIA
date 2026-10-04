"""Back up local SQLite, migrate, and verify pre-existing lesson/learner rows.

Run with the backend virtualenv from any directory. Never restores or deletes
data automatically; if verification fails, the printed backup remains available.
"""
import os
from pathlib import Path
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()
from django.conf import settings
from django.core.management import call_command


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def main():
    config = settings.DATABASES["default"]
    if config["ENGINE"] != "django.db.backends.sqlite3":
        raise RuntimeError("This backup helper is only for local SQLite.")
    database = Path(config["NAME"]).resolve(strict=True)
    backup_dir = database.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"before-classmate-merge-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
    before = {}
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
        tables = source.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for (table,) in tables:
            if table.startswith(("lessons_", "course_", "adaptive_", "question_generation_", "user_")):
                columns = [row[1] for row in source.execute(f"PRAGMA table_info({quoted(table)})")]
                query = f"SELECT {', '.join(map(quoted, columns))} FROM {quoted(table)} ORDER BY 1"
                before[table] = (query, source.execute(query).fetchall())
    print(f"Database backup: {backup}", flush=True)
    call_command("migrate", interactive=False)
    with sqlite3.connect(database) as source:
        for table, (query, rows) in before.items():
            if source.execute(query).fetchall() != rows:
                raise RuntimeError(f"Existing data changed in {table}; inspect backup {backup} before continuing.")
    print(f"Verified unchanged existing data in {len(before)} tables.")


if __name__ == "__main__":
    main()
