"""Pure simulation logic. No database or web code lives here."""
import json
import random
from pathlib import Path

from pydantic import BaseModel, Field

STATS = ["constitution", "dexterity", "wisdom", "strength", "charisma"]
STAT_MAX = 100
SKILLS_PATH = Path(__file__).parent / "data" / "skills.json"


class Character(BaseModel):
    name: str
    age: int = 0
    alive: bool = True
    stats: dict[str, int] = Field(default_factory=dict)
    skills: dict[str, float] = Field(default_factory=dict)   # skill_id -> level
    history: list[dict] = Field(default_factory=list)        # one entry per year
    cause_of_death: str | None = None


# ---------- data-driven skills ----------

def load_skills() -> dict[str, str]:
    """Return {skill_id: category}. Add a skill by editing skills.json only."""
    data = json.loads(SKILLS_PATH.read_text(encoding="utf-8"))
    return {skill: cat for cat, skills in data.items() for skill in skills}


def pretty(skill_id: str) -> str:
    return skill_id.replace("_", " ").title()


# ---------- randomness ----------

def year_rng(seed: int, age: int, purpose: str = "year") -> random.Random:
    """A fresh deterministic RNG for one year of one life.

    Because it is derived from (seed, age), the database never has to store
    RNG state, and the choices shown for a year always match that year's outcome.
    """
    return random.Random(f"{seed}:{age}:{purpose}")


# ---------- character creation ----------

def new_character(name: str, rng: random.Random) -> Character:
    return Character(name=name, stats={s: rng.randint(20, 60) for s in STATS})


# ---------- yearly choices ----------

def offer_choices(c: Character, seed: int) -> list[dict]:
    """The options available this year. Empty once the character is dead."""
    if not c.alive:
        return []
    rng = year_rng(seed, c.age, "choices")
    skills = sorted(load_skills())
    picks = rng.sample(skills, 2)

    if c.age < 6:
        return [{"id": "play", "label": "Play with the village children"}]

    choices = [{"id": f"train:{s}", "label": f"Train {pretty(s)}"} for s in picks]
    if c.age >= 14:
        choices.append({"id": "work", "label": "Take on hard work"})
    else:
        choices.append({"id": "play", "label": "Play and explore"})
    choices.append({"id": "rest", "label": "Rest and look after yourself"})
    return choices


# ---------- the yearly turn ----------

def _bump(c: Character, stat: str, amount: int) -> None:
    c.stats[stat] = max(1, min(STAT_MAX, c.stats[stat] + amount))


def death_chance(c: Character) -> float:
    base = 0.001 + (max(0, c.age - 40) ** 2) * 0.00005
    return min(1.0, base * (1.5 - c.stats["constitution"] / 100))


def advance_year(c: Character, rng: random.Random, choice: str | None = None) -> None:
    if not c.alive:
        raise ValueError("Character is already dead")

    c.age += 1
    events: list[str] = []

    # --- the player's choice ---
    if choice and choice.startswith("train:"):
        skill = choice.split(":", 1)[1]
        if skill not in load_skills():
            raise ValueError(f"Unknown skill: {skill}")
        gain = rng.uniform(1, 3)
        c.skills[skill] = round(c.skills.get(skill, 0) + gain, 2)
        events.append(f"Trained {pretty(skill)}")
    elif choice == "work":
        stat = rng.choice(["strength", "dexterity", "charisma"])
        if rng.random() < 0.5:
            _bump(c, stat, 1)
            events.append(f"Hard work built your {stat}")
        else:
            events.append("Worked a long, uneventful year")
        if rng.random() < 0.05:
            _bump(c, "constitution", -2)
            events.append("Suffered an injury at work")
    elif choice == "rest":
        if rng.random() < 0.6:
            _bump(c, "constitution", 1)
            events.append("Rested and felt stronger")
        else:
            events.append("Took a quiet year")
    elif choice == "play":
        stat = rng.choice(["dexterity", "wisdom", "charisma"])
        if rng.random() < 0.5:
            _bump(c, stat, 1)
            events.append(f"Play sharpened your {stat}")
        else:
            events.append("Spent the year playing")
    elif choice is not None:
        raise ValueError(f"Unknown choice: {choice}")

    # --- natural growth while young ---
    if c.age <= 18 and rng.random() < 0.3:
        stat = rng.choice(STATS)
        _bump(c, stat, 1)
        events.append(f"Grew in {stat}")

    # --- aging ---
    if c.age > 50 and rng.random() < 0.5:
        _bump(c, "constitution", -1)

    # --- death ---
    if rng.random() < death_chance(c):
        c.alive = False
        c.cause_of_death = "old age" if c.age > 60 else "illness"
        events.append("Died")

    c.history.append({"age": c.age, "choice": choice, "events": events})


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
        "years_recorded": len(c.history),
        "defining_skill": pretty(top[0][0]) if top else None,
    }
