"""Lifecycle: birth, actions, the annual turn, events and talent awakening.

A year works like this:
  1. The player may perform ONE action (the stage decides which are on offer).
  2. The player advances the turn: they age, growth/aging/death resolve, a hidden talent may
     awaken or hint at itself, traits may be acquired, and an event may fire.
  3. A pending event must be resolved (pick a choice) before the next action or turn.

Everything random is seeded from (campaign seed, age, purpose), so a life is reproducible.
The hidden talent is the one thing rolled at birth, then SAVED on the character: never rerolled.
"""
from __future__ import annotations

import random

from . import battle
from .character import Character
from .content import STATS, ChoiceDef, Content, EventDef, get_content
from .rules import (
    BASE_AGE_UP_HEAL, alignment_label, apply_effects, change_happiness, change_health,
    clamp_stat, clamp_vital, conditions_met, effective_stat, gain_skill, gain_stress,
    grant_trait, has_ability, heal_bonus, max_health, migrate_skills, mortality_multiplier, pretty,
    queue_event, requirements_met, skill_aptitude, species_of, stage_of, trainable_skills,
    unmet_requirements_text,
)

TALENT_BIRTH_CHANCE = 0.01
HINT_CHANCE = 0.10
TRAIN_STAT_STRESS = 8.0
TRAIN_SKILL_STRESS = 6.0
WORK_STRESS = 6.0
DEFAULT_LABELS = {
    "play": "Play and explore",
    "work": "Take on hard work",
    "rest": "Take the time off",
    "socialize": "Socialize",
}


# Social Actions are a budget of their own: this many per in-game year for every playable character, spent and reset
# independently of the Life-action budget (the life stage's `action_count`).
SOCIAL_ACTIONS_PER_YEAR = 3


class NotAllowed(Exception):
    """The request is valid but not possible right now (HTTP 409)."""


class InvalidChoice(Exception):
    """The request names something that does not exist or is not on offer (HTTP 400)."""


def year_rng(seed: int, age: int, purpose: str) -> random.Random:
    """Fresh deterministic RNG for one purpose in one year of one life."""
    return random.Random(f"{seed}:{age}:{purpose}")


def _log(c: Character, kind: str, text: str, event_id: str | None = None) -> None:
    c.history.append({"age": c.age, "kind": kind, "text": text, "event_id": event_id})


def _bump(c: Character, ct: Content, stat: str, amount: int) -> int:
    before = c.stats[stat]
    c.stats[stat] = clamp_stat(c, ct, before + amount)
    return c.stats[stat] - before


def _die(c: Character, cause: str) -> None:
    c.alive = False
    c.cause_of_death = cause
    _log(c, "death", f"Died at age {c.age} of {cause}.")


def _check_vitals(c: Character, ct: Content) -> None:
    """Clamp health to its maximum, apply stress burnout, and kill at 0 health.

    Death is only marked here (alive=False, cause set); the caller logs it, matching how
    the 'die' effect works, so a death is never logged twice.
    """
    if not c.alive:
        return
    if c.health is None:
        c.health = max_health(c, ct)
    c.health = min(c.health, max_health(c, ct))
    if c.stress >= 100:
        harm = change_health(c, ct, -10)
        c.stress = clamp_vital(c.stress - 20)          # the breaking point brings some release
        if harm:
            _log(c, "stress", f"The pressure broke something inside you ({harm:+d} health).")
    if c.health <= 0:
        c.alive = False
        c.cause_of_death = "their wounds"


def _log_death_if_needed(c: Character) -> None:
    if not c.alive and not any(h["kind"] == "death" and h["age"] == c.age for h in c.history):
        _log(c, "death", f"Died at age {c.age} of {c.cause_of_death}.")


DEFAULT_BATTLE_CAUSE = "wounds taken in battle"


def apply_battle_damage(c: Character, ct: Content, hp: int, *, lethal: bool, cause: str) -> int:
    """Combat's one door into health and death. Health drops through the normal vitals; reaching 0, or an instant
    death (a 10x Power gap), kills exactly as the `die` effect and `_check_vitals` do: `alive` goes False and the
    cause is set. The caller logs the death (`log_death_if_needed`), so it is never logged twice. Returns the
    health actually lost."""
    before = c.health if c.health is not None else max_health(c, ct)
    change_health(c, ct, -hp)
    if lethal or c.health <= 0:
        c.health = 0
        c.alive = False
        c.cause_of_death = cause
    return before - c.health


# ---------------------------------------------------------------- birth

def roll_hidden_talent(rng: random.Random, c: Character, ct: Content, chance: float) -> str | None:
    """The one-time birth roll. Called ONLY from new_character; the result is stored on the character."""
    if rng.random() >= chance:
        return None
    pool = [t for t in ct.talents.values() if requirements_met(t.eligible, c, ct)]
    if not pool:
        return None
    return rng.choices(pool, [t.weight for t in pool])[0].id


def new_character(name: str, species_id: str, seed: int, ct: Content | None = None,
                  talent_chance: float = TALENT_BIRTH_CHANCE) -> Character:
    ct = ct or get_content()
    if species_id not in ct.species:
        raise InvalidChoice(f"Unknown species: {species_id}")
    sp = ct.species[species_id]

    # Independent random streams, so changing one system never shifts another's results.
    body = random.Random(f"{seed}:birth:body")
    stats = {s: max(1, min(sp.stat_cap, body.randint(20, 60) + sp.stat_modifiers.get(s, 0))) for s in STATS}
    axes: dict[str, int] = {}
    for axis_id, axis in ct.axes.items():
        spread = 30 if axis.group == "personality" else 5
        axes[axis_id] = max(-100, min(100, body.randint(-spread, spread) + sp.axis_bias.get(axis_id, 0)))

    c = Character(
        name=name, species_id=species_id, stats=stats, axes=axes,
        skills=dict(sp.innate_skills), traits=list(sp.innate_traits),
        location=random.Random(f"{seed}:birth:location").choice(sp.habitats),
    )

    trait_rng = random.Random(f"{seed}:birth:traits")
    for trait_id in sorted(ct.traits):
        trait = ct.traits[trait_id]
        if trait.birth is None:
            continue
        roll = trait_rng.random()          # always drawn, so the stream stays stable
        if roll < trait.birth.chance and requirements_met(trait.birth.requirements, c, ct):
            grant_trait(c, ct, trait_id)

    talent = roll_hidden_talent(random.Random(f"{seed}:birth:talent"), c, ct, talent_chance)
    c.hidden_talent = talent
    c.talent_state = "dormant" if talent else "none"
    c.health = max_health(c, ct)

    article = "an" if sp.name[0].lower() in "aeiou" else "a"
    _log(c, "birth", f"{name} was born {article} {sp.name.lower()} in the {pretty(c.location).lower()}.")

    # Events normally fire after a birthday, so a species whose whole infancy is age 0 (a wolf)
    # would never meet an infancy event. The story can begin at birth with one.
    birth_events = random.Random(f"{seed}:birth:event")
    if birth_events.random() < stage_of(c, ct).event_chance * sp.event_rate:
        c.pending_event = _select_event(c, ct, birth_events)
    return c


# ---------------------------------------------------------------- actions

def action_label(action_id: str, species_id: str | None = None, ct: Content | None = None) -> str:
    ct = ct or get_content()
    if action_id.startswith("train:"):
        return f"Train {pretty(action_id.split(':', 1)[1])}"
    if action_id.startswith("train_stat:"):
        return f"Train {pretty(action_id.split(':', 1)[1])}"
    labels = ct.species[species_id].labels if species_id in ct.species else {}
    return labels.get(action_id) or DEFAULT_LABELS.get(action_id, pretty(action_id))


def action_budget(c: Character, ct: Content | None = None) -> int:
    """How many actions may be taken this year. The life stage decides."""
    ct = ct or get_content()
    if not c.alive:
        return 0
    return stage_of(c, ct).action_count


def actions_remaining(c: Character, ct: Content | None = None) -> int:
    ct = ct or get_content()
    return max(0, action_budget(c, ct) - len(c.actions_taken))


def social_budget(c: Character) -> int:
    """Social Actions per year. The same for every living character; none for the dead."""
    return SOCIAL_ACTIONS_PER_YEAR if c.alive else 0


def social_remaining(c: Character) -> int:
    return max(0, social_budget(c) - c.social_actions_used)


def start_social_action(c: Character, request_id: str | None = None) -> bool:
    """Spend one Social Action. Returns True if one was spent.

    A `request_id` already processed this year is a retry: nothing is spent again and it is not an error, even if the
    budget is used up since. Otherwise it is refused (NotAllowed) when the character has died, an event needs a
    decision, or no Social Actions remain, so the balance can never go below zero. Life actions are not touched.
    Only the budget lives here; what a social action does is not implemented yet.
    """
    if request_id is not None and request_id in c.social_requests:
        return False
    if not c.alive:
        raise NotAllowed("This character has died")
    if c.pending_event:
        raise NotAllowed("An event needs your decision first")
    if social_remaining(c) <= 0:
        raise NotAllowed("You have used all your Social Actions this year")
    c.social_actions_used += 1
    if request_id is not None:
        c.social_requests.append(request_id)
    return True


def offer_actions(c: Character, seed: int) -> list[dict]:
    """Actions available now. The life stage decides which kinds exist and how many may be used."""
    ct = get_content()
    if not c.alive or c.pending_event or actions_remaining(c, ct) <= 0:
        return []
    ids: list[str] = []
    for kind in stage_of(c, ct).actions:
        if kind == "train":
            pool = trainable_skills(c, ct)
            picks = year_rng(seed, c.age, "offer").sample(pool, min(2, len(pool)))
            ids += [f"train:{s}" for s in picks]
        elif kind == "train_stat":
            ids += [f"train_stat:{s}" for s in STATS]
        else:
            ids.append(kind)
    return [{"id": i, "label": action_label(i, c.species_id, ct)} for i in ids]


def perform_action(c: Character, seed: int, action_id: str) -> None:
    ct = get_content()
    if not c.alive:
        raise NotAllowed("This character has died")
    if c.pending_event:
        raise NotAllowed("An event needs your decision first")
    if actions_remaining(c, ct) <= 0:
        raise NotAllowed("You have used all your actions this year")
    if action_id not in {a["id"] for a in offer_actions(c, seed)}:
        raise InvalidChoice(f"Action not available: {action_id}")

    sp = species_of(c, ct)
    rng = year_rng(seed, c.age, f"action:{len(c.actions_taken)}:{action_id}")
    if action_id.startswith("train_stat:"):
        stat = action_id.split(":", 1)[1]
        text = f"You drilled your {stat}."
        if _bump(c, ct, stat, 1):
            text = f"You trained hard and grew in {stat} (+1)."
        stress = gain_stress(c, TRAIN_STAT_STRESS)
        text += f" The effort wears on you (+{stress:.0f} stress)."
    elif action_id.startswith("train:"):
        skill = action_id.split(":", 1)[1]
        gained = gain_skill(c, ct, skill, rng.uniform(1, 3))
        stress = gain_stress(c, TRAIN_SKILL_STRESS)
        text = f"Trained {pretty(skill)} (+{gained:.1f}, +{stress:.0f} stress)."
    elif action_id == "work":
        stat = rng.choice(sp.work_stats)
        text = f"You spent time on {action_label('work', c.species_id, ct).lower()}."
        if rng.random() < 0.5 and _bump(c, ct, stat, 1):
            text += f" It built your {stat} (+1)."
        stress = gain_stress(c, WORK_STRESS)
        text += f" (+{stress:.0f} stress.)"
        if rng.random() < 0.05:
            harm = change_health(c, ct, -8)
            if harm:
                text += f" An injury cost you some health ({harm:+d} health)."
            stress = gain_stress(c, 4)
    elif action_id == "rest":
        before = c.stress
        c.stress = clamp_vital(c.stress - 25)
        relieved = before - c.stress
        happiness = change_happiness(c, 4)
        text = f"You took the time off and unwound (-{relieved:.0f} stress, +{happiness:.0f} happiness)."
    elif action_id == "socialize":
        happiness = change_happiness(c, 10)
        text = f"You spent time with others and brightened (+{happiness:.0f} happiness)."
        if rng.random() < 0.3 and _bump(c, ct, "charisma", 1):
            text += " Your charm grew (+1 charisma)."
    else:  # play
        stat = rng.choice(sp.play_stats)
        happiness = change_happiness(c, 6)
        text = f"You spent time playing (+{happiness:.0f} happiness)."
        if rng.random() < 0.5 and _bump(c, ct, stat, 1):
            text += f" Play sharpened your {stat} (+1)."

    c.actions_taken.append(action_id)
    _log(c, "action", text)
    _check_vitals(c, ct)
    _log_death_if_needed(c)


# ---------------------------------------------------------------- the annual turn

def death_chance(c: Character, ct: Content | None = None) -> float:
    ct = ct or get_content()
    sp = species_of(c, ct)
    fraction = c.age / sp.lifespan
    base = sp.base_mortality + 0.32 * max(0.0, fraction - 0.5) ** 2
    con = effective_stat(c, ct, "constitution")
    return max(0.0, min(1.0, base * mortality_multiplier(c, ct) * (1.5 - con / 100)))


def _select_event(c: Character, ct: Content, rng: random.Random) -> str | None:
    seen = {h["event_id"] for h in c.history if h.get("event_id")}
    eligible = [
        e for e in ct.events.values()
        if not e.trigger_only and e.weight > 0
        and not (e.once and e.id in seen)
        and conditions_met(e.conditions, c, ct)
    ]
    if not eligible:
        return None
    return rng.choices(eligible, [e.weight for e in eligible])[0].id


def check_awakening(c: Character, seed: int, ct: Content | None = None) -> bool:
    """Awaken the hidden talent if its unlock conditions are met. Happens at most once per life."""
    ct = ct or get_content()
    if c.talent_state != "dormant" or not c.hidden_talent:
        return False
    talent = ct.talents[c.hidden_talent]
    if talent.awaken.min_stage and \
            ct.stage_ids.index(stage_of(c, ct).id) < ct.stage_ids.index(talent.awaken.min_stage):
        return False
    if not requirements_met(talent.awaken.requirements, c, ct):
        return False

    c.talent_state = "awakened"
    notes: list[str] = []
    apply_effects(talent.on_awaken.effects, c, ct, year_rng(seed, c.age, "awaken"), notes)
    text = f"Awakening: {talent.name}. {talent.on_awaken.narrative.strip()}"
    if notes:
        text += f" ({', '.join(notes)})"
    _log(c, "awakening", text)
    if talent.on_awaken.trigger_event:
        queue_event(c, talent.on_awaken.trigger_event)
    return True


def _acquire_traits(c: Character, ct: Content, rng: random.Random) -> None:
    for trait_id in sorted(ct.traits):
        trait = ct.traits[trait_id]
        if trait_id in c.traits:
            continue
        for rule in trait.acquire:
            if requirements_met(rule.requirements, c, ct) and rng.random() < rule.chance:
                if grant_trait(c, ct, trait_id):
                    _log(c, "trait", rule.narrative.strip())
                break


def _apply_age_up_vitals(c: Character, ct: Content) -> None:
    """At each Age Up: the body recovers, and Happiness depletes Stress."""
    healed = change_health(c, ct, max_health(c, ct) * (BASE_AGE_UP_HEAL + heal_bonus(c, ct)))
    before = c.stress
    c.stress = clamp_vital(c.stress - c.happiness / 10)
    drained = before - c.stress
    if healed > 0 and drained > 0:
        _log(c, "year", f"You recover with the turning of the year (+{healed} health, -{drained:.0f} stress).")
    elif healed > 0:
        _log(c, "year", f"Your body mends with the turning of the year (+{healed} health).")
    elif drained > 0:
        _log(c, "year", f"Contentment wears down your worries (-{drained:.0f} stress).")


def advance_turn(c: Character, seed: int) -> None:
    ct = get_content()
    if not c.alive:
        raise NotAllowed("This character has died")
    if c.pending_event:
        raise NotAllowed("An event needs your decision first")

    rng = year_rng(seed, c.age, "turn")
    had_action = bool(c.actions_taken)
    c.age += 1
    c.actions_taken = []
    c.social_actions_used = 0                    # a new year: the Social Action budget is full again. Set, never added
    c.social_requests = []                       # to, so processing a year twice cannot overfill it
    stage = stage_of(c, ct)
    entries_before = len(c.history)

    if rng.random() < stage.growth_chance:
        stat = rng.choice(STATS)
        if _bump(c, ct, stat, 1):
            _log(c, "growth", f"You grew in {stat} (+1).")

    if rng.random() < stage.aging_chance and _bump(c, ct, "constitution", -1):
        _log(c, "aging", "The years weigh on you (-1 constitution).")

    _apply_age_up_vitals(c, ct)

    if rng.random() < death_chance(c, ct):
        fraction = c.age / species_of(c, ct).lifespan
        _die(c, "old age" if fraction > 0.75 else "illness")
        return
    _check_vitals(c, ct)
    _log_death_if_needed(c)
    if not c.alive:
        return

    check_awakening(c, seed, ct)

    if c.talent_state == "dormant" and rng.random() < HINT_CHANCE:
        hint = rng.choice(ct.talents[c.hidden_talent].hints)       # vague on purpose: never names the talent
        _log(c, "hint", hint)

    _acquire_traits(c, ct, rng)

    if not c.pending_event and rng.random() < stage.event_chance * species_of(c, ct).event_rate:
        c.pending_event = _select_event(c, ct, rng)

    if not had_action and not c.pending_event and len(c.history) == entries_before:
        _log(c, "year", "A quiet year.")


# ---------------------------------------------------------------- events

def event_choice_state(c: Character, ct: Content, event_id: str) -> list[dict]:
    """The choices for an event, each flagged available/locked with a reason. A combat choice is offered as a
    Fight (kind "fight") followed by a generated Flee (kind "flee", id "<choice>:flee"), both carrying the
    resolver's pre-battle preview, unless the encounter forbids fleeing."""
    out = []
    for ch in ct.events[event_id].choices:
        reason = unmet_requirements_text(ch.requirements, c, ct)
        entry = {"id": ch.id, "label": ch.text, "available": reason is None, "requirement": reason,
                 "kind": "normal", "combat": None}
        if ch.combat is None:
            out.append(entry)
            continue
        shown = battle.preview(battle.matchup(c, ct, ch.combat)).model_dump()
        out.append({**entry, "kind": "fight", "combat": shown})
        if ch.combat.flee.allowed:
            out.append({**entry, "id": ch.id + battle.FLEE_SUFFIX, "label": ch.combat.flee.text,
                        "kind": "flee", "combat": shown})
    return out


def resolve_event(c: Character, seed: int, event_id: str, choice_id: str) -> battle.BattleSummary | None:
    """Resolve the pending event with a choice. A combat choice (or its generated Flee) is resolved by the combat
    system and returns its battle summary; every other choice returns None."""
    ct = get_content()
    if not c.alive:
        raise NotAllowed("This character has died")
    if not c.pending_event:
        raise NotAllowed("No event is waiting for a decision")
    if c.pending_event != event_id:
        raise NotAllowed("That is not the current event")

    event = ct.events[event_id]
    flee = choice_id.endswith(battle.FLEE_SUFFIX)
    choice = next((ch for ch in event.choices if ch.id == (choice_id[:-len(battle.FLEE_SUFFIX)] if flee else choice_id)), None)
    if choice is None or (flee and not (choice.combat and choice.combat.flee.allowed)):
        raise InvalidChoice(f"Unknown choice: {choice_id}")
    locked = unmet_requirements_text(choice.requirements, c, ct)
    if locked:
        raise NotAllowed(locked)
    if choice.combat:
        return _resolve_combat(c, ct, seed, event, choice, flee=flee)

    rng = year_rng(seed, c.age, f"event:{event_id}")
    notes: list[str] = []
    if choice.check:
        chk = choice.check
        value = effective_stat(c, ct, chk.stat) if chk.stat else c.skills.get(chk.skill, 0.0)
        chance = max(0.05, min(0.95, 0.5 + (value - chk.difficulty) / 100))
        success = rng.random() < chance
        if not success and has_ability(c, ct, "second_wind") and rng.random() < 0.5:
            success = True
            notes.append("Second Wind turned the tide")
        outcome = choice.success if success else choice.failure
        result, effects = outcome.result, outcome.effects
    else:
        result, effects = choice.result, choice.effects

    c.pending_event = None
    apply_effects(effects, c, ct, rng, notes)
    _check_vitals(c, ct)
    text = f"{event.title}: {result}"
    if notes:
        text += f" ({', '.join(notes)})"
    _log(c, "event", text, event_id=event_id)

    if not c.alive:
        _log_death_if_needed(c)
        return None
    check_awakening(c, seed, ct)
    if c.pending_event is None and c.queued_events:
        c.pending_event = c.queued_events.pop(0)
    return None


def _resolve_combat(c: Character, ct: Content, seed: int, event: EventDef, choice: ChoiceDef,
                    *, flee: bool) -> battle.BattleSummary:
    """Fight or flee. Both Powers come from the Power system, the dice are seeded like every other roll, and the
    consequences are applied once, here: damage and death through `apply_battle_damage`, rewards and other
    effects through the normal effect engine. A successful flee applies nothing."""
    combat_def = choice.combat
    m = battle.matchup(c, ct, combat_def)
    roll = battle.roll_battle(m, flee=flee, rngs=battle.rngs_for(f"{seed}:{c.age}:combat:{event.id}"))
    before = battle.snapshot(c, ct)

    c.pending_event = None
    outcome = {"victory": combat_def.victory, "defeat": combat_def.defeat}.get(roll.outcome)
    health_lost = 0
    if roll.outcome == "defeat":
        health_lost = apply_battle_damage(c, ct, battle.damage_for(max_health(c, ct), roll),
                                          lethal=roll.instant_death, cause=m.opponent.cause or DEFAULT_BATTLE_CAUSE)
    notes: list[str] = []
    if outcome and c.alive:                                     # the dead collect no rewards
        apply_effects(outcome.effects, c, ct, year_rng(seed, c.age, f"event:{event.id}"), notes)
    _check_vitals(c, ct)

    text = battle.result_text(roll, combat_def.flee, outcome.result if outcome else None)
    if health_lost:
        notes.append(f"{-health_lost:+d} health")
    _log(c, "event", f"{event.title}: {text}" + (f" ({', '.join(notes)})" if notes else ""), event_id=event.id)
    summary = battle.build_summary(source="legacy", event_id=event.id, event_title=event.title, m=m, roll=roll,
                                   text=text, before=before, after=c, health_lost=health_lost, ct=ct)
    if not c.alive:
        _log_death_if_needed(c)
        return summary
    check_awakening(c, seed, ct)
    if c.pending_event is None and c.queued_events:
        c.pending_event = c.queued_events.pop(0)
    return summary


# ---------------------------------------------------------------- housekeeping & summary

def repair(c: Character) -> Character:
    """Make a loaded character safe: tolerate saves from older versions and removed content."""
    ct = get_content()
    if c.species_id not in ct.species:
        c.species_id = "human"
    if c.pending_event and c.pending_event not in ct.events:
        c.pending_event = None
    c.queued_events = [e for e in c.queued_events if e in ct.events]
    c.skills = migrate_skills(c.skills, ct)                  # merged or removed skills (the old magic schools)
    for axis in ct.axes:
        c.axes.setdefault(axis, 0)
    if c.location not in species_of(c, ct).habitats and not c.location:
        c.location = species_of(c, ct).habitats[0]
    # Vitals were added later: old saves start healthy, calm and content.
    if c.health is None:
        c.health = max_health(c, ct)
    else:
        c.health = max(0, min(max_health(c, ct), c.health))
    c.stress = clamp_vital(c.stress)
    c.happiness = clamp_vital(c.happiness)
    # Social Actions were added later: older saves start with a full budget; a corrupt value is brought into range.
    c.social_actions_used = max(0, min(SOCIAL_ACTIONS_PER_YEAR, int(c.social_actions_used or 0)))
    c.social_requests = [r for r in (c.social_requests or []) if isinstance(r, str)][-SOCIAL_ACTIONS_PER_YEAR:]
    return c


def summarize(c: Character) -> dict:
    ct = get_content()
    top = sorted(c.skills.items(), key=lambda kv: kv[1], reverse=True)[:3]
    unawakened = ct.talents[c.hidden_talent].name if c.talent_state == "dormant" and c.hidden_talent else None
    return {
        "name": c.name,
        "species": species_of(c, ct).name,
        "age": c.age,
        "alive": c.alive,
        "cause_of_death": c.cause_of_death,
        "stats": {s: effective_stat(c, ct, s) for s in STATS},
        "top_skills": [{"skill": pretty(s), "level": round(v, 1)} for s, v in top],
        "traits": [ct.traits[t].name for t in c.traits if t in ct.traits],
        "alignment": alignment_label(c),
        "events_faced": sum(1 for h in c.history if h["kind"] == "event"),
        "defining_skill": pretty(top[0][0]) if top else None,
        "awakened_talent": ct.talents[c.hidden_talent].name if c.talent_state == "awakened" else None,
        # A talent that never awakened is revealed only now, once the life is over.
        "unawakened_talent": unawakened,
    }
