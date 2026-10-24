"""Saves made before species/traits existed must keep working."""
import json
import os

from sqlalchemy import create_engine, text
from sqlmodel import Session, select

from app import engine as sim
from app.db import init_db
from app.models import CharacterRecord

OLD_SCHEMA = """
CREATE TABLE campaign (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, seed INTEGER NOT NULL,
                       active BOOLEAN NOT NULL, created_at DATETIME NOT NULL);
CREATE TABLE characterrecord (id INTEGER PRIMARY KEY, campaign_id INTEGER NOT NULL, name VARCHAR NOT NULL,
                              age INTEGER NOT NULL, alive BOOLEAN NOT NULL, stats JSON, skills JSON, history JSON,
                              cause_of_death VARCHAR, pending_event VARCHAR);
"""


def make_old_save(path):
    eng = create_engine(f"sqlite:///{path}")
    with eng.begin() as conn:
        for statement in OLD_SCHEMA.strip().split(";"):
            if statement.strip():
                conn.execute(text(statement))
        conn.execute(text("INSERT INTO campaign VALUES (1, 'Old Aren', 123, 1, '2026-01-01 00:00:00')"))
        conn.execute(text(
            "INSERT INTO characterrecord VALUES (1, 1, 'Old Aren', 20, 1, :stats, :skills, :history, NULL, 'wandering_merchant')"),
            {"stats": json.dumps({"constitution": 40, "dexterity": 40, "wisdom": 40, "strength": 40, "charisma": 40}),
             "skills": json.dumps({"archery": 3.5}),
             "history": json.dumps([{"age": 1, "kind": "birth", "text": "Born.", "event_id": None}])},
        )
    return eng


def test_old_save_is_upgraded_in_place_and_backed_up(tmp_path):
    path = tmp_path / "old.db"
    eng = make_old_save(path)
    init_db(eng)

    assert any(name.startswith("old.db.backup-") for name in os.listdir(tmp_path))
    with Session(eng) as s:
        rec = s.exec(select(CharacterRecord)).first()
        c = sim.repair(rec.to_character())
    assert c.name == "Old Aren" and c.age == 20 and c.skills == {"archery": 3.5}      # data untouched
    assert c.species_id == "human" and c.traits == [] and c.talent_state == "none"
    assert c.hidden_talent is None and c.location == "village"
    assert set(c.axes) == {"bravery", "curiosity", "sociability", "discipline", "law_chaos", "good_evil"}
    # The migration fills in vitals and the new actions list with safe defaults.
    assert c.actions_taken == [] and c.health is not None and c.health > 0
    assert c.stress == 0 and c.happiness == 50


def test_upgraded_character_can_keep_playing(tmp_path):
    eng = make_old_save(tmp_path / "old.db")
    init_db(eng)
    with Session(eng) as s:
        c = sim.repair(s.exec(select(CharacterRecord)).first().to_character())
    assert c.pending_event == "wandering_merchant"                  # an event still in the content
    sim.resolve_event(c, 123, "wandering_merchant", "ignore")
    sim.advance_turn(c, 123)
    assert c.age == 21


def test_removed_event_in_old_save_is_cleared_not_crashed(tmp_path):
    eng = make_old_save(tmp_path / "old.db")
    with eng.begin() as conn:
        conn.execute(text("UPDATE characterrecord SET pending_event = 'event_that_no_longer_exists'"))
    init_db(eng)
    with Session(eng) as s:
        c = sim.repair(s.exec(select(CharacterRecord)).first().to_character())
    assert c.pending_event is None


def test_fresh_database_needs_no_upgrade(tmp_path):
    path = tmp_path / "fresh.db"
    eng = create_engine(f"sqlite:///{path}")
    init_db(eng)
    init_db(eng)                                                    # idempotent
    assert not any("backup" in name for name in os.listdir(tmp_path))


def test_old_campaign_table_gains_the_world_clock_and_old_npcs_load(tmp_path):
    from app.models import Campaign, NpcRecord
    from app.npc import Npc
    from tests.progress_helpers import person
    path = tmp_path / "old.db"
    eng = make_old_save(path)
    init_db(eng)
    init_db(eng)                                                # twice is harmless
    with Session(eng) as s:
        assert s.get(Campaign, 1).world_year == 0
        n = person(age=30, seed=5)
        old = n.model_dump(mode="json")
        old.pop("progress")
        old.pop("surname")                                      # exactly what an NPC saved before this change looks like
        s.add(NpcRecord(campaign_id=1, npc_id=n.id, category="persistent", status="active", species_id="human",
                        name=n.name, age=30, data=old))
        s.commit()
        loaded = Npc.model_validate(s.exec(select(NpcRecord)).one().data)
        assert loaded.progress is None and loaded.stats == n.stats
