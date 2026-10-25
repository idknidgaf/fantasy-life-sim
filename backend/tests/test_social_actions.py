"""The yearly Social Action budget: 3 per in-game year, separate from the Life actions, backend-authoritative."""
import pytest
from sqlmodel import Session, create_engine, select

from app import engine as sim
from app.character import Character
from app.content import get_content
from app.db import init_db
from app.models import CharacterRecord
from tests import test_legacy as lg
from tests.test_api import client, new_game  # noqa: F401  (client is a fixture)

CT = get_content()


def char(client):
    r = client.get("/api/character")
    assert r.status_code == 200
    return r.json()


def social(client, request_id=None):
    return client.post("/api/social/start", json={"request_id": request_id} if request_id else None)


def left(client):
    return char(client)["social_remaining"]


def year(client):
    """One Age Up. The game may have rolled a birth event; clear it so only the lifecycle is under test."""
    lg.set_latest(client, pending_event=None, queued_events=[])
    r = client.post("/api/turn/advance")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def game(client):  # noqa: F811
    new_game(client)
    lg.set_latest(client, age=20, pending_event=None, queued_events=[])        # an adult: every kind of Life action exists
    return client


# ---------- the budget ----------

def test_every_new_character_starts_with_three(game):
    c = char(game)
    assert (c["social_budget"], c["social_remaining"]) == (3, 3)
    assert sim.SOCIAL_ACTIONS_PER_YEAR == 3


def test_starting_a_social_action_spends_exactly_one(game):
    for expected in (2, 1, 0):
        r = social(game)
        assert r.status_code == 200 and r.json()["social_remaining"] == expected and r.json()["social_budget"] == 3


def test_none_remaining_means_no_more_and_the_balance_never_goes_negative(game):
    for _ in range(3):
        social(game)
    for _ in range(3):
        r = social(game)
        assert r.status_code == 409 and "Social Actions" in r.json()["detail"]
    assert left(game) == 0
    with Session(game.engine) as s:
        assert s.exec(select(CharacterRecord)).first().social_actions_used == 3        # not 4, not 6


def test_a_refused_start_changes_nothing(game):
    for _ in range(3):
        social(game)
    before = char(game)
    social(game)
    assert char(game) == before


def test_a_dead_character_has_none_and_cannot_start_one(game):
    lg.kill(game)
    c = char(game)
    assert c["social_budget"] == 0 and c["social_remaining"] == 0
    assert social(game).status_code == 409


def test_an_event_waiting_for_a_decision_blocks_it_and_spends_nothing(game):
    lg.set_latest(game, pending_event=next(iter(CT.events)))
    assert social(game).status_code == 409 and left(game) == 3


# ---------- retries ----------

def test_a_retried_request_spends_only_once(game):
    assert social(game, "click-1").json()["social_remaining"] == 2
    for _ in range(3):
        r = social(game, "click-1")                        # a retry, a double delivery, a replayed request
        assert r.status_code == 200 and r.json()["social_remaining"] == 2
    assert social(game, "click-2").json()["social_remaining"] == 1


def test_a_replay_still_succeeds_after_the_budget_is_used_up(game):
    for i in range(3):
        social(game, f"c{i}")
    assert social(game, "c2").status_code == 200 and left(game) == 0
    assert social(game, "brand-new").status_code == 409


def test_request_ids_are_validated(game):
    for bad in ("", "has space", "x" * 65, "semi;colon"):
        assert social(game, bad).status_code == 422 if bad else True
    assert game.post("/api/social/start", json={"request_id": ""}).status_code == 422
    assert game.post("/api/social/start", json={"request_id": 5}).status_code == 422
    assert left(game) == 3
    assert game.post("/api/social/start").status_code == 200            # no body at all is fine


# ---------- the new year ----------

def test_age_up_refills_the_budget(game):
    for _ in range(3):
        social(game)
    assert left(game) == 0
    year(game)
    c = char(game)
    assert c["social_remaining"] == 3 and c["social_budget"] == 3
    assert social(game).json()["social_remaining"] == 2


def test_a_part_used_budget_also_resets_to_exactly_three_never_more(game):
    social(game)
    year(game)
    assert left(game) == 3
    year(game)
    assert left(game) == 3                                              # untouched years stay at three, not six


def test_every_year_gets_its_own_three(game):
    for y in range(4):
        lg.set_latest(game, pending_event=None, queued_events=[])             # the new year may have rolled an event
        for i in range(3):
            assert social(game, f"y{y}-{i}").status_code == 200
        assert social(game, f"y{y}-extra").status_code == 409
        year(game)
    assert left(game) == 3


def test_request_ids_do_not_carry_into_the_next_year(game):
    social(game, "same")
    year(game)
    assert social(game, "same").json()["social_remaining"] == 2          # a new year: the id is a new request


def test_reading_and_refused_age_ups_never_reset_or_change_it(game):
    social(game); social(game)
    for _ in range(3):
        char(game); game.get("/api/campaign"); game.get("/api/actions")
    assert left(game) == 1
    lg.set_latest(game, pending_event=next(iter(CT.events)))
    assert game.post("/api/turn/advance").status_code == 409            # a blocked age-up is not a new year
    lg.set_latest(game, pending_event=None)
    assert left(game) == 1


def test_the_reset_is_part_of_the_one_age_up_transition():
    c = Character(name="A", age=10, social_actions_used=3, social_requests=["a", "b", "c"])
    c = sim.repair(c)
    sim.advance_turn(c, seed=1)
    assert (c.age, c.social_actions_used, c.social_requests) == (11, 0, [])
    sim.advance_turn(c, seed=1)
    assert c.social_actions_used == 0                                    # an absolute reset, not an addition


# ---------- save and load ----------

def test_the_balance_survives_a_reload(game):
    social(game); social(game)
    assert left(game) == 1
    assert game.get("/api/campaign").json()["character"]["social_remaining"] == 1       # a fresh read of the saved record
    with Session(game.engine) as s:
        rec = s.exec(select(CharacterRecord)).first()
        assert rec.social_actions_used == 2
        again = CharacterRecord.from_character(rec.campaign_id, rec.to_character())     # the record <-> character round trip
        assert again.social_actions_used == 2


def test_the_balance_survives_closing_and_reopening_the_database(tmp_path):
    url = f"sqlite:///{tmp_path / 'g.db'}"
    eng = create_engine(url)
    init_db(eng)
    from app.models import Campaign
    with Session(eng) as s:
        s.add(Campaign(name="x", seed=1)); s.commit()
        c = sim.new_character("Aren", "human", 1)
        sim.start_social_action(c, "r1"); sim.start_social_action(c, "r2")
        s.add(CharacterRecord.from_character(1, c)); s.commit()
    with Session(create_engine(url)) as s:
        c = sim.repair(s.exec(select(CharacterRecord)).first().to_character())
        assert sim.social_remaining(c) == 1
        assert sim.start_social_action(c, "r2") is False and sim.social_remaining(c) == 1     # the replay is remembered
        assert sim.start_social_action(c, "r3") is True and sim.social_remaining(c) == 0


def test_a_save_from_before_social_actions_gains_a_full_budget(tmp_path):
    from tests.test_migration import make_old_save
    eng = make_old_save(tmp_path / "old.db")
    init_db(eng)
    init_db(eng)                                                         # twice is harmless
    with Session(eng) as s:
        rec = s.exec(select(CharacterRecord)).first()
        assert rec.social_actions_used == 0
        c = sim.repair(rec.to_character())
        assert sim.social_remaining(c) == 3 and c.social_requests == []


@pytest.mark.parametrize("stored, expected", [(-5, 3), (99, 0), (2, 1), (0, 3), (3, 0)])
def test_a_corrupt_stored_value_is_brought_into_range(game, stored, expected):
    lg.set_latest(game, social_actions_used=stored)
    c = char(game)
    assert c["social_remaining"] == expected and 0 <= c["social_remaining"] <= 3


def test_a_new_life_starts_with_a_full_budget(game):
    from app.descendants import create_descendant
    from app.relationships import player_ref
    lg.set_latest(game, age=60)
    with Session(game.engine) as s:
        kid = create_descendant(s, 1, [player_ref(lg.latest(s))], seed=3, age=25).npc.id
        s.commit()
    for _ in range(3):
        social(game)
    lg.kill(game)
    assert lg.cont(game, kid).status_code == 200
    assert left(game) == 3


# ---------- independent of the normal Life actions ----------

def test_life_actions_do_not_touch_the_social_budget(game):
    social(game)
    before = left(game)
    for a in game.get("/api/actions").json()[:2]:
        game.post(f"/api/actions/{a['id']}")
    assert left(game) == before


def test_social_actions_do_not_touch_the_life_actions(game):
    actions_before = game.get("/api/actions").json()
    history_before = game.get("/api/history").json()
    c0 = char(game)
    for _ in range(3):
        social(game)
    c1 = char(game)
    assert game.get("/api/actions").json() == actions_before
    for field in ("action_budget", "actions_remaining", "actions_taken_labels", "stats", "happiness", "stress"):
        assert c1[field] == c0[field]
    assert game.get("/api/history").json() == history_before                # nothing was logged either


def test_life_actions_still_work_when_social_actions_are_used_up(game):
    for _ in range(3):
        social(game)
    assert game.get("/api/actions").json()
    pick = game.get("/api/actions").json()[0]["id"]
    assert game.post(f"/api/actions/{pick}").status_code == 200


def test_social_actions_still_work_when_every_life_action_is_used_up(game):
    while char(game)["actions_remaining"] > 0:
        game.post(f"/api/actions/{game.get('/api/actions').json()[0]['id']}")
    assert game.get("/api/actions").json() == []
    assert social(game).status_code == 200 and left(game) == 2


def test_the_existing_socialize_life_action_is_still_a_life_action(game):
    offered = [a["id"] for a in game.get("/api/actions").json()]
    assert "socialize" in offered
    social_before = left(game)
    remaining = char(game)["actions_remaining"]
    assert game.post("/api/actions/socialize").status_code == 200
    assert char(game)["actions_remaining"] == remaining - 1 and left(game) == social_before


# ---------- the frontend contract ----------

def test_the_frontend_character_type_has_the_social_fields():
    from pathlib import Path
    ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "types.ts").read_text(encoding="utf-8")
    assert "social_budget: number" in ts and "social_remaining: number" in ts
