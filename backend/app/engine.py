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

from .character import Character
from .content import STATS, Content, get_content
from .rules import (
    alignment_label, apply_effects, clamp_stat, conditions_met, effective_stat, gain_skill,
    grant_trait, has_ability, mortality_multiplier, pretty, queue_event, requirements_met,
    skill_aptitude, species_of, stage_of, trainable_skills, unmet_requirements_text,
)

TALENT_BIRTH_CHANCE = 0.01
HINT_CHANCE = 0.10
DEFAULT_LABELS = {"play": "Play and explore", "work": "Take on hard work", "rest": "Rest and look after yourself"}


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
    labels = ct.species[species_id].labels if species_id in ct.species else {}
    return labels.get(action_id) or DEFAULT_LABELS.get(action_id, pretty(action_id))


def offer_actions(c: Character, seed: int) -> list[dict]:
    """Actions available now. The life stage decides which kinds exist."""
    ct = get_content()
    if not c.alive or c.pending_event or c.action_taken:
        return []
    ids: list[str] = []
    for kind in stage_of(c, ct).actions:
        if kind == "train":
            pool = trainable_skills(c, ct)
            picks = year_rng(seed, c.age, "offer").sample(pool, min(2, len(pool)))
            ids += [f"train:{s}" for s in picks]
        else:
            ids.append(kind)
    return [{"id": i, "label": action_label(i, c.species_id, ct)} for i in ids]


def perform_action(c: Character, seed: int, action_id: str) -> None:
    ct = get_content()
    if not c.alive:
        raise NotAllowed("This character has died")
    if c.pending_event:
        raise NotAllowed("An event needs your decision first")
    if c.action_taken:
        raise NotAllowed("You have already acted this year")
    if action_id not in {a["id"] for a in offer_actions(c, seed)}:
        raise InvalidChoice(f"Action not available: {action_id}")

    sp = species_of(c, ct)
    rng = year_rng(seed, c.age, "action")
    if action_id.startswith("train:"):
        skill = action_id.split(":", 1)[1]
        gained = gain_skill(c, ct, skill, rng.uniform(1, 3))
        text = f"Trained {pretty(skill)} (+{gained:.1f})."
    elif action_id == "work":
        stat = rng.choice(sp.work_stats)
        text = f"You spent the year on {action_label('work', c.species_id, ct).lower()}."
        if rng.random() < 0.5 and _bump(c, ct, stat, 1):
            text += f" It built your {stat} (+1)."
        if rng.random() < 0.05:
            _bump(c, ct, "constitution", -2)
            text += " An injury cost you some health (-2 constitution)."
    elif action_id == "rest":
        text = "You took a quiet, restful year."
        if rng.random() < 0.6 and _bump(c, ct, "constitution", 1):
            text = "You rested and felt stronger (+1 constitution)."
    else:  # play
        stat = rng.choice(sp.play_stats)
        text = "You spent the year playing."
        if rng.random() < 0.5 and _bump(c, ct, stat, 1):
            text = f"Play sharpened your {stat} (+1)."

    c.action_taken = action_id
    _log(c, "action", text)


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


def advance_turn(c: Character, seed: int) -> None:
    ct = get_content()
    if not c.alive:
        raise NotAllowed("This character has died")
    if c.pending_event:
        raise NotAllowed("An event needs your decision first")

    rng = year_rng(seed, c.age, "turn")
    had_action = c.action_taken is not None
    c.age += 1
    c.action_taken = None
    stage = stage_of(c, ct)
    entries_before = len(c.history)

    if rng.random() < stage.growth_chance:
        stat = rng.choice(STATS)
        if _bump(c, ct, stat, 1):
            _log(c, "growth", f"You grew in {stat} (+1).")

    if rng.random() < stage.aging_chance and _bump(c, ct, "constitution", -1):
        _log(c, "aging", "The years weigh on you (-1 constitution).")

    if rng.random() < death_chance(c, ct):
        fraction = c.age / species_of(c, ct).lifespan
        _die(c, "old age" if fraction > 0.75 else "illness")
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
    """The choices for an event, each flagged available/locked with a reason."""
    out = []
    for ch in ct.events[event_id].choices:
        reason = unmet_requirements_text(ch.requirements, c, ct)
        out.append({"id": ch.id, "label": ch.text, "available": reason is None, "requirement": reason})
    return out


def resolve_event(c: Character, seed: int, event_id: str, choice_id: str) -> None:
    ct = get_content()
    if not c.alive:
        raise NotAllowed("This character has died")
    if not c.pending_event:
        raise NotAllowed("No event is waiting for a decision")
    if c.pending_event != event_id:
        raise NotAllowed("That is not the current event")

    event = ct.events[event_id]
    choice = next((ch for ch in event.choices if ch.id == choice_id), None)
    if choice is None:
        raise InvalidChoice(f"Unknown choice: {choice_id}")
    locked = unmet_requirements_text(choice.requirements, c, ct)
    if locked:
        raise NotAllowed(locked)

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
    text = f"{event.title}: {result}"
    if notes:
        text += f" ({', '.join(notes)})"
    _log(c, "event", text, event_id=event_id)

    if not c.alive:
        _log(c, "death", f"Died at age {c.age} of {c.cause_of_death}.")
        return
    check_awakening(c, seed, ct)
    if c.pending_event is None and c.queued_events:
        c.pending_event = c.queued_events.pop(0)


# ---------------------------------------------------------------- housekeeping & summary

def repair(c: Character) -> Character:
    """Make a loaded character safe: tolerate saves from older versions and removed content."""
    ct = get_content()
    if c.species_id not in ct.species:
        c.species_id = "human"
    if c.pending_event and c.pending_event not in ct.events:
        c.pending_event = None
    c.queued_events = [e for e in c.queued_events if e in ct.events]
    for axis in ct.axes:
        c.axes.setdefault(axis, 0)
    if c.location not in species_of(c, ct).habitats and not c.location:
        c.location = species_of(c, ct).habitats[0]
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
