from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def connect(url: str):
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=4)


def migrate(url: str):
    with connect(url) as db:
        db.execute("SELECT pg_advisory_xact_lock(982374011)")
        db.execute("CREATE TABLE IF NOT EXISTS mad_migrations (name text PRIMARY KEY)")
        for migration in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
            if not db.execute("SELECT 1 FROM mad_migrations WHERE name = %s", (migration.name,)).fetchone():
                db.execute(migration.read_text(encoding="utf-8"))
                db.execute("INSERT INTO mad_migrations VALUES (%s)", (migration.name,))


def ready(url: str) -> bool:
    with connect(url) as db:
        if db.execute("SELECT to_regclass('mad_migrations') AS migrations").fetchone()["migrations"] is None:
            return False
        installed = {row["name"] for row in db.execute("SELECT name FROM mad_migrations")}
        return all(path.name in installed for path in Path(__file__).with_name("migrations").glob("*.sql"))
