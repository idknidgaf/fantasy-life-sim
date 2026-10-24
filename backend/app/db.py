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


CAMPAIGN_ADDED_COLUMNS = {
    "world_year": "INTEGER NOT NULL DEFAULT 0",
}


def init_db(eng: Engine | None = None) -> None:
    """Create tables, and upgrade the character and campaign tables of an older save in place."""
    from . import models  # noqa: F401  (registers the tables)

    eng = eng or engine
    SQLModel.metadata.create_all(eng)
    _drop_orphaned_npcs(eng)
    _add_missing_columns(eng, models.CharacterRecord, ADDED_COLUMNS)
    _add_missing_columns(eng, models.Campaign, CAMPAIGN_ADDED_COLUMNS)


def _add_missing_columns(eng: Engine, model, known: dict[str, str]) -> None:
    table = model.__tablename__
    existing = {c["name"] for c in inspect(eng).get_columns(table)}
    missing = [c for c in model.__table__.columns.keys() if c not in existing]
    if not missing:
        return

    unknown = [c for c in missing if c not in known]
    if unknown:
        raise RuntimeError(f"Don't know how to upgrade the save file: missing columns {unknown} in {table}")

    path = eng.url.database
    if eng.url.get_backend_name() == "sqlite" and path and path != ":memory:" and os.path.exists(path):
        backup = f"{path}.backup-{datetime.now():%Y%m%d-%H%M%S}"
        if not os.path.exists(backup):
            shutil.copy(path, backup)
            print(f"[save upgrade] backed up {path} to {backup}")
    with eng.begin() as conn:
        for column in missing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {known[column]}"))
    print(f"[save upgrade] added columns to {table}: {', '.join(missing)}")


def _drop_orphaned_npcs(eng: Engine) -> None:
    """NPCs and relationships whose campaign no longer exists. Deleting a campaign used to leave them behind, and
    SQLite reuses campaign ids, so a new game could have inherited a deleted game's characters. Safe to repeat."""
    from . import models
    campaign = models.Campaign.__tablename__
    with eng.begin() as conn:
        for table in (models.CharacterRelationship.__tablename__, models.KnownNpc.__tablename__, models.NpcRecord.__tablename__):
            conn.execute(text(f"DELETE FROM {table} WHERE campaign_id NOT IN (SELECT id FROM {campaign})"))


def get_session():
    with Session(engine) as session:
        yield session
