"""Family queries (siblings, half-siblings, cousins, ancestors, descendants) and validation of the whole tree."""
import pytest
from sqlmodel import Session, select

from app import family as fam
from app import relationships as rel
from app.descendants import create_descendant
from app.inheritance import InheritanceRules, ParentNotEligibleError, get_inheritance_rules
from app.models import CharacterRelationship, NpcRecord
from app.npc_store import delete_npc, save_npc
from tests.progress_helpers import new_db, person

CID = 1


@pytest.fixture
def db():
    with Session(new_db()) as s:
        yield s


_seeds = iter(range(10_000, 99_999))


def adult(db, seed=None):
    n = person(age=40, seed=next(_seeds) if seed is None else seed)
    save_npc(db, CID, n)
    return n.id


def child_of(db, *parents):
    n = person(age=5, seed=next(_seeds))
    save_npc(db, CID, n)
    rel.add_parent(db, CID, n.id, *parents)
    return n.id


@pytest.fixture
def tree(db):
    """gran1 + gran2 -> mum, uncle ; dad + mum -> kid, sis ; dad + other -> half ; uncle + aunt -> cousin"""
    t = {k: adult(db) for k in ["gran1", "gran2", "dad", "other", "aunt"]}
    t["mum"] = child_of(db, t["gran1"], t["gran2"])
    t["uncle"] = child_of(db, t["gran1"], t["gran2"])
    t["kid"] = child_of(db, t["dad"], t["mum"])
    t["sis"] = child_of(db, t["dad"], t["mum"])
    t["half"] = child_of(db, t["dad"], t["other"])
    t["cousin"] = child_of(db, t["uncle"], t["aunt"])
    t["solo"] = child_of(db, t["dad"])
    return t


# ---------- siblings ----------

def test_full_and_half_siblings_are_told_apart(db, tree):
    sibs = {s.ref: s for s in fam.siblings_of(db, CID, tree["kid"])}
    assert sibs[tree["sis"]].kind == "full" and set(sibs[tree["sis"]].shared_parents) == {tree["dad"], tree["mum"]}
    assert sibs[tree["half"]].kind == "half" and sibs[tree["half"]].shared_parents == (tree["dad"],)
    assert tree["kid"] not in sibs
    assert fam.full_siblings_of(db, CID, tree["kid"]) == [tree["sis"]]
    assert set(fam.half_siblings_of(db, CID, tree["kid"])) == {tree["half"], tree["solo"]}


def test_shared_parents(db, tree):
    assert set(fam.shared_parents(db, CID, tree["kid"], tree["sis"])) == {tree["dad"], tree["mum"]}
    assert fam.shared_parents(db, CID, tree["kid"], tree["half"]) == [tree["dad"]]
    assert fam.shared_parents(db, CID, tree["kid"], tree["cousin"]) == []
    assert fam.shared_parents(db, CID, tree["kid"], tree["kid"]) == []


def test_missing_parents_are_handled_safely(db):
    orphan = adult(db)
    assert fam.siblings_of(db, CID, orphan) == [] and fam.cousins_of(db, CID, orphan) == []
    assert fam.siblings_of(db, CID, "npc_does_not_exist") == []
    f = fam.family_of(db, CID, "npc_does_not_exist")
    assert f.parents == [] and f.children == [] and f.cousins == []


def test_a_parent_who_was_deleted_just_drops_out(db, tree):
    delete_npc(db, CID, tree["mum"])
    assert fam.siblings_of(db, CID, tree["kid"])        # still siblings through dad
    assert tree["mum"] not in fam.family_of(db, CID, tree["kid"]).parents


# ---------- cousins, ancestors, descendants, relations ----------

def test_cousins_are_children_of_a_parents_siblings(db, tree):
    cousins = {c.ref: c for c in fam.cousins_of(db, CID, tree["kid"])}
    assert set(cousins) == {tree["cousin"]}
    assert set(cousins[tree["cousin"]].shared_grandparents) == {tree["gran1"], tree["gran2"]}
    assert [c.ref for c in fam.cousins_of(db, CID, tree["cousin"])] == [tree["kid"], tree["sis"]]


def test_cousins_need_known_grandparents(db):
    a, b = adult(db), adult(db)
    k1, k2 = child_of(db, a), child_of(db, b)
    assert fam.cousins_of(db, CID, k1) == []


def test_ancestors_and_descendants(db, tree):
    assert set(rel.ancestors_of(db, CID, tree["kid"])) == {tree["dad"], tree["mum"], tree["gran1"], tree["gran2"]}
    assert set(rel.descendants_of(db, CID, tree["gran1"])) == {tree["mum"], tree["uncle"], tree["kid"], tree["sis"], tree["cousin"]}


def test_relations_between_names_every_relation_that_holds(db, tree):
    r = lambda a, b: fam.relations_between(db, CID, tree[a], tree[b])
    assert r("kid", "dad") == ["parent", "ancestor"]
    assert r("dad", "kid") == ["child", "descendant"]
    assert r("kid", "sis") == ["sibling"] and r("kid", "half") == ["half_sibling"]
    assert r("kid", "cousin") == ["cousin"] and r("kid", "gran1") == ["ancestor"]
    assert r("kid", "other") == [] and fam.relations_between(db, CID, tree["kid"], tree["kid"]) == []


def test_family_of_bundles_everything(db, tree):
    f = fam.family_of(db, CID, tree["kid"])
    assert set(f.parents) == {tree["dad"], tree["mum"]} and f.children == []
    assert {s.ref for s in f.siblings} == {tree["sis"], tree["half"], tree["solo"]}
    assert set(f.grandparents) == {tree["gran1"], tree["gran2"]} and [c.ref for c in f.cousins] == [tree["cousin"]]


def test_family_queries_use_a_bounded_number_of_queries(db, tree):
    from sqlalchemy import event
    count = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda *a: count.append(1))
    fam.family_of(db, CID, tree["kid"])
    small = len(count)
    for _ in range(12):                                 # a much bigger family must not cost more queries
        child_of(db, tree["dad"])
    count.clear()
    fam.family_of(db, CID, tree["kid"])
    assert len(count) == small


# ---------- invalid relationships are refused ----------

def test_self_parentage_is_refused(db):
    a = adult(db)
    with pytest.raises(rel.SelfRelationshipError):
        rel.add_relationship(db, CID, "parent", a, a)


def test_circular_ancestry_is_refused(db, tree):
    with pytest.raises(rel.CircularParentageError):
        rel.add_relationship(db, CID, "parent", tree["kid"], tree["gran1"])
    with pytest.raises(rel.CircularParentageError):
        rel.add_relationship(db, CID, "parent", tree["kid"], tree["dad"])        # dad has a free parent slot


def test_duplicate_parent_links_are_one_link(db):
    p, c = adult(db), adult(db)
    a = rel.add_relationship(db, CID, "parent", p, c)
    b = rel.add_relationship(db, CID, "parent", p, c)
    assert a.id == b.id
    assert len(db.exec(select(CharacterRelationship).where(CharacterRelationship.kind == "parent")).all()) == 1


def test_a_third_parent_is_refused(db, tree):
    with pytest.raises(rel.TooManyParentsError):
        rel.add_relationship(db, CID, "parent", tree["aunt"], tree["kid"])


# ---------- siblings and cousins are identified, never prohibited by default ----------

def test_siblings_can_have_a_child_unless_a_rule_forbids_it(db, tree):
    a, b = adult(db), adult(db)
    s1, s2 = _adult_child(db, a, b), _adult_child(db, a, b)
    d = create_descendant(db, CID, [s1, s2], seed=3)
    assert d.created and d.parent_relations == ["sibling"]


def test_a_game_rule_can_forbid_a_relation(db):
    a, b = adult(db), adult(db)
    s1, s2 = _adult_child(db, a, b), _adult_child(db, a, b)
    rules = get_inheritance_rules().model_copy(deep=True)
    rules.parents.forbidden_relations = ["sibling"]
    with pytest.raises(ParentNotEligibleError, match="sibling"):
        create_descendant(db, CID, [s1, s2], seed=3, rules=rules)


def test_cousins_are_identified_and_allowed(db):
    g1, g2 = adult(db), adult(db)
    p1, p2 = _adult_child(db, g1, g2), _adult_child(db, g1, g2)
    c1, c2 = _adult_child(db, p1), _adult_child(db, p2)
    d = create_descendant(db, CID, [c1, c2], seed=4)
    assert d.created and d.parent_relations == ["cousin"]


def _adult_child(db, *parents):
    n = person(age=25, seed=next(_seeds))
    save_npc(db, CID, n)
    rel.add_parent(db, CID, n.id, *parents)
    return n.id


# ---------- whole-tree validation ----------

def test_a_healthy_tree_has_no_issues(db, tree):
    assert fam.validate_family(db, CID) == []


def test_validation_finds_cycles_too_many_parents_and_dangling_links(db, tree):
    def raw(kind_source, target):
        db.add(CharacterRelationship(campaign_id=CID, kind="parent", source_id=kind_source, target_id=target))
    raw(tree["kid"], tree["gran1"])                       # kid -> gran1 closes a loop (written around the checks)
    raw(tree["aunt"], tree["kid"])                        # a third parent
    raw("npc_ghost", tree["sis"])                         # a parent who does not exist
    db.flush()
    kinds = {i.kind for i in fam.validate_family(db, CID)}
    assert {"circular_ancestry", "too_many_parents", "dangling_link"} <= kinds


def test_validation_survives_a_save_and_reload(tmp_path):
    from sqlmodel import create_engine
    from app.db import init_db
    from app.models import Campaign
    url = f"sqlite:///{tmp_path / 'g.db'}"
    eng = create_engine(url)
    init_db(eng)
    with Session(eng) as s:
        s.add(Campaign(name="x", seed=1))
        s.commit()
        a, b = adult(s), adult(s)
        kid = child_of(s, a, b)
        s.commit()
    eng2 = create_engine(url)
    with Session(eng2) as s:
        assert fam.validate_family(s, CID) == []
        assert set(rel.parents_of(s, CID, kid)) == {a, b}
