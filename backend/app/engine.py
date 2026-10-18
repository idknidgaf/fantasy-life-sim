"""Pure simulation logic. No database or web code lives here.

A year works like this:
  1. The player may perform ONE action (train, work, rest...).
  2. The player advances the turn: the year resolves, they age, and an event may fire.
  3. If an event fires, it must be resolved (pick a choice) before the next action or turn.
"""
import json
import random
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

STATS = ["constitution", "dexterity", "wisdom", "strength", "charisma"]
STAT_MAX = 100
EVENT_CHANCE = 0.35
DATA_DIR = Path(__file__).parent / "data"

BASIC_ACTIONS = {
    "play": "Play and explore",
    "work": "Take on hard work",
    "rest": "Rest and look after yourself",
}


class NotAllowed(Exception):
    """The request is valid but not possible right now (HTTP 409)."""


class InvalidChoice(Exception):
    """The request names something that does not exist or is not on offer (HTTP 400)."""


class Character(BaseModel):
    name: str
    age: int = 0
    alive: bool = True
    stats: dict[str, int] = Field(default_factory=dict)
    skills: dict[str, float] = Field(default_factory=dict)   # skill_id -> level
    history: list[dict] = Field(default_factory=list)        # {age, kind, text, event_id}
    cause_of_death: str | None = None
    action_taken: str | None = None      # action performed this year, if any
    pending_event: str | None = None     # event id waiting for a decision


# ---------- data-driven content ----------

@lru_cache
def _read_json(filename: str):
    return json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))


def load_skills() -> dict[str, str]:
    """{skill_id: category}. Add a skill by editing skills.json only."""
    return {s: cat for cat, skills in _read_json("skills.json").items() for s in skills}


def load_events() -> dict[str, dict]:
    """{event_id: event}. Add a story event by editing events.json only."""
    return {e["id"]: e for e in _read_json("events.json")}


def pretty(skill_id: str) -> str:
    return skill_id.replace("_", " ").title()


# ---------- randomness ----------

def year_rng(seed: int, age: int, purpose: str) -> random.Random:
    """A fresh deterministic RNG for one purpose in one year of one life.

    Derived from (seed, age, purpose), so the database never stores RNG state
    and the same life always unfolds the same way.
    """
    return random.Random(f"{seed}:{age}:{purpose}")


# ---------- helpers ----------

def _bump(c: Character, stat: str, amount: int) -> None:
    c.stats[stat] = max(1, min(STAT_MAX, c.stats[stat] + amount))


def _log(c: Character, kind: str, text: str, event_id: str | None = None) -> None:
    c.history.append({"age": c.age, "kind": kind, "text": text, "event_id": event_id})


def _die(c: Character, cause: str) -> None:
    c.alive = False
    c.cause_of_death = cause
    _log(c, "death", f"Died at age {c.age} of {cause}.")


# ---------- character creation ----------

def new_character(name: str, rng: random.Random) -> Character:
    c = Character(name=name, stats={s: rng.randint(20, 60) for s in STATS})
    _log(c, "birth", f"{name} was born.")
    return c


# ---------- actions ----------

def action_label(action_id: str) -> str:
    if action_id.startswith("train:"):
        return f"Train {pretty(action_id.split(':', 1)[1])}"
    return BASIC_ACTIONS.get(action_id, pretty(action_id))


def offer_actions(c: Character, seed: int) -> list[dict]:
    """Actions the character may take right now."""
    if not c.alive or c.pending_event or c.action_taken:
        return []
    if c.age < 6:
        return [{"id": "play", "label": action_label("play")}]

    rng = year_rng(seed, c.age, "offer")
    picks = rng.sample(sorted(load_skills()), 2)
    ids = [f"train:{s}" for s in picks]
    ids.append("work" if c.age >= 14 else "play")
    ids.append("rest")
    return [{"id": i, "label": action_label(i)} for i in ids]


def perform_action(c: Character, seed: int, action_id: str) -> None:
    if not c.alive:
        raise NotAllowed("This character has died")
    if c.pending_event:
        raise NotAllowed("An event needs your decision first")
    if c.action_taken:
        raise NotAllowed("You have already acted this year")
    if action_id not in {a["id"] for a in offer_actions(c, seed)}:
        raise InvalidChoice(f"Action not available: {action_id}")

    rng = year_rng(seed, c.age, "action")
    if action_id.startswith("train:"):
        skill = action_id.split(":", 1)[1]
        gain = rng.uniform(1, 3)
        c.skills[skill] = round(c.skills.get(skill, 0) + gain, 2)
        text = f"Trained {pretty(skill)} (+{gain:.1f})."
    elif action_id == "work":
        stat = rng.choice(["strength", "dexterity", "charisma"])
        text = "Worked a long, uneventful year."
        if rng.random() < 0.5:
            _bump(c, stat, 1)
            text = f"Hard work built your {stat} (+1)."
        if rng.random() < 0.05:
            _bump(c, "constitution", -2)
            text += " An injury at work cost you some health (-2 constitution)."
    elif action_id == "rest":
        if rng.random() < 0.6:
            _bump(c, "constitution", 1)
            text = "Rested and felt stronger (+1 constitution)."
        else:
            text = "Took a quiet, restful year."
    else:  # play
        stat = rng.choice(["dexterity", "wisdom", "charisma"])
        if rng.random() < 0.5:
            _bump(c, stat, 1)
            text = f"Play sharpened your {stat} (+1)."
        else:
            text = "Spent the year playing."

    c.action_taken = action_id
    _log(c, "action", text)


# ---------- the annual turn ----------

def death_chance(c: Character) -> float:
    base = 0.001 + (max(0, c.age - 40) ** 2) * 0.00005
    return min(1.0, base * (1.5 - c.stats["constitution"] / 100))


def _roll_event(c: Character, rng: random.Random) -> None:
    if rng.random() >= EVENT_CHANCE:
        return
    seen = {h["event_id"] for h in c.history if h.get("event_id")}
    eligible = [
        e for e in load_events().values()
        if e["min_age"] <= c.age <= e["max_age"] and not (e.get("once") and e["id"] in seen)
    ]
    if eligible:
        c.pending_event = rng.choices(eligible, [e.get("weight", 1) for e in eligible])[0]["id"]


def advance_turn(c: Character, seed: int) -> None:
    if not c.alive:
        raise NotAllowed("This character has died")
    if c.pending_event:
        raise NotAllowed("An event needs your decision first")

    rng = year_rng(seed, c.age, "turn")
    had_action = c.action_taken is not None
    c.age += 1
    c.action_taken = None
    entries_before = len(c.history)

    if c.age <= 18 and rng.random() < 0.3:
        stat = rng.choice(STATS)
        _bump(c, stat, 1)
        _log(c, "growth", f"You grew in {stat} (+1).")

    if c.age > 50 and rng.random() < 0.5:
        _bump(c, "constitution", -1)
        _log(c, "aging", "The years weigh on you (-1 constitution).")

    if rng.random() < death_chance(c):
        _die(c, "old age" if c.age > 60 else "illness")
        return

    _roll_event(c, rng)

    if not had_action and not c.pending_event and len(c.history) == entries_before:
        _log(c, "year", "A quiet year.")


# ---------- events ----------

def _apply_effect(c: Character, effect: dict, rng: random.Random, notes: list[str]) -> None:
    if "chance" in effect and rng.random() >= effect["chance"]:
        return
    kind = effect["type"]
    if kind == "stat":
        _bump(c, effect["target"], effect["delta"])
        notes.append(f"{effect['delta']:+d} {effect['target']}")
    elif kind == "skill":
        skill = effect["target"]
        c.skills[skill] = round(max(0.0, c.skills.get(skill, 0) + effect["delta"]), 2)
        notes.append(f"{effect['delta']:+g} {pretty(skill)}")
    elif kind == "death":
        c.alive = False
        c.cause_of_death = effect.get("cause", "misadventure")


def resolve_event(c: Character, seed: int, event_id: str, choice_id: str) -> None:
    if not c.alive:
        raise NotAllowed("This character has died")
    if not c.pending_event:
        raise NotAllowed("No event is waiting for a decision")
    if c.pending_event != event_id:
        raise NotAllowed("That is not the current event")

    event = load_events()[event_id]
    choice = next((ch for ch in event["choices"] if ch["id"] == choice_id), None)
    if choice is None:
        raise InvalidChoice(f"Unknown choice: {choice_id}")

    rng = year_rng(seed, c.age, f"event:{event_id}")
    notes: list[str] = []
    for effect in choice.get("effects", []):
        _apply_effect(c, effect, rng, notes)

    text = f"{event['title']}: {choice['result']}"
    if notes:
        text += f" ({', '.join(notes)})"
    c.pending_event = None
    _log(c, "event", text, event_id=event_id)

    if not c.alive:
        _log(c, "death", f"Died at age {c.age} of {c.cause_of_death}.")


# ---------- life summary ----------

def summarize(c: Character) -> dict:
    top = sorted(c.skills.items(), key=lambda kv: kv[1], reverse=True)[:3]
    return {
        "name": c.name,
        "age": c.age,
        "alive": c.alive,
        "cause_of_death": c.cause_of_death,
        "stats": c.stats,
        "top_skills": [{"skill": pretty(s), "level": round(v, 1)} for s, v in top],
        "events_faced": sum(1 for h in c.history if h["kind"] == "event"),
        "defining_skill": pretty(top[0][0]) if top else None,
    }
