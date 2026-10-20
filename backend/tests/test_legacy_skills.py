"""The four schools of magic (elemental_magic, spirit_magic, necromancy, enchantment) are gone. Only Magic,
Swordsmanship, Marksmanship and Martial Arts are specialised stats, and old saves must still load."""
import json
import re

import pytest
from sqlmodel import Session, select

from app import engine as sim
from app.content import DATA_DIR, STATS, get_content
from app.models import CharacterRecord
from app.power import calculate_power, get_power_config
from app.rules import LEGACY_SKILLS, migrate_skills
from tests.helpers import make
from tests.test_api import client, new_game, play_one_year  # noqa: F401  (client is a fixture)

CT = get_content()
CFG = get_power_config()
REMOVED = set(LEGACY_SKILLS)


def test_exactly_the_four_specialised_stats_remain():
    assert {i: d.name for i, d in CFG.specialised_stats.items()} == {
        "swordsmanship": "Swordsmanship", "marksmanship": "Marksmanship", "magic": "Magic", "martial_arts": "Martial Arts"}
    assert all(len(d.skills) == 1 for d in CFG.specialised_stats.values())     # one skill each, Magic included
    assert not set(CFG.specialised_stats) & set(STATS)                         # base stats are untouched


def test_removed_schools_exist_nowhere_in_the_game_data():
    assert not REMOVED & set(CT.skills)
    pattern = re.compile(r"\b(" + "|".join(REMOVED) + r")\b")
    for path in DATA_DIR.rglob("*"):
        if path.is_file() and path.suffix in {".yaml", ".yml", ".json"}:
            assert not pattern.search(path.read_text(encoding="utf-8")), f"{path.name} still mentions a removed skill"


def test_base_stats_are_unchanged():
    assert set(STATS) == {"strength", "dexterity", "constitution", "wisdom", "charisma"}


# ---------- migrating saved characters ----------

def test_schools_are_folded_into_magic_so_power_is_unchanged():
    old = {"elemental_magic": 30, "spirit_magic": 20, "necromancy": 5, "enchantment": 1, "swordsmanship": 12}
    new = migrate_skills(old, CT)
    assert new == {"magic": 56, "swordsmanship": 12}
    assert sum(new.values()) == sum(old.values())
    power = calculate_power(make(skills=new), CT, CFG)
    magic = next(l for l in power.stats.lines if l.source_id == "magic")
    assert magic.value == 56 and power.total == 68          # what the four schools used to add up to


def test_migration_adds_to_existing_magic_drops_unknown_skills_and_is_idempotent():
    once = migrate_skills({"magic": 4, "necromancy": 6, "long_gone_skill": 9}, CT)
    assert once == {"magic": 10}
    assert migrate_skills(once, CT) == once


def test_repair_cleans_a_loaded_legacy_character():
    c = sim.repair(make(skills={"elemental_magic": 7.5, "spirit_magic": 2.5, "archery": 3}))
    assert c.skills == {"magic": 10.0, "archery": 3}


def test_legacy_save_loads_everywhere_through_the_api(client):  # noqa: F811
    new_game(client, seed=7)
    with Session(client.engine) as s:                          # simulate a save from before this change
        rec = s.exec(select(CharacterRecord)).first()
        rec.skills = {"elemental_magic": 30.0, "necromancy": 5.0, "enchantment": 1.0, "swordsmanship": 10.0}
        s.add(rec)
        s.commit()
    ch = client.get("/api/character")
    assert ch.status_code == 200
    assert ch.json()["skills"] == {"magic": 36.0, "swordsmanship": 10.0}
    power = client.get("/api/power").json()
    assert next(l for l in power["stats"]["lines"] if l["source_id"] == "magic")["value"] == 36.0
    assert power["stats"]["total"] == 46.0
    assert client.get("/api/actions").status_code == 200
    play_one_year(client)                                             # a mutating call saves the cleaned skills
    with Session(client.engine) as s:
        saved = json.loads(json.dumps(s.exec(select(CharacterRecord)).first().skills))
    assert not REMOVED & set(saved) and saved["magic"] >= 36.0
