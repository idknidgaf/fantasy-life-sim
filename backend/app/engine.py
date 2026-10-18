import random
from pydantic import BaseModel, Field

STATS = ["constitution", "dexterity", "wisdom", "strength", "charisma"]


class Character(BaseModel):
    name: str
    age: int = 0
    alive: bool = True
    stats: dict[str, int] = Field(default_factory=dict)
    skills: dict[str, float] = Field(default_factory=dict)   # skill_id -> level
    history: list[dict] = Field(default_factory=list)        # one entry per year
    cause_of_death: str | None = None


def new_character(name: str, rng: random.Random) -> Character:
    return Character(name=name, stats={s: rng.randint(20, 60) for s in STATS})


def death_chance(c: Character) -> float:
    base = 0.001 + (max(0, c.age - 40) ** 2) * 0.00005
    return min(1.0, base * (1.5 - c.stats["constitution"] / 100))


def advance_year(c: Character, rng: random.Random, focus_skill: str | None = None) -> None:
    c.age += 1
    events = []

    if focus_skill:                                  # the player's yearly choice
        c.skills[focus_skill] = c.skills.get(focus_skill, 0) + rng.uniform(1, 3)
        events.append(f"Trained {focus_skill}")

    if c.age > 50 and rng.random() < 0.5:            # aging
        c.stats["constitution"] = max(1, c.stats["constitution"] - 1)

    if rng.random() < death_chance(c):
        c.alive = False
        c.cause_of_death = "old age" if c.age > 60 else "illness"
        events.append("Died")

    c.history.append({"age": c.age, "events": events})
