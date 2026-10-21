"""Combat: the resolver's maths, defeat severity, warnings, fleeing, how a fight is applied (once, atomically),
the battle summary, and the API / frontend contract. Power itself is tested in test_power.py and only used here."""
import math
import random
import re
from fractions import Fraction
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app import battle, battle_store, combat
from app import engine as sim
from app.battle import BattleSummary, CombatPreview
from app.combat import (
    CombatConfig, defeat_severity, effective_power, flee_chance, load_combat_config, percent_floor,
    warning_level, win_probability,
)
from app.content import ContentError, get_content
from app.models import CharacterRecord, CombatRecord
from app.power import calculate_power

from .helpers import make
from .test_api import client, new_game  # noqa: F401  (fixture)

CT = get_content()
FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def sev(ratio, loser=100.0):
    """Defeat severity for a winner `ratio` times as strong as the loser."""
    return defeat_severity(loser, loser * ratio)


# ---------------------------------------------------------------- normalisation, zero and extreme Power

def test_effective_power_floors_zero_negative_nan_and_caps_infinity():
    assert effective_power(0) == 1.0 and effective_power(-5) == 1.0 and effective_power(float("nan")) == 1.0
    assert effective_power(0.4) == 1.0 and effective_power(7.5) == 7.5
    assert effective_power(float("inf")) > 1e300 and math.isfinite(effective_power(float("inf")))


def test_zero_power_on_both_sides_is_an_even_match_not_a_division_by_zero():
    assert win_probability(0, 0) == 0.5
    assert flee_chance(0, 0) == 0.5
    assert sev(1.0, loser=0).band == "minor"


def test_win_probability_follows_relative_power_and_mirrors_exactly():
    assert win_probability(100, 100) == 0.5
    assert win_probability(200, 100) > 0.5 > win_probability(100, 200)
    assert win_probability(100, 200) < win_probability(100, 150) < win_probability(100, 100)
    assert win_probability(300, 100) == pytest.approx(0.75)
    for a, b in [(10, 3), (1000, 100), (0, 50), (7, 7)]:
        assert win_probability(a, b) + win_probability(b, a) == pytest.approx(1.0)


def test_only_the_ratio_matters_so_it_suits_uncapped_power():
    assert win_probability(30, 10) == pytest.approx(win_probability(3e9, 1e9))
    assert win_probability(3e250, 1e250) == pytest.approx(0.75)


def test_the_weaker_side_always_keeps_a_small_nonzero_chance_and_the_stronger_is_never_certain():
    for weak, strong in [(1, 1e6), (0, 1e12), (1, 1e300), (1, float("inf"))]:
        assert 0 < win_probability(weak, strong) <= 0.01 + 1e-12
        assert 0.99 - 1e-12 <= win_probability(strong, weak) < 1


def test_extreme_power_does_not_overflow():
    assert win_probability(1e308, 1e-300) == 0.99
    assert win_probability(float("inf"), float("inf")) == 0.5
    assert win_probability(1e308, 1.0) == 0.99


def test_combat_config_validation(tmp_path):
    bad = tmp_path / "combat.yaml"
    bad.write_text("win_chance_flor: 0.1\n")
    with pytest.raises(ContentError):
        load_combat_config(bad)
    bad.write_text("flee: {min_chance: 0.9, max_chance: 0.1}\n")
    with pytest.raises(ContentError):
        load_combat_config(bad)
    assert load_combat_config(tmp_path / "missing.yaml") == CombatConfig()


# ---------------------------------------------------------------- defeat severity

@pytest.mark.parametrize("ratio, band, lo, hi, dies", [
    (0.5, "minor", 1, 1, False),          # the loser was actually the stronger side (an upset)
    (1.0, "minor", 1, 1, False),
    (1.49, "minor", 1, 1, False),
    (1.5, "minor", 5, 5, False),
    (1.99, "minor", 5, 5, False),
    (2.0, "minor", 5, 10, False),
    (2.99, "minor", 5, 10, False),
    (3.0, "heavy", 50, 50, False),         # exact boundary belongs to the band that starts there
    (3.5, "heavy", 50, 50, False),
    (3.899, "heavy", 50, 50, False),
    (3.9, "severe", 90, 90, False),        # exactly 3.9 is compared exactly, not as 3.8999999
    (3.901, "severe", 90, 90, False),
    (9.99, "severe", 90, 90, False),
    (10.0, "lethal", 100, 100, True),
    (1e6, "lethal", 100, 100, True),
])
def test_defeat_severity_bands_and_exact_boundaries(ratio, band, lo, hi, dies):
    s = sev(ratio)
    assert (s.band, s.min_percent, s.max_percent, s.instant_death) == (band, lo, hi, dies)


def test_boundaries_hold_with_awkward_floats():
    # 0.1 + 0.2 style rounding must not move a ratio across a boundary
    assert defeat_severity(10, 39).band == "severe"
    assert defeat_severity(10, 30).band == "heavy"
    assert defeat_severity(3, 30).band == "lethal"
    assert defeat_severity(0, 10).band == "lethal"          # floor 1 -> ratio 10
    assert defeat_severity(0, 9.999).band == "severe"


def test_the_random_minor_band_is_always_a_whole_5_to_10_percent():
    s, rng = sev(2.5), random.Random(1)
    seen = {s.roll_percent(rng) for _ in range(400)}
    assert seen == {5, 6, 7, 8, 9, 10}
    assert sev(1.0).roll_percent(rng) == 1 and sev(1.7).roll_percent(rng) == 5 and sev(4).roll_percent(rng) == 90


def test_severity_does_not_depend_on_win_probability_and_labels_are_readable():
    assert sev(3).label == "Estimated defeat: -50% HP"
    assert sev(2.5).label == "Estimated defeat: -5% to -10% HP"
    assert "instant death" in sev(10).label
    assert combat.damage_hp(50, 1) == 1 and combat.damage_hp(50, 90) == 45 and combat.damage_hp(55, 5) == 3
    assert combat.damage_hp(50, 0) == 0
    assert percent_floor(0.4999) == 49 and percent_floor(0.5) == 50 and percent_floor(0.99) == 99


# ---------------------------------------------------------------- warnings

def test_warning_thresholds_and_precedence():
    assert warning_level(0.5, sev(1)) == "none"                      # exactly 50% is not below 50%
    assert warning_level(0.499, sev(1)) == "caution"                 # yellow: under 50%
    assert warning_level(0.9, sev(3)) == "danger"                    # red: defeat costs 50% or more ...
    assert warning_level(0.9, sev(10)) == "danger"                   # ... even when the odds look good
    assert warning_level(0.1, sev(3)) == "danger"                    # red beats yellow
    assert warning_level(0.1, sev(2.99)) == "caution"
    assert combat.warning_text("none", sev(1)) is None
    for level, s in [("caution", sev(1)), ("danger", sev(3)), ("danger", sev(10))]:
        assert combat.warning_text(level, s)                          # never colour alone


# ---------------------------------------------------------------- fleeing chance

def test_flee_chance_falls_as_the_opponent_gets_stronger_and_is_never_impossible_or_certain():
    chances = [flee_chance(100, p) for p in (50, 100, 200, 1000, 1e9)]
    assert chances == sorted(chances, reverse=True) and chances[0] > chances[-1]
    assert flee_chance(1, 1e12) == 0.05 and flee_chance(1e12, 1) == 0.95


def test_flee_chance_is_configurable_and_takes_encounter_context():
    easy = CombatConfig.model_validate({"flee": {"bias": 3.0}})
    assert flee_chance(100, 100, easy) > flee_chance(100, 100)
    assert flee_chance(100, 100, modifier=0.2) == pytest.approx(0.7)
    assert flee_chance(100, 100, modifier=-0.2) == pytest.approx(0.3)
    assert flee_chance(100, 100, modifier=5) == 0.95


# ---------------------------------------------------------------- the resolver on real characters

def fighter(**skills):
    return make("human", seed=1, skills=dict(skills))


def combat_def(event="village_bully", choice="fight"):
    return next(c for c in CT.events[event].choices if c.id == choice).combat


def test_resolver_uses_the_power_system_for_both_combatants():
    c = fighter(martial_arts=30)
    m = battle.matchup(c, CT, combat_def())
    assert m.player_power == calculate_power(c, CT).total == 30
    assert m.opponent_power == calculate_power(
        sim.Character(name="x", skills={"martial_arts": 1}), CT).total == 1
    assert m.win_probability == win_probability(30, 1)
    assert m.defeat.band == "minor"               # the player is the stronger side if beaten
    assert m.flee_chance == flee_chance(30, 1)


def test_preview_carries_the_resolvers_numbers_and_warning():
    weak, strong = fighter(), fighter(martial_arts=500)
    p = battle.preview(battle.matchup(weak, CT, combat_def("wolves_circle" if "wolves_circle" in CT.events else "village_bully")))
    assert isinstance(p, CombatPreview) and p.flee_available and p.flee_percent is not None
    p2 = battle.preview(battle.matchup(strong, CT, combat_def()))
    assert p2.warning == "none" and p2.win_percent == 99 and p2.defeat.label.startswith("Estimated defeat")


def with_opponent(monkeypatch, event, choice, **skills):
    monkeypatch.setattr(combat_def(event, choice).opponent, "skills", dict(skills))


def test_encounter_options_show_fight_and_flee_with_the_same_preview(monkeypatch):
    with_opponent(monkeypatch, "village_bully", "fight", martial_arts=400)
    c = fighter(martial_arts=100)                         # ratio 4 -> 90% defeat -> red
    opts = sim.event_choice_state(c, CT, "village_bully")
    kinds = [o["kind"] for o in opts]
    assert kinds[:2] == ["fight", "flee"] and "normal" in kinds[2:]
    fight, flee = opts[0], opts[1]
    assert fight["combat"] == flee["combat"] and flee["id"] == "fight:flee"
    assert fight["combat"]["warning"] == "danger" and fight["combat"]["win_percent"] == 20
    assert fight["combat"]["defeat"]["label"] == "Estimated defeat: -90% HP"
    assert all(o["combat"] is None for o in opts[2:])


def test_flee_can_be_disabled_by_an_encounter(monkeypatch):
    monkeypatch.setattr(combat_def("village_bully", "fight").flee, "allowed", False)
    c = fighter()
    assert [o["kind"] for o in sim.event_choice_state(c, CT, "village_bully")].count("flee") == 0
    c.pending_event = "village_bully"
    with pytest.raises(sim.InvalidChoice):
        sim.resolve_event(c, 1, "village_bully", "fight:flee")


def find_seed(setup, wanted, limit=2000, choice="fight", event="village_bully"):
    """Smallest seed for which resolving `choice` gives `wanted(summary)` (the dice are deterministic)."""
    for seed in range(limit):
        c = setup()
        c.pending_event = event
        s = sim.resolve_event(c, seed, event, choice)
        if wanted(s):
            return seed
    raise AssertionError("no seed found")


def resolve(setup, seed, choice="fight", event="village_bully"):
    c = setup()
    c.pending_event = event
    return c, sim.resolve_event(c, seed, event, choice)


def test_a_victory_grants_the_rewards_and_no_damage():
    setup = lambda: fighter(martial_arts=500)
    seed = find_seed(setup, lambda s: s.outcome == "victory")
    c, s = resolve(setup, seed)
    assert s.outcome == "victory" and s.damage is None and not s.died
    texts = [r.text for r in s.rewards]
    assert "+1 Strength" in texts and any("Martial Arts" in t for t in texts)
    assert s.other_consequences == [] or all(o.kind != "stat" for o in s.other_consequences)
    assert c.health == sim.max_health(c, CT)
    assert s.power.player == 500 and s.power.opponent == 1
    assert s.estimated_win_percent == 99 and s.action == "fight" and s.flee is None
    assert c.pending_event is None


def test_rewards_listed_are_only_what_was_really_granted():
    """A stat already at its cap gains nothing, so the summary must not claim it."""
    def setup():
        c = fighter(martial_arts=500)
        c.stats["strength"] = CT.species[c.species_id].stat_cap
        return c
    seed = find_seed(setup, lambda s: s.outcome == "victory")
    before = setup()
    c, s = resolve(setup, seed)
    assert c.stats["strength"] == before.stats["strength"]
    assert "+1 Strength" not in [r.text for r in s.rewards]


def test_a_defeat_applies_percent_damage_once_and_no_rewards(monkeypatch):
    with_opponent(monkeypatch, "village_bully", "fight", martial_arts=400)       # ratio 4 -> -90%
    setup = lambda: fighter(martial_arts=100)
    seed = find_seed(setup, lambda s: s.outcome == "defeat")
    c, s = resolve(setup, seed)
    mx = sim.max_health(c, CT)
    assert s.damage.percent == 90 and s.damage.max_health == mx
    assert s.damage.health_lost == math.ceil(mx * 0.9) == mx - c.health
    assert s.damage.health_before == mx and s.damage.health_after == c.health and c.alive and not s.died
    assert "+1 Strength" not in [r.text for r in s.rewards]
    assert not any(r.kind == "stat" for r in s.rewards)
    # the defeat's own effects (not damage) still apply, and nothing else hurt the player
    assert sum(1 for h in c.history if h["kind"] == "event") == 1


def test_a_tenfold_gap_is_instant_death_through_the_life_cycle(monkeypatch):
    with_opponent(monkeypatch, "village_bully", "fight", martial_arts=1000)
    setup = lambda: fighter(martial_arts=100)                                    # ratio 10
    seed = find_seed(setup, lambda s: s.outcome == "defeat")
    c, s = resolve(setup, seed)
    assert s.died and s.damage.instant_death and s.damage.percent == 100
    assert c.alive is False and c.health == 0 and c.cause_of_death == "a beating behind the granary"
    assert s.cause_of_death == c.cause_of_death and s.rewards == []
    deaths = [h for h in c.history if h["kind"] == "death"]
    assert len(deaths) == 1 and c.cause_of_death in deaths[0]["text"]
    with pytest.raises(sim.NotAllowed):
        sim.resolve_event(c, 1, "village_bully", "fight")


def test_just_under_tenfold_is_survivable(monkeypatch):
    with_opponent(monkeypatch, "village_bully", "fight", martial_arts=999)
    setup = lambda: fighter(martial_arts=100)
    c, s = resolve(setup, find_seed(setup, lambda s: s.outcome == "defeat"))
    assert c.alive and s.damage.percent == 90 and not s.damage.instant_death


def test_a_weaker_player_can_win(monkeypatch):
    with_opponent(monkeypatch, "village_bully", "fight", martial_arts=100000)
    setup = lambda: fighter()
    assert win_probability(0, 100000) > 0
    assert find_seed(setup, lambda s: s.outcome == "victory", limit=20000) >= 0


def test_a_successful_flee_ends_the_encounter_with_no_rewards_and_no_damage():
    setup = lambda: fighter(martial_arts=100)
    seed = find_seed(setup, lambda s: s.outcome == "fled", choice="fight:flee")
    c, s = resolve(setup, seed, "fight:flee")
    assert (s.outcome, s.action) == ("fled", "flee") and s.flee.succeeded and s.flee.chance > 0
    assert s.damage is None and s.rewards == [] and s.other_consequences == [] and not s.died
    assert c.health == sim.max_health(c, CT) and c.pending_event is None
    assert c.skills == {"martial_arts": 100} and c.items == {} and c.stats == setup().stats


def test_a_failed_flee_resolves_as_a_normal_fight_without_extra_penalty(monkeypatch):
    with_opponent(monkeypatch, "village_bully", "fight", martial_arts=400)
    setup = lambda: fighter(martial_arts=100)
    seed = find_seed(setup, lambda s: s.action == "flee" and s.outcome == "defeat", choice="fight:flee")
    c, s = resolve(setup, seed, "fight:flee")
    assert s.flee is not None and not s.flee.succeeded and s.outcome == "defeat"
    mx = sim.max_health(c, CT)
    assert s.damage.percent == 90 and mx - c.health == math.ceil(mx * 0.9)      # exactly the normal defeat damage
    # and a failed flee that then wins is an ordinary victory, with its rewards
    seed = find_seed(setup, lambda s: s.action == "flee" and s.outcome == "victory", choice="fight:flee")
    _, s2 = resolve(setup, seed, "fight:flee")
    assert s2.flee and not s2.flee.succeeded and s2.damage is None and s2.rewards


def test_every_combat_choice_in_the_content_is_a_valid_encounter():
    fights = [(e.id, ch.id) for e in CT.events.values() for ch in e.choices if ch.combat]
    assert len(fights) >= 5
    for event_id, choice_id in fights:
        c = fighter(martial_arts=10, swordsmanship=10)
        opts = {o["id"]: o for o in sim.event_choice_state(c, CT, event_id)}
        assert opts[choice_id]["kind"] == "fight"
        if f"{choice_id}:flee" in opts:
            assert opts[f"{choice_id}:flee"]["kind"] == "flee"


def test_combat_effects_may_not_deal_damage_or_kill_twice():
    from app.content import _check_combat, ChoiceDef  # noqa: F401
    ch = next(c for c in CT.events["village_bully"].choices if c.id == "fight").model_copy(deep=True)
    ch.combat.defeat.effects.append({"die": {"cause": "x"}})
    errs: list[str] = []
    _check_combat(ch, "where", CT, None, errs)
    assert any("nothing is applied twice" in e for e in errs)


def test_legacy_checks_are_untouched():
    c = make("human", seed=1)
    c.pending_event = "village_bully"
    assert sim.resolve_event(c, 1, "village_bully", "talk") is None
    assert c.pending_event is None


def test_whole_lives_still_run_with_combat(monkeypatch):
    from .helpers import play_life
    for seed in range(15):
        assert play_life(seed).alive is False


# ---------------------------------------------------------------- API: summary, retries, queue, save/load

def _set(client, **fields):
    with Session(client.engine) as s:
        rec = s.exec(select(CharacterRecord)).first()
        for k, v in fields.items():
            setattr(rec, k, v)
        s.add(rec)
        s.commit()


def _rec(client):
    with Session(client.engine) as s:
        return s.exec(select(CharacterRecord)).first()


def setup_fight(client, skills=None, queued=("stray_dog",), **fields):
    new_game(client)
    _set(client, pending_event="village_bully", queued_events=list(queued), skills=skills or {"martial_arts": 500},
         age=10, **fields)


def test_api_offers_fight_and_flee_with_the_preview(client):
    setup_fight(client)
    ev = client.get("/api/character").json()["pending_event"]
    kinds = [c["kind"] for c in ev["choices"]]
    assert kinds[:2] == ["fight", "flee"]
    fight = ev["choices"][0]
    assert fight["combat"]["win_percent"] == 99 and fight["combat"]["player_power"] == 500
    assert fight["combat"]["warning"] == "none"


def test_resolving_returns_a_stored_summary_and_keeps_the_queue(client):
    setup_fight(client)
    r = client.post("/api/events/village_bully/choices/fight")
    assert r.status_code == 200
    ch = r.json()
    s = ch["battle_summary"]
    assert s["id"] and s["event_id"] == "village_bully" and s["opponent_name"] == "The village bully"
    assert s["outcome"] in ("victory", "defeat") and s["power"] == {"player": 500, "opponent": 1}
    assert ch["pending_event"]["id"] == "stray_dog"                  # the queue moved on and nothing was lost
    # reload: the same summary, not a recalculation
    again = client.get("/api/character").json()
    assert again["battle_summary"] == s and again["pending_event"]["id"] == "stray_dog"
    with Session(client.engine) as session:
        rows = session.exec(select(CombatRecord)).all()
        assert len(rows) == 1 and rows[0].summary == s and not rows[0].acknowledged


def test_retrying_a_resolved_fight_changes_nothing_and_stores_nothing_twice(client):
    setup_fight(client, queued=())
    first = client.post("/api/events/village_bully/choices/fight").json()
    snapshot = _rec(client)
    for choice in ("fight", "fight:flee"):
        r = client.post(f"/api/events/village_bully/choices/{choice}")
        assert r.status_code == 409
    after = _rec(client)
    assert (after.health, after.skills, after.stats, after.items, after.history) == (
        snapshot.health, snapshot.skills, snapshot.stats, snapshot.items, snapshot.history)
    with Session(client.engine) as session:
        assert len(session.exec(select(CombatRecord)).all()) == 1
    assert client.get("/api/character").json()["battle_summary"] == first["battle_summary"]


def test_flee_attempts_and_outcomes_are_persisted(client):
    setup_fight(client, queued=())
    ch = client.post("/api/events/village_bully/choices/fight:flee").json()
    s = ch["battle_summary"]
    assert s["action"] == "flee" and s["flee"]["chance"] > 0
    with Session(client.engine) as session:
        row = session.exec(select(CombatRecord)).one()
        assert row.flee_attempted is True and row.flee_succeeded == s["flee"]["succeeded"]
        assert row.outcome == s["outcome"] and row.choice_id == "fight:flee"


def test_dismissing_the_summary_resumes_play_and_is_idempotent(client):
    setup_fight(client)
    s = client.post("/api/events/village_bully/choices/fight").json()["battle_summary"]
    r = client.post(f"/api/battles/{s['id']}/acknowledge")
    assert r.status_code == 200
    ch = r.json()
    assert ch["battle_summary"] is None and ch["pending_event"]["id"] == "stray_dog"
    assert client.post(f"/api/battles/{s['id']}/acknowledge").status_code == 200
    assert client.get("/api/character").json()["battle_summary"] is None
    assert client.post("/api/battles/9999/acknowledge").status_code == 404


def test_acting_on_closes_a_stale_summary(client):
    setup_fight(client, queued=())
    client.post("/api/events/village_bully/choices/fight")
    ch = client.post("/api/turn/advance").json()
    assert ch["battle_summary"] is None


def test_a_non_combat_choice_has_no_summary(client):
    setup_fight(client, queued=())
    ch = client.post("/api/events/village_bully/choices/run").json()
    assert ch["battle_summary"] is None
    with Session(client.engine) as session:
        assert session.exec(select(CombatRecord)).all() == []


def test_death_in_battle_is_saved_and_summarised(client, monkeypatch):
    monkeypatch.setattr(combat_def("village_bully", "fight").opponent, "skills", {"martial_arts": 100000})
    setup_fight(client, skills={"martial_arts": 1}, queued=())
    for seed in range(1, 500):
        _set(client, pending_event="village_bully", alive=True, health=None, cause_of_death=None)
        # the dice come from the campaign seed and the age, so vary the age instead
        _set(client, age=seed % 90 + 1)
        ch = client.post("/api/events/village_bully/choices/fight").json()
        if ch["battle_summary"]["outcome"] == "defeat":
            break
    s = ch["battle_summary"]
    assert s["died"] and not ch["alive"] and ch["cause_of_death"] == "a beating behind the granary"
    assert s["damage"]["instant_death"] and s["rewards"] == []
    assert _rec(client).alive is False


def test_deleting_a_campaign_removes_its_battle_records(client):
    setup_fight(client)
    client.post("/api/events/village_bully/choices/fight")
    cid = client.get("/api/campaign").json()["id"]
    assert client.delete(f"/api/campaigns/{cid}").status_code == 204
    with Session(client.engine) as session:
        assert session.exec(select(CombatRecord)).all() == []


# ---------------------------------------------------------------- the frontend contract

def _ts_fields(source: str, name: str) -> set[str]:
    m = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.S)
    assert m, f"interface {name} missing from frontend/src/types.ts"
    return set(re.findall(r"^\s+(\w+)\??:", m.group(1), re.M))


@pytest.mark.parametrize("name, model", [
    ("BattleSummary", battle.BattleSummary), ("CombatPreview", battle.CombatPreview),
    ("DefeatEstimate", battle.DefeatEstimate), ("BattleLine", battle.BattleLine),
    ("BattlePower", battle.BattlePower), ("FleeResult", battle.FleeResult), ("BattleDamage", battle.BattleDamage)])
def test_frontend_types_match_the_backend_battle_models(name, model):
    ts = (FRONTEND / "types.ts").read_text(encoding="utf-8")
    assert _ts_fields(ts, name) == set(model.model_fields)


def _code(path: str) -> str:
    return re.sub(r"/\*.*?\*/|//.*", "", (FRONTEND / path).read_text(encoding="utf-8"), flags=re.S)


def test_the_summary_component_calculates_nothing():
    body = _code("components/BattleSummary.tsx")
    assert not re.search(r"Math\.|\.reduce\(|\bpower\.\w+\s*[*/+-]|percent\s*[*/]|\*\s*100", body)


def test_the_warning_uses_backend_values_not_hardcoded_enemies():
    body = _code("components/EventPanel.tsx")
    assert "warning" in body and "warning_label" in body
    assert not re.search(r"wolf|bandit|bully|champion|\b50\b|\b0\.5\b", body, re.I)


def test_ui_text_never_relies_on_colour_alone():
    summary = _code("components/BattleSummary.tsx")
    for label in ("Victory", "Defeat", "Fled Successfully", "Flee Failed", "No rewards gained", "Continue"):
        assert label in summary
    assert "Rewards" in summary and "Consequences" in summary


def test_battle_summary_validates_round_trip():
    c = fighter(martial_arts=500)
    c.pending_event = "village_bully"
    s = sim.resolve_event(c, 1, "village_bully", "fight")
    assert BattleSummary.model_validate(s.model_dump(mode="json")) == s
