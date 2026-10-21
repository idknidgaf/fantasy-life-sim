"""Events must make sense for the life stage the character is actually in."""
from app.content import get_content
from app.rules import stage_at
from tests.helpers import make, play_life

CT = get_content()
SPECIES = list(CT.species)


def test_logged_events_always_fit_their_conditions():
    """Across many lives of every species, each event happened at an appropriate stage and for
    an appropriate kind of being."""
    checked = 0
    for species in SPECIES:
        sp = CT.species[species]
        for seed in range(25):
            c = play_life(seed, species, talent_chance=0)
            for h in c.history:
                if h["kind"] != "event":
                    continue
                cond = CT.events[h["event_id"]].conditions
                stage = stage_at(sp, h["age"], CT)
                idx = CT.stage_ids.index(stage.id)
                if cond.min_stage:
                    assert idx >= CT.stage_ids.index(cond.min_stage), (species, h)
                if cond.max_stage:
                    assert idx <= CT.stage_ids.index(cond.max_stage), (species, h)
                if cond.species_categories:
                    assert sp.category in cond.species_categories, (species, h)
                if cond.species_tags:
                    assert all(t in sp.tags for t in cond.species_tags), (species, h)
                checked += 1
    assert checked > 500


def test_the_age_three_fever_bug_is_fixed():
    """A toddler must never be asked whether to tend the sick."""
    for seed in range(300):
        c = play_life(seed, "human", talent_chance=0)
        for h in c.history:
            if h.get("event_id") in ("fever_outbreak", "fever_child"):
                assert stage_at(CT.species["human"], h["age"], CT).id not in ("infancy", "early_childhood"), h
            if h.get("event_id") == "fever_outbreak":
                assert h["age"] >= 18


def test_infants_only_get_passive_events():
    """Infants cannot decide anything: their events have a single, instinctive choice."""
    infant_only = [e for e in CT.events.values() if e.conditions.max_stage == "infancy"]
    assert len(infant_only) >= 4
    for e in infant_only:
        assert len(e.choices) == 1, e.id


def test_infancy_offers_no_actions_but_childhood_does():
    from app import engine as sim
    assert sim.offer_actions(make("human", age=1), 1) == []
    assert [a["id"] for a in sim.offer_actions(make("human", age=4), 1)] == ["play"]
    ids = [a["id"] for a in sim.offer_actions(make("human", age=8), 1)]
    assert any(i.startswith("train:") for i in ids) and "rest" in ids and "work" not in ids
    adult = [a["id"] for a in sim.offer_actions(make("human", age=25), 1)]
    assert "work" in adult and any(i.startswith("train_stat:") for i in adult)


def test_children_only_train_age_appropriate_categories():
    from app.rules import trainable_skills
    for seed in range(20):
        kid = make("human", seed=seed, age=8)
        assert {CT.skills[s] for s in trainable_skills(kid, CT)} <= {"survival", "mental", "social", "crafting"}


def test_talent_path_events_are_never_random():
    from app import engine as sim
    c = make("human", age=30)
    for seed in range(200):
        assert sim._select_event(c, CT, __import__("random").Random(seed)) not in (
            "master_of_none_path", "divine_favor_path", "worlds_adversary_path", "primordial_origin_path")


def test_once_events_never_repeat():
    once = {e.id for e in CT.events.values() if e.once}
    for seed in range(60):
        c = play_life(seed, "human", talent_chance=0)
        ids = [h["event_id"] for h in c.history if h.get("event_id") in once]
        assert len(ids) == len(set(ids))


def test_locked_choices_report_why():
    from app import engine as sim
    elf = make("elf", age=30, location="ancient_forest", stats={"constitution": 30, "dexterity": 30, "wisdom": 10,
                                                                 "strength": 30, "charisma": 30})
    elf.pending_event = "forest_ancient_altar"
    state = {o["id"]: o for o in sim.event_choice_state(elf, CT, "forest_ancient_altar")}
    assert not state["study"]["available"] and state["study"]["requirement"] == "Requires Wisdom 20+"
    assert not state["offer"]["available"] and "valuable offering" in state["offer"]["requirement"]
    assert state["leave"]["available"] and state["leave"]["requirement"] is None
    try:
        sim.resolve_event(elf, 1, "forest_ancient_altar", "study")
    except sim.NotAllowed as e:
        assert "Wisdom" in str(e)
    else:
        raise AssertionError("locked choice was accepted")


def test_altar_offer_consumes_the_item():
    from app import engine as sim
    c = make("elf", age=30, location="ancient_forest", items={"silver_locket": 1})
    c.pending_event = "forest_ancient_altar"
    sim.resolve_event(c, 1, "forest_ancient_altar", "offer")
    assert "silver_locket" not in c.items and c.items.get("ancient_relic") == 1
    assert "altar_offering_made" in c.flags


def test_events_chain_and_second_wind():
    from app import engine as sim
    c = make("human", age=20, traits=["second_wind"])
    wins = 0
    for seed in range(300):
        t = make("human", seed=seed, age=20, traits=["second_wind"])
        t.stats["charisma"] = 1                           # near-certain failure without Second Wind
        t.pending_event = "village_bully"
        sim.resolve_event(t, seed, "village_bully", "talk")
        wins += any("Second Wind" in h["text"] for h in t.history)
    assert wins > 0


def test_newborns_do_not_speak():
    for seed in range(300):
        c = play_life(seed, "human", talent_chance=0)
        first = [h for h in c.history if h.get("event_id") == "infant_first_words"]
        assert all(h["age"] >= 1 for h in first)
