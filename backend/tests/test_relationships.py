"""Relationships between characters: parent and child, partners and other links; validation, queries, safety."""
import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app import relationships as rel
from app.models import Campaign, CharacterRecord, CharacterRelationship, NpcRecord
from app.npc import NpcFactory, NpcSpec
from app.npc_store import delete_npc, keep_temporary_npc, save_npc
from app.relationships import (
    CircularParentageError, RelationshipError, RelationshipKind as K, SelfRelationshipError,
    TemporaryCharacterError, TooManyParentsError, UnknownCharacterError, add_parent, add_relationship,
)

FACTORY = NpcFactory()
PERSISTENT = NpcSpec(category="persistent", species="human")


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all([Campaign(name="one", seed=1), Campaign(name="two", seed=2)])
        s.commit()
        s.engine = engine
        yield s


_seed = iter(range(1000, 100000))


def person(db, cid=1, **kw):
    """A saved persistent NPC; returns its id."""
    npc = FACTORY.generate(PERSISTENT.model_copy(update=kw) if kw else PERSISTENT, seed=next(_seed))
    save_npc(db, cid, npc)
    return npc.id


def edges(db, cid=1):
    return db.exec(select(CharacterRelationship).where(CharacterRelationship.campaign_id == cid)).all()


# ---------- parent and child ----------

def test_a_parent_child_link_can_be_created_and_queried_both_ways(db):
    mum, kid = person(db), person(db)
    link = add_relationship(db, 1, K.PARENT, mum, kid)
    assert (link.source_id, link.target_id, link.kind) == (mum, kid, "parent")
    assert rel.parents_of(db, 1, kid) == [mum] and rel.children_of(db, 1, mum) == [kid]
    assert rel.parents_of(db, 1, mum) == [] and rel.children_of(db, 1, kid) == []


def test_a_child_may_have_two_parents_one_or_none(db):
    a, b, c, orphan, one = person(db), person(db), person(db), person(db), person(db)
    both = person(db)
    add_parent(db, 1, both, a, b)
    add_parent(db, 1, one, a)
    assert set(rel.parents_of(db, 1, both)) == {a, b} and rel.parents_of(db, 1, one) == [a]
    assert rel.parents_of(db, 1, orphan) == []                                  # no parent needed to exist
    assert set(rel.children_of(db, 1, a)) == {both, one}
    with pytest.raises(TooManyParentsError):
        add_relationship(db, 1, K.PARENT, c, both)
    assert set(rel.parents_of(db, 1, both)) == {a, b}


def test_add_parent_is_all_or_nothing_and_keeps_the_callers_other_work(db):
    p, kid = person(db), person(db)
    pending = FACTORY.generate(PERSISTENT, seed=777)
    save_npc(db, 1, pending)                                                    # unflushed-to-commit caller work
    with pytest.raises(UnknownCharacterError):
        add_parent(db, 1, kid, p, "npc_nobody")
    assert rel.parents_of(db, 1, kid) == []                                     # p was not left behind
    assert db.exec(select(NpcRecord).where(NpcRecord.npc_id == pending.id)).first() is not None
    with pytest.raises(TooManyParentsError):
        add_parent(db, 1, kid, p, person(db), person(db))
    with pytest.raises(RelationshipError, match="twice"):
        add_parent(db, 1, kid, p, p)


# ---------- invalid relationships ----------

def test_a_character_cannot_be_their_own_parent_or_relative(db):
    a = person(db)
    for kind in K:
        with pytest.raises(SelfRelationshipError):
            add_relationship(db, 1, kind, a, a)
    assert edges(db) == []


def test_the_database_itself_refuses_a_self_link(db):
    db.add(CharacterRelationship(campaign_id=1, kind="friend", source_id="x", target_id="x"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_circular_parentage_is_refused_however_long_the_loop(db):
    a, b, c, d = (person(db) for _ in range(4))
    add_relationship(db, 1, K.PARENT, a, b)
    with pytest.raises(CircularParentageError):
        add_relationship(db, 1, K.PARENT, b, a)                                 # direct
    add_relationship(db, 1, K.PARENT, b, c)
    add_relationship(db, 1, K.PARENT, c, d)
    with pytest.raises(CircularParentageError):
        add_relationship(db, 1, K.PARENT, d, a)                                 # four generations round
    assert len(edges(db)) == 3


def test_unknown_and_temporary_characters_cannot_be_related(db):
    a = person(db)
    with pytest.raises(UnknownCharacterError):
        add_relationship(db, 1, K.PARENT, a, "npc_ghost")
    temp = FACTORY.generate(NpcSpec(species="human"), seed=5)
    keep_temporary_npc(db, 1, temp)
    with pytest.raises(TemporaryCharacterError, match="promote"):
        add_relationship(db, 1, K.FRIEND, a, temp.id)
    save_npc(db, 1, FACTORY.promote(temp))                                      # promoted, it can be related
    assert add_relationship(db, 1, K.FRIEND, a, temp.id)


def test_unknown_kind_is_refused(db):
    a, b = person(db), person(db)
    with pytest.raises(RelationshipError, match="unknown relationship kind"):
        add_relationship(db, 1, "nemesis", a, b)


def test_characters_of_another_campaign_cannot_be_related(db):
    here, there = person(db, 1), person(db, 2)
    with pytest.raises(UnknownCharacterError):
        add_relationship(db, 1, K.FRIEND, here, there)


# ---------- retries and duplicates ----------

def test_repeating_a_link_returns_the_same_one(db):
    a, b = person(db), person(db)
    first = add_relationship(db, 1, K.PARENT, a, b)
    again = add_relationship(db, 1, K.PARENT, a, b)
    assert again.id == first.id and len(edges(db)) == 1


def test_symmetric_links_are_stored_once_whichever_way_they_are_given(db):
    a, b = person(db), person(db)
    one = add_relationship(db, 1, K.PARTNER, a, b)
    two = add_relationship(db, 1, K.PARTNER, b, a)
    assert one.id == two.id and len(edges(db)) == 1
    assert rel.related_to(db, 1, a, K.PARTNER) == [b] and rel.related_to(db, 1, b, K.PARTNER) == [a]
    assert rel.remove_relationship(db, 1, K.PARTNER, b, a) and edges(db) == []


def test_directed_links_are_not_symmetric(db):
    mentor, pupil = person(db), person(db)
    add_relationship(db, 1, K.MENTOR, mentor, pupil)
    assert rel.related_to(db, 1, mentor, K.MENTOR) == [pupil] and rel.related_to(db, 1, pupil, K.MENTOR) == []
    add_relationship(db, 1, K.MENTOR, pupil, mentor)                            # the reverse is a different link
    assert len(edges(db)) == 2


def test_a_lost_race_returns_the_winners_link_and_keeps_the_session_usable(db, monkeypatch):
    a, b = person(db), person(db)
    winner = add_relationship(db, 1, K.PARENT, a, b)
    real = rel._edge
    calls = {"n": 0}

    def blind_once(*args, **kw):                         # the first look finds nothing, as if a rival wrote after it
        calls["n"] += 1
        return None if calls["n"] == 1 else real(*args, **kw)
    monkeypatch.setattr(rel, "_edge", blind_once)
    again = add_relationship(db, 1, K.PARENT, a, b)
    assert again.id == winner.id and len(edges(db)) == 1
    assert person(db)                                                           # and the session still works


# ---------- descendants ----------

def build_tree(db):
    """gran -> (mum, uncle); mum + dad -> (kid1, kid2); kid1 -> grandkid"""
    ids = {n: person(db) for n in ("gran", "mum", "dad", "uncle", "kid1", "kid2", "grandkid")}
    for parent, child in (("gran", "mum"), ("gran", "uncle"), ("mum", "kid1"), ("dad", "kid1"),
                          ("mum", "kid2"), ("dad", "kid2"), ("kid1", "grandkid")):
        add_relationship(db, 1, K.PARENT, ids[parent], ids[child])
    return ids


def test_descendants_come_nearest_generation_first_with_no_repeats(db):
    t = build_tree(db)
    d = rel.descendants_of(db, 1, t["gran"])
    assert set(d[:2]) == {t["mum"], t["uncle"]} and set(d[2:4]) == {t["kid1"], t["kid2"]} and d[4] == t["grandkid"]
    assert len(d) == len(set(d)) == 5
    assert rel.descendants_of(db, 1, t["gran"], max_depth=1) == d[:2]
    assert set(rel.ancestors_of(db, 1, t["grandkid"])) == {t["kid1"], t["mum"], t["dad"], t["gran"]}
    assert rel.descendants_of(db, 1, t["grandkid"]) == []


def test_only_active_descendants_are_eligible(db):
    t = build_tree(db)
    for name, action in (("kid2", "dead"), ("uncle", "retired")):
        rec = db.exec(select(NpcRecord).where(NpcRecord.npc_id == t[name])).one()
        from app.npc import Npc
        npc = Npc.model_validate(rec.data)
        npc.mark_deceased("a fever") if action == "dead" else npc.retire()
        save_npc(db, 1, npc)
    eligible = rel.eligible_descendants(db, 1, t["gran"])
    assert [n.id for n in eligible][:1] == [t["mum"]]
    assert {n.id for n in eligible} == {t["mum"], t["kid1"], t["grandkid"]}
    assert all(n.status == "active" for n in eligible)


def test_family_queries_take_one_query_per_generation_not_one_per_person(db):
    t = build_tree(db)
    db.commit()
    counted = []
    listener = lambda conn, cur, stmt, *a: counted.append(stmt)
    event.listen(db.engine, "before_cursor_execute", listener)
    try:
        rel.eligible_descendants(db, 1, t["gran"])
    finally:
        event.remove(db.engine, "before_cursor_execute", listener)
    assert len(counted) <= 3 * 4 + 2, f"{len(counted)} queries for a 3-generation tree"


# ---------- missing targets ----------

def test_a_deleted_character_is_skipped_and_dangling_links_can_be_pruned(db):
    t = build_tree(db)
    row = db.exec(select(NpcRecord).where(NpcRecord.npc_id == t["mum"])).one()
    db.delete(row)                                                              # removed behind the store's back
    db.flush()
    assert rel.parents_of(db, 1, t["kid1"]) == [t["dad"]]
    assert t["mum"] not in rel.descendants_of(db, 1, t["gran"])
    assert rel.prune_dangling(db, 1) == 3 and rel.prune_dangling(db, 1) == 0     # gran>mum, mum>kid1, mum>kid2
    assert all(t["mum"] not in (e.source_id, e.target_id) for e in edges(db))


def test_deleting_a_character_through_the_store_removes_their_links(db):
    t = build_tree(db)
    assert delete_npc(db, 1, t["mum"])
    assert not rel.relationships_of(db, 1, t["mum"])
    assert rel.parents_of(db, 1, t["kid1"]) == [t["dad"]] and rel.children_of(db, 1, t["gran"]) == [t["uncle"]]


def test_a_corrupt_circular_tree_cannot_hang_the_walk(db):
    a, b = person(db), person(db)
    db.add_all([CharacterRelationship(campaign_id=1, kind="parent", source_id=a, target_id=b),
                CharacterRelationship(campaign_id=1, kind="parent", source_id=b, target_id=a)])
    db.flush()
    assert set(rel.descendants_of(db, 1, a)) <= {a, b} and len(rel.descendants_of(db, 1, a)) <= 2


# ---------- the playable character ----------

def test_the_playable_character_can_be_a_parent(db):
    db.add(CharacterRecord(campaign_id=1, name="Aren"))
    db.flush()
    pc = rel.player_ref(db.exec(select(CharacterRecord)).one())
    kid = person(db)
    add_relationship(db, 1, K.PARENT, pc, kid)
    assert rel.parents_of(db, 1, kid) == [pc] and rel.children_of(db, 1, pc) == [kid]
    assert [n.id for n in rel.eligible_descendants(db, 1, pc)] == [kid]
    with pytest.raises(UnknownCharacterError):
        add_relationship(db, 1, K.PARENT, "pc_999", kid)
    with pytest.raises(UnknownCharacterError):                                  # right number, wrong campaign
        add_relationship(db, 2, K.PARENT, pc, person(db, 2))
    with pytest.raises(ValueError):
        rel.player_ref(CharacterRecord(campaign_id=1, name="unsaved"))
