import os
import shutil
from datetime import datetime

from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, SQLModel, create_engine

DATABASE_URL = os.getenv("LIFESIM_DB", "sqlite:///game.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# Columns added after the first release. Saves from earlier versions get them filled with
# sensible defaults, so old lives keep working (as humans with no traits).
ADDED_COLUMNS = {
    "species_id": "VARCHAR NOT NULL DEFAULT 'human'",
    "location": "VARCHAR NOT NULL DEFAULT 'village'",
    "talent_state": "VARCHAR NOT NULL DEFAULT 'none'",
    "hidden_talent": "VARCHAR",
    "axes": "JSON DEFAULT '{}'",
    "traits": "JSON DEFAULT '[]'",
    "flags": "JSON DEFAULT '[]'",
    "counters": "JSON DEFAULT '{}'",
    "items": "JSON DEFAULT '{}'",
    "queued_events": "JSON DEFAULT '[]'",
    "actions_taken": "JSON DEFAULT '[]'",
    "health": "INTEGER",
    "stress": "FLOAT NOT NULL DEFAULT 0",
    "happiness": "FLOAT NOT NULL DEFAULT 50",
}


def init_db(eng: Engine | None = None) -> None:
    """Create tables, and upgrade the character table of an older save in place."""
    from . import models  # noqa: F401  (registers the tables)

    eng = eng or engine
    SQLModel.metadata.create_all(eng)
    inspector = inspect(eng)
    table = models.CharacterRecord.__tablename__
    existing = {c["name"] for c in inspector.get_columns(table)}
    missing = [c for c in models.CharacterRecord.__table__.columns.keys() if c not in existing]
    if not missing:
        return

    unknown = [c for c in missing if c not in ADDED_COLUMNS]
    if unknown:
        raise RuntimeError(f"Don't know how to upgrade the save file: missing columns {unknown}")

    path = eng.url.database
    if eng.url.get_backend_name() == "sqlite" and path and path != ":memory:" and os.path.exists(path):
        backup = f"{path}.backup-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy(path, backup)
        print(f"[save upgrade] backed up {path} to {backup}")
    with eng.begin() as conn:
        for column in missing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ADDED_COLUMNS[column]}"))
    print(f"[save upgrade] added columns: {', '.join(missing)}")


def get_session():
    with Session(engine) as session:
        yield session
