"""NPC awareness and relationship registration: when an NPC becomes Notable or an Acquaintance, and when they do not.

Awareness (Notable), family and personal relationship (Acquaintance) are separate states. Nothing registers by itself.
"""
import pytest
from sqlmodel import Session, create_engine, select

from app import awareness as aw
from app import named
from app import relationships as rel
from app.db import init_db
from app.models import Campaign, KnownNpc, NpcRecord
from app.npc_store import load_npc, save_npc
from app.relationships import player_ref
from app.story import service
from app.story.errors import StoryContentError, StoryEffectError
from tests.test_api import client, new_game  # noqa: F401  (client is a fixture)
from tests.progress_helpers import person
from tests.story_helpers import ev
from tests.test_named_npcs import choose, defs, lib, spawn, spawn_event, story_world, world, db  # noqa: F401  (fixtures)

CID = 1


def act(*effects, event_id="act", **kw):
    return ev(event_id, choices=[{"id": "go", "text": "Go", "effects": list(effects)}], **kw)


def row(db, npc_id):
    return db.exec(select(KnownNpc).where(KnownNpc.npc_id == npc_id)).first()


def stranger(db, seed=5):
    n = person(age=40, seed=seed)
    save_npc(db, CID, n)
    return n.id


# ======================================================================== the registration rules (awareness.py)

def test_nothing_is_registered_by_existing_or_being_saved(world):
    db, rec = world
    npc = stranger(db)
    assert row(db, npc) is None and aw.state_of(db, CID, player_ref(rec), npc) == aw.PersonalState()
    assert aw.acquainted_ids(db, CID, player_ref(rec)) == []


def test_first_time_establishing_makes_an_acquaintance_with_a_starting_closeness(world):
    db, rec = world
    me, npc = player_ref(rec), stranger(db)
    r = aw.establish_acquaintance(db, CID, me, npc, kind="friend")
    assert r.changed and r.acquainted and r.aware and r.closeness == aw.INITIAL_CLOSENESS and r.stance == "open"
    assert aw.acquainted_ids(db, CID, me) == [npc]
    assert rel.related_to(db, CID, me, "friend") == [npc]                      # the link, once
    assert row(db, npc).first_met_year == 0 and row(db, npc).acquainted_year == 0


def test_meeting_again_changes_nothing_and_creates_no_duplicate(world):
    db, rec = world
    me, npc = player_ref(rec), stranger(db)
    aw.establish_acquaintance(db, CID, me, npc, kind="friend", closeness=40)
    aw.change_closeness(db, CID, me, npc, 5)
    again = aw.establish_acquaintance(db, CID, me, npc, kind="friend", closeness=10)
    assert not again.changed and again.reason == "already an acquaintance" and again.closeness == 45     # not reset
    assert len(db.exec(select(KnownNpc)).all()) == 1 and len(db.exec(select(NpcRecord).where(NpcRecord.npc_id == npc)).all()) == 1
    assert len(rel.relationships_of(db, CID, me, "friend")) == 1


def test_a_custom_starting_closeness_is_honoured_and_validated(world):
    db, rec = world
    me = player_ref(rec)
    assert aw.establish_acquaintance(db, CID, me, stranger(db, 1), closeness=60).closeness == 60
    for bad in (-1, 101):
        with pytest.raises(aw.AwarenessError):
            aw.establish_acquaintance(db, CID, me, stranger(db, 2), closeness=bad)
    with pytest.raises(aw.AwarenessError):
        aw.establish_acquaintance(db, CID, me, stranger(db, 3), kind="parent")      # not an acquaintance kind


def test_revealing_is_awareness_only(world):
    db, rec = world
    me, npc = player_ref(rec), stranger(db)
    r = aw.reveal(db, CID, me, npc)
    assert r.changed and r.aware and not r.acquainted and r.closeness is None
    assert aw.acquainted_ids(db, CID, me) == [] and rel.relationships_of(db, CID, me) == []
    assert row(db, npc).source == "story"
    again = aw.reveal(db, CID, me, npc, source="encounter")
    assert not again.changed and again.reason == "already aware" and row(db, npc).source == "story"      # first source kept
    assert len(db.exec(select(KnownNpc)).all()) == 1


def test_revealing_then_meeting_uses_the_same_record(world):
    db, rec = world
    me, npc = player_ref(rec), stranger(db)
    aw.reveal(db, CID, me, npc)
    aw.establish_acquaintance(db, CID, me, npc)
    assert len(db.exec(select(KnownNpc)).all()) == 1 and aw.state_of(db, CID, me, npc).acquainted


def test_improving_and_damaging_an_existing_relationship(world):
    db, rec = world
    me, npc = player_ref(rec), stranger(db)
    aw.establish_acquaintance(db, CID, me, npc, closeness=50)
    assert aw.change_closeness(db, CID, me, npc, 20).closeness == 70
    assert aw.change_closeness(db, CID, me, npc, -30).closeness == 40
    assert aw.change_closeness(db, CID, me, npc, 500).closeness == 100
    capped = aw.change_closeness(db, CID, me, npc, 5)
    assert not capped.changed and capped.closeness == 100
    assert aw.change_closeness(db, CID, me, npc, -500).closeness == 0
    assert aw.state_of(db, CID, me, npc).acquainted                              # damage alone never ends it


def test_changing_closeness_of_a_stranger_registers_nobody(world):
    db, rec = world
    me, npc = player_ref(rec), stranger(db)
    r = aw.change_closeness(db, CID, me, npc, 30)
    assert not r.changed and r.reason == "not an acquaintance" and row(db, npc) is None
    aw.reveal(db, CID, me, npc)                                                   # aware, still not an acquaintance
    assert not aw.change_closeness(db, CID, me, npc, 30).changed and row(db, npc).closeness is None


# ---------- negative encounters ----------

def test_hostility_toward_a_stranger_never_makes_an_acquaintance(world):
    db, rec = world
    me, npc = player_ref(rec), stranger(db)
    r = aw.set_stance(db, CID, me, npc, "hostile")                                # "punching a stranger"
    assert r.changed and r.stance == "hostile" and not r.acquainted and r.closeness is None
    assert aw.acquainted_ids(db, CID, me) == []
    assert row(db, npc).stance == "hostile" and row(db, npc).acquainted is False


def test_a_hostile_or_refusing_npc_cannot_be_befriended_until_contact_is_restored(world):
    db, rec = world
    me, npc = player_ref(rec), stranger(db)
    for stance, why in (("hostile", "they are hostile"), ("refused", "they refuse further contact")):
        aw.set_stance(db, CID, me, npc, stance)
        r = aw.establish_acquaintance(db, CID, me, npc, kind="friend")
        assert not r.changed and r.reason == why and not r.acquainted
        assert rel.relationships_of(db, CID, me) == []
    aw.set_stance(db, CID, me, npc, "open")
    assert aw.establish_acquaintance(db, CID, me, npc).acquainted


def test_turning_hostile_ends_an_existing_acquaintance_but_keeps_everything_else(world):
    db, rec = world
    me, npc = player_ref(rec), stranger(db)
    aw.establish_acquaintance(db, CID, me, npc, kind="friend")
    r = aw.set_stance(db, CID, me, npc, "hostile")
    assert r.changed and not r.acquainted and r.closeness is None
    assert rel.relationships_of(db, CID, me) == [] and load_npc(db, CID, npc) is not None and row(db, npc) is not None


def test_stance_changes_are_idempotent_and_restoring_a_stranger_registers_nobody(world):
    db, rec = world
    me, npc = player_ref(rec), stranger(db)
    assert not aw.set_stance(db, CID, me, npc, "open").changed and row(db, npc) is None
    aw.set_stance(db, CID, me, npc, "hostile")
    assert not aw.set_stance(db, CID, me, npc, "hostile").changed
    with pytest.raises(aw.AwarenessError):
        aw.set_stance(db, CID, me, npc, "furious")


def test_the_dead_and_gone_cannot_be_befriended(world):
    db, rec = world
    me = player_ref(rec)
    dead, away = stranger(db, 8), stranger(db, 9)
    n = load_npc(db, CID, dead); n.mark_deceased("illness"); save_npc(db, CID, n)
    n = load_npc(db, CID, away); n.retire(); save_npc(db, CID, n)
    for npc, why in ((dead, "deceased"), (away, "retired")):
        r = aw.establish_acquaintance(db, CID, me, npc)
        assert not r.changed and why in r.reason and not r.acquainted


# ---------- ending a relationship ----------

def test_ending_a_relationship_keeps_the_character_awareness_and_family(world):
    db, rec = world
    me = player_ref(rec)
    from app.descendants import create_descendant
    kid = create_descendant(db, CID, [me], seed=4, age=20).npc.id               # family, and about to be a friend too
    aw.establish_acquaintance(db, CID, me, kid, kind="friend")
    mentor = stranger(db)
    aw.establish_acquaintance(db, CID, me, mentor, kind="friend", closeness=70)
    rel.add_relationship(db, CID, "mentor", mentor, me)
    r = aw.end_relationship(db, CID, me, mentor)
    assert r.changed and not r.acquainted and r.closeness is None and r.aware
    assert load_npc(db, CID, mentor) is not None                                  # the character is not deleted
    assert rel.relationships_of(db, CID, me, "friend") != [] and mentor not in rel.related_to(db, CID, me, "friend")
    assert rel.related_to(db, CID, me, "mentor") == []                            # the directed link went too
    assert row(db, mentor) is not None                                            # still aware of them
    aw.end_relationship(db, CID, me, kid)
    assert rel.parents_of(db, CID, kid) == [me]                                   # family is untouched
    assert kid not in aw.acquainted_ids(db, CID, me)


def test_ending_a_relationship_that_does_not_exist_is_a_noop(world):
    db, rec = world
    me, npc = player_ref(rec), stranger(db)
    assert aw.end_relationship(db, CID, me, npc).reason == "not an acquaintance" and row(db, npc) is None
    aw.reveal(db, CID, me, npc)
    assert not aw.end_relationship(db, CID, me, npc).changed and row(db, npc) is not None


def test_an_ended_relationship_can_be_established_again(world):
    db, rec = world
    me, npc = player_ref(rec), stranger(db)
    aw.establish_acquaintance(db, CID, me, npc, closeness=80)
    aw.end_relationship(db, CID, me, npc)
    r = aw.establish_acquaintance(db, CID, me, npc)
    assert r.changed and r.closeness == aw.INITIAL_CLOSENESS and len(db.exec(select(KnownNpc)).all()) == 1


# ---------- identity and validity ----------

def test_only_kept_npcs_of_this_campaign_can_be_registered(world):
    db, rec = world
    me = player_ref(rec)
    from app.npc import NpcFactory, NpcSpec
    from app.npc_store import keep_temporary_npc
    light = NpcFactory().generate(NpcSpec(species="human"), seed=3)
    keep_temporary_npc(db, CID, light)
    other = person(age=30, seed=77)
    db.add(Campaign(name="two", seed=2)); db.flush()
    save_npc(db, 2, other)
    for bad in (light.id, other.id, "npc_nope"):
        with pytest.raises(aw.AwarenessError):
            aw.reveal(db, CID, me, bad)
    with pytest.raises(aw.AwarenessError):
        aw.reveal(db, CID, "npc_notaplayer", stranger(db))


def test_a_new_playable_life_is_aware_of_who_the_last_knew_but_is_not_their_friend(world):
    db, rec = world
    old, new, npc = player_ref(rec), "pc_99", stranger(db)
    aw.establish_acquaintance(db, CID, old, npc, closeness=70)
    assert aw.state_of(db, CID, new, npc) == aw.PersonalState()                    # nothing personal carries over...
    assert row(db, npc) is not None                                                # ...awareness does
    r = aw.establish_acquaintance(db, CID, new, npc)
    assert r.closeness == aw.INITIAL_CLOSENESS and aw.acquainted_ids(db, CID, old) == []
    assert len(db.exec(select(KnownNpc)).all()) == 1


def test_an_ended_relationship_of_another_life_is_not_ended_by_a_new_one(world):
    db, rec = world
    old, npc = player_ref(rec), stranger(db)
    aw.establish_acquaintance(db, CID, old, npc)
    assert not aw.end_relationship(db, CID, "pc_99", npc).changed
    assert aw.state_of(db, CID, old, npc).acquainted


# ---------- persistence ----------

def test_state_survives_a_save_and_reload(tmp_path):
    from tests.story_helpers import make_life
    url = f"sqlite:///{tmp_path / 'g.db'}"
    eng = create_engine(url)
    init_db(eng)
    with Session(eng) as s:
        rec = make_life(s, seed=7, age=40)
        me, a, b, c = player_ref(rec), stranger(s, 1), stranger(s, 2), stranger(s, 3)
        aw.establish_acquaintance(s, CID, me, a, kind="friend", closeness=55)
        aw.reveal(s, CID, me, b)
        aw.set_stance(s, CID, me, c, "refused")
        s.commit()
    with Session(create_engine(url)) as s:
        assert aw.state_of(s, CID, me, a) == aw.PersonalState(True, 55, "open")
        assert aw.state_of(s, CID, me, b) == aw.PersonalState() and row(s, b) is not None
        assert aw.state_of(s, CID, me, c) == aw.PersonalState(False, None, "refused")
        assert aw.establish_acquaintance(s, CID, me, a).reason == "already an acquaintance"      # idempotent after a reload
        assert len(s.exec(select(KnownNpc)).all()) == 3


def test_a_save_made_before_personal_state_existed_loads_as_aware_but_not_acquainted(tmp_path):
    from sqlalchemy import text
    from tests.story_helpers import make_life
    path = tmp_path / "old.db"
    eng = create_engine(f"sqlite:///{path}")
    init_db(eng)
    with Session(eng) as s:
        make_life(s, seed=7, age=40)
        npc = stranger(s)
        s.commit()
    with eng.begin() as c:                                        # recreate the earlier shape of the table, with a row in it
        c.execute(text("DROP TABLE knownnpc"))
        c.execute(text("CREATE TABLE knownnpc (id INTEGER PRIMARY KEY, campaign_id INTEGER, npc_id VARCHAR, source VARCHAR,"
                       " first_met_year INTEGER, created_at DATETIME)"))
        c.execute(text("INSERT INTO knownnpc (campaign_id, npc_id, source, first_met_year, created_at)"
                       f" VALUES (1, '{npc}', 'encounter', 3, '2026-01-01')"))
    init_db(eng)
    init_db(eng)                                                  # twice is harmless
    with Session(eng) as s:
        k = s.exec(select(KnownNpc)).one()
        assert (k.holder, k.acquainted, k.closeness, k.stance) == (None, False, None, "open")
        assert aw.personal_state(k, "pc_1") == aw.PersonalState()
        assert k.first_met_year == 3                              # the awareness it already had is kept


# ======================================================================== through the existing story events

def test_only_naming_an_npc_in_an_event_registers_nothing(story_world, defs):
    db, rec = story_world
    library = lib(defs, ev("gossip", npc="test_sage", choices=[{"id": "go", "text": "Hear the rumours"}]))
    choose(db, rec, library, "gossip")                                           # the NPC is mentioned and referenced
    assert named.get_instance(db, 1, "test_sage") is None and db.exec(select(KnownNpc)).all() == []


def test_spawning_makes_a_character_notable_but_never_an_acquaintance(story_world, defs):
    db, rec = story_world
    choose(db, rec, lib(defs, spawn_event("meet")), "meet")
    k = row(db, "named_test_sage")
    assert k is not None and k.acquainted is False and aw.acquainted_ids(db, 1, player_ref(rec)) == []


def test_reveal_npc_makes_a_notable_figure_once_however_many_events(story_world, defs):
    db, rec = story_world
    reveal = {"type": "reveal_npc", "npc": "test_sage"}
    library = lib(defs, act(reveal, event_id="rumour"), act(reveal, event_id="portrait"))
    first = choose(db, rec, library, "rumour")["effects"][0]
    second = choose(db, rec, library, "portrait")["effects"][0]
    assert first["changed"] is True and second["changed"] is False and second["reason"] == "already aware"
    assert first["acquainted"] is False
    assert len(db.exec(select(NpcRecord).where(NpcRecord.npc_id == "named_test_sage")).all()) == 1
    assert len(db.exec(select(KnownNpc)).all()) == 1 and aw.acquainted_ids(db, 1, player_ref(rec)) == []


def test_reveal_respects_the_definitions_encounter_conditions(story_world, tmp_path, monkeypatch):
    from tests.test_named_npcs import FILE, write
    from app.npc import load_named_characters
    db, rec = story_world
    gated = load_named_characters(path=write(tmp_path, FILE.replace("{type: age_at_least, value: 5}", "{type: age_at_least, value: 99}")))
    monkeypatch.setattr("app.named.definitions", lambda: gated)
    r = choose(db, rec, lib(gated, act({"type": "reveal_npc", "npc": "test_sage"})), "act")["effects"][0]
    assert r["changed"] is False and r["reason"] == "encounter conditions not met" and db.exec(select(KnownNpc)).all() == []


def test_a_positive_encounter_establishes_an_acquaintance_who_is_also_notable(story_world, defs):
    db, rec = story_world
    library = lib(defs, act({"type": "spawn_npc", "npc": "test_sage"},
                            {"type": "establish_acquaintance", "npc": "test_sage", "kind": "friend", "closeness": 25}))
    out = choose(db, rec, library, "act")["effects"][1]
    assert out["changed"] and out["acquainted"] and out["closeness"] == 25
    me = player_ref(rec)
    assert aw.acquainted_ids(db, 1, me) == ["named_test_sage"]
    assert named.get_instance(db, 1, "test_sage") is not None
    assert "named_test_sage" in rel.related_to(db, 1, me, "friend")


def test_meeting_the_same_npc_in_several_events_never_duplicates_anything(story_world, defs):
    db, rec = story_world
    meet = [{"type": "spawn_npc", "npc": "test_sage"}, {"type": "establish_acquaintance", "npc": "test_sage", "kind": "friend"},
            {"type": "change_relationship", "npc": "test_sage", "amount": 5}]
    library = lib(defs, act(*meet, event_id="one"), act(*meet, event_id="two"), act(*meet, event_id="three"))
    for e in ("one", "two", "three"):
        choose(db, rec, library, e)
    me = player_ref(rec)
    assert len(db.exec(select(NpcRecord).where(NpcRecord.npc_id == "named_test_sage")).all()) == 1
    assert len(db.exec(select(KnownNpc)).all()) == 1 and len(rel.relationships_of(db, 1, me, "friend")) == 1
    assert aw.state_of(db, 1, me, "named_test_sage").closeness == aw.INITIAL_CLOSENESS + 15     # only the closeness moves


def test_an_aggressive_encounter_makes_enemies_not_acquaintances(story_world, defs):
    db, rec = story_world
    punch = act({"type": "spawn_npc", "npc": "test_plain"}, {"type": "set_npc_stance", "npc": "test_plain", "stance": "hostile"},
                event_id="punch")
    befriend = act({"type": "establish_acquaintance", "npc": "test_plain"}, event_id="befriend")
    library = lib(defs, punch, befriend)
    choose(db, rec, library, "punch")
    me = player_ref(rec)
    assert aw.acquainted_ids(db, 1, me) == [] and aw.state_of(db, 1, me, "named_test_plain").stance == "hostile"
    out = choose(db, rec, library, "befriend")["effects"][0]
    assert out["changed"] is False and out["reason"] == "they are hostile" and aw.acquainted_ids(db, 1, me) == []
    assert row(db, "named_test_plain") is not None                               # they are known of, as someone who hates you


def test_ending_it_by_event_keeps_the_notable_and_family_state(story_world, defs):
    db, rec = story_world
    me = player_ref(rec)
    setup = act({"type": "spawn_npc", "npc": "test_sage"}, {"type": "link_npc", "npc": "test_sage", "kind": "parent", "other": "player"},
                {"type": "establish_acquaintance", "npc": "test_sage", "kind": "friend"}, event_id="setup")
    end = act({"type": "end_relationship", "npc": "test_sage"}, event_id="end")
    library = lib(defs, setup, end)
    choose(db, rec, library, "setup")
    assert "named_test_sage" in aw.acquainted_ids(db, 1, me)
    out = choose(db, rec, library, "end")["effects"][0]
    assert out["changed"] and out["acquainted"] is False
    assert aw.acquainted_ids(db, 1, me) == [] and named.get_instance(db, 1, "test_sage") is not None
    assert row(db, "named_test_sage") is not None                                # still a notable figure
    assert rel.parents_of(db, 1, me) == ["named_test_sage"]                      # still family
    assert rel.related_to(db, 1, me, "friend") == []


def test_improve_and_damage_by_event_only_affect_an_existing_acquaintance(story_world, defs):
    db, rec = story_world
    me = player_ref(rec)
    library = lib(defs, act({"type": "spawn_npc", "npc": "test_sage"}, {"type": "change_relationship", "npc": "test_sage", "amount": 30},
                            event_id="early"),
                  act({"type": "establish_acquaintance", "npc": "test_sage", "closeness": 50}, event_id="meet"),
                  act({"type": "change_relationship", "npc": "test_sage", "amount": 20}, event_id="gift"),
                  act({"type": "change_relationship", "npc": "test_sage", "amount": -45}, event_id="insult"))
    assert choose(db, rec, library, "early")["effects"][1]["reason"] == "not an acquaintance"
    assert aw.state_of(db, 1, me, "named_test_sage").acquainted is False
    choose(db, rec, library, "meet")
    assert choose(db, rec, library, "gift")["effects"][0]["closeness"] == 70
    assert choose(db, rec, library, "insult")["effects"][0]["closeness"] == 25


def test_conditions_let_events_branch_on_relationship_state(story_world, defs):
    from app.story.conditions import evaluate, parse_condition
    from tests.story_helpers import ctx_for
    db, rec = story_world
    ctx = ctx_for(db, rec, lib(defs, ev("x")))
    holds = lambda c: evaluate(parse_condition(c), ctx)
    npc = {"npc": "test_sage"}
    assert not holds({"type": "npc_acquainted", **npc}) and not holds({"type": "npc_stance", "stance": "open", **npc})   # not introduced
    spawn(db, defs, "test_sage")
    me = player_ref(rec)
    assert not holds({"type": "npc_acquainted", **npc}) and holds({"type": "npc_stance", "stance": "open", **npc})
    aw.establish_acquaintance(db, 1, me, "named_test_sage", closeness=40)
    assert holds({"type": "npc_acquainted", **npc}) and holds({"type": "npc_closeness_at_least", "value": 40, **npc})
    assert not holds({"type": "npc_closeness_at_least", "value": 41, **npc})
    aw.set_stance(db, 1, me, "named_test_sage", "refused")
    assert not holds({"type": "npc_acquainted", **npc}) and holds({"type": "npc_stance", "stance": "refused", **npc})
    assert not holds({"type": "npc_closeness_at_least", "value": 0, **npc})


def test_registering_a_character_who_was_never_introduced_fails_and_rolls_the_choice_back(story_world, defs):
    db, rec = story_world
    library = lib(defs, act({"type": "spawn_npc", "npc": "test_sage"}, {"type": "establish_acquaintance", "npc": "test_sage"},
                            {"type": "establish_acquaintance", "npc": "test_plain"}))
    inst = service.start_event(db, rec, "act", library=library)
    with pytest.raises(StoryEffectError, match="has not been introduced"):
        service.resolve_choice(db, rec, inst["instance_id"], "go", library=library)
    assert named.get_instance(db, 1, "test_sage") is None and db.exec(select(KnownNpc)).all() == []
    assert aw.acquainted_ids(db, 1, player_ref(rec)) == []


@pytest.mark.parametrize("bad", [
    {"type": "reveal_npc"}, {"type": "reveal_npc", "npc": "test_sage", "kind": "friend"},
    {"type": "establish_acquaintance", "npc": "test_sage", "kind": "parent"},
    {"type": "establish_acquaintance", "npc": "test_sage", "closeness": 101},
    {"type": "change_relationship", "npc": "test_sage", "amount": 1000},
    {"type": "change_relationship", "npc": "test_sage"},
    {"type": "set_npc_stance", "npc": "test_sage", "stance": "furious"},
    {"type": "end_relationship", "npc": "test_sage", "extra": 1},
])
def test_registration_effects_are_validated_when_the_story_loads(defs, bad):
    with pytest.raises(StoryContentError):
        lib(defs, act(bad))


@pytest.mark.parametrize("effect", ["reveal_npc", "establish_acquaintance", "set_npc_stance", "end_relationship"])
def test_registration_effects_must_name_a_defined_character(defs, effect):
    params = {"stance": "hostile"} if effect == "set_npc_stance" else {}
    with pytest.raises(StoryContentError, match="unknown named character 'ghost'"):
        lib(defs, act({"type": effect, "npc": "ghost", **params}))


def test_the_exported_story_schema_lists_the_registration_types():
    from app.story.export_schema import build
    schema = build()
    assert {"reveal_npc", "establish_acquaintance", "change_relationship", "set_npc_stance", "end_relationship"} <= set(schema["effect_types"])
    assert {"npc_acquainted", "npc_closeness_at_least", "npc_stance"} <= set(schema["condition_types"])


# ======================================================================== what the player sees (the Character tab API)

def test_the_api_shows_the_three_states_independently(client, defs, monkeypatch):
    from tests.test_legacy import latest
    monkeypatch.setattr("app.named.definitions", lambda: defs)
    new_game(client)
    with Session(client.engine) as s:
        me = player_ref(latest(s))
        sage = spawn(s, defs, "test_sage").npc.id                      # introduced: Notable
        plain = spawn(s, defs, "test_plain").npc.id
        aw.establish_acquaintance(s, 1, me, plain, kind="friend", closeness=65)
        s.commit()
    get = lambda cat: {i["id"]: i for i in client.get("/api/npcs", params={"category": cat}).json()["items"]}
    assert set(get("notable")) == {sage, plain} and set(get("acquaintances")) == {plain}
    item = get("acquaintances")[plain]
    assert item["closeness"] == 65 and item["contact"] == "open" and item["categories"] == ["acquaintances", "notable"]
    assert get("notable")[sage]["closeness"] is None
    with Session(client.engine) as s:
        aw.end_relationship(s, 1, me, plain)
        aw.set_stance(s, 1, me, sage, "hostile")
        s.commit()
    assert set(get("acquaintances")) == set() and set(get("notable")) == {sage, plain}      # notable survives
    assert get("notable")[sage]["contact"] == "hostile" and get("notable")[plain]["closeness"] is None
    assert client.get(f"/api/npcs/{plain}").json()["closeness"] is None


def test_an_heirs_tab_keeps_family_and_notable_but_not_their_parents_friends(client, defs, monkeypatch):
    from tests import test_legacy as lg
    from app.descendants import create_descendant
    monkeypatch.setattr("app.named.definitions", lambda: defs)
    new_game(client)
    lg.set_latest(client, age=60)
    with Session(client.engine) as s:
        me = player_ref(lg.latest(s))
        kid = create_descendant(s, 1, [me], seed=1, age=25).npc.id
        sib = create_descendant(s, 1, [me], seed=2, age=20).npc.id
        sage = spawn(s, defs, "test_sage").npc.id
        aw.establish_acquaintance(s, 1, me, sage, closeness=80)
        s.commit()
    lg.kill(client)
    assert lg.cont(client, kid).status_code == 200
    get = lambda cat: {i["id"] for i in client.get("/api/npcs", params={"category": cat}).json()["items"]}
    assert sib in get("family") and sage in get("notable")          # family and awareness carry on
    assert get("acquaintances") == set()                            # the parent's friendship does not
