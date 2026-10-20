"""The condition engine (is X true of this character?) and effect engine (make X happen)."""
from __future__ import annotations

import random

from .character import Character
from .content import (
    HUMAN_YEARS, MONSTER_STAGE_NAMES, REQ_KINDS, STATS, ConditionsDef, Content,
    SpeciesDef, StageDef,
)

AXIS_LIMIT = 100


def pretty(identifier: str) -> str:
    return identifier.replace("_", " ").title()


# ---------------------------------------------------------------- species, stages, derived values

def species_of(c: Character, ct: Content) -> SpeciesDef:
    return ct.species[c.species_id]


def stage_at(sp: SpeciesDef, age: int, ct: Content) -> StageDef:
    """Life stage for a species at a real age, scaled to human-equivalent years."""
    equivalent = age * HUMAN_YEARS / sp.lifespan
    current = ct.stages[0]
    for st in ct.stages:
        if equivalent >= st.starts_at:
            current = st
    return current


def stage_of(c: Character, ct: Content) -> StageDef:
    return stage_at(species_of(c, ct), c.age, ct)


def stage_name(sp: SpeciesDef, stage: StageDef) -> str:
    """Display name for the stage. Beasts and monsters use Hatchling/Juvenile/Adult/Ancient."""
    if sp.category == "humanoid":
        return stage.name
    return stage.monster_name or MONSTER_STAGE_NAMES.get(stage.id, stage.name)


def stage_index(ct: Content, stage_id: str) -> int:
    return ct.stage_ids.index(stage_id)


def trait_defs(c: Character, ct: Content):
    return [ct.traits[t] for t in c.traits if t in ct.traits]


def effective_stat(c: Character, ct: Content, stat: str) -> int:
    bonus = sum(t.modifiers.stat.get(stat, 0) for t in trait_defs(c, ct))
    return max(1, c.stats.get(stat, 0) + bonus)


def has_ability(c: Character, ct: Content, ability: str) -> bool:
    return any(ability in t.abilities for t in trait_defs(c, ct))


def skill_aptitude(c: Character, ct: Content, skill: str) -> float:
    """Multiplier on skill gains, from species and traits (keyed by skill, category or 'all')."""
    category = ct.skills[skill]
    sources = [species_of(c, ct).skill_aptitude] + [t.modifiers.skill_aptitude for t in trait_defs(c, ct)]
    mult = 1.0
    for source in sources:
        for key in ("all", category, skill):
            if key in source:
                mult *= source[key]
    return mult


def mortality_multiplier(c: Character, ct: Content) -> float:
    mult = 1.0
    for t in trait_defs(c, ct):
        mult *= t.modifiers.mortality
    return mult


# ---------------------------------------------------------------- vitals

VITALS_LIMIT = 100.0
BASE_AGE_UP_HEAL = 0.10          # fraction of max health recovered at each Age Up


def max_health(c: Character, ct: Content) -> int:
    return 40 + effective_stat(c, ct, "constitution")


def clamp_vital(value: float) -> float:
    return round(max(0.0, min(VITALS_LIMIT, value)), 1)


def stress_rate(c: Character) -> float:
    """Happiness slows Stress gain: 100 happiness halves it, 0 happiness raises it by half."""
    return 1.5 - clamp_vital(c.happiness) / VITALS_LIMIT


def gain_stress(c: Character, amount: float) -> float:
    """Add Stress scaled by Happiness. Returns the actual change."""
    before = c.stress
    c.stress = clamp_vital(c.stress + amount * stress_rate(c))
    return c.stress - before


def change_happiness(c: Character, amount: float) -> float:
    before = c.happiness
    c.happiness = clamp_vital(c.happiness + amount)
    return c.happiness - before


def heal_bonus(c: Character, ct: Content) -> float:
    """Extra fraction of max health healed at each Age Up, from traits (e.g. Regenerative)."""
    return sum(t.modifiers.heal_bonus for t in trait_defs(c, ct))


def change_health(c: Character, ct: Content, amount: float) -> int:
    """Change current health, clamped to 0..max_health. Returns the actual change."""
    before = c.health if c.health is not None else max_health(c, ct)
    after = max(0, min(max_health(c, ct), round(before + amount)))
    c.health = after
    return after - before


def describe_trait_effects(t, ct: Content) -> str:
    """Player-facing summary of what a trait does, e.g. '+6 Constitution, ×0.8 mortality'."""
    parts: list[str] = []
    for stat, v in sorted(t.modifiers.stat.items(), key=lambda kv: -abs(kv[1])):
        parts.append(f"{v:+d} {pretty(stat)}")
    for key, v in sorted(t.modifiers.skill_aptitude.items()):
        parts.append(f"×{v:g} {pretty(key)} XP")
    if t.modifiers.mortality != 1.0:
        parts.append(f"×{t.modifiers.mortality:g} mortality")
    if t.modifiers.heal_bonus:
        parts.append(f"Each Age Up heals +{t.modifiers.heal_bonus:.0%} more")
    if t.modifiers.power:
        parts.append(f"{t.modifiers.power:+.0%} Power")
    for skill in t.modifiers.unlock_skills:
        parts.append(f"Unlocks {pretty(skill)}")
    for ability in t.abilities:
        parts.append(pretty(ability))
    return ", ".join(parts)


def unlocked_skills(c: Character, ct: Content) -> set[str]:
    """Skills a character may train even if their category is locked (supernatural)."""
    unlocked = set(species_of(c, ct).innate_skills)
    for t in trait_defs(c, ct):
        unlocked |= set(t.modifiers.unlock_skills)
    unlocked |= {s for s, level in c.skills.items() if level > 0}
    return unlocked


def trainable_skills(c: Character, ct: Content) -> list[str]:
    stage = stage_of(c, ct)
    allowed = species_of(c, ct).allowed_skills
    unlocked = unlocked_skills(c, ct)
    out = []
    for skill, category in sorted(ct.skills.items()):
        if "all" not in stage.train_categories and category not in stage.train_categories:
            continue
        if allowed is not None and skill not in allowed:
            continue
        if category == "supernatural" and skill not in unlocked:
            continue
        out.append(skill)
    return out


def alignment_label(c: Character) -> str:
    order = c.axes.get("law_chaos", 0)
    moral = c.axes.get("good_evil", 0)
    a = "Lawful" if order >= 25 else "Chaotic" if order <= -25 else "Neutral"
    b = "Good" if moral >= 25 else "Evil" if moral <= -25 else "Neutral"
    return "True Neutral" if a == b == "Neutral" else f"{a} {b}"


# ---------------------------------------------------------------- condition engine

def _in_range(x: float, lo, hi) -> bool:
    return (lo is None or x >= lo) and (hi is None or x <= hi)


def requirement_met(req: dict, c: Character, ct: Content) -> bool:
    kind = next(k for k in req if k in REQ_KINDS)
    v = req[kind]
    sp = species_of(c, ct)
    if kind == "stat":
        return _in_range(effective_stat(c, ct, v), req.get("min"), req.get("max"))
    if kind == "skill":
        return _in_range(c.skills.get(v, 0.0), req.get("min_level"), req.get("max_level"))
    if kind == "skill_count":
        return sum(1 for lvl in c.skills.values() if lvl >= req.get("min_level", 1)) >= v
    if kind == "max_top_skill":
        return max(c.skills.values(), default=0.0) <= v
    if kind == "trait":
        return v in c.traits
    if kind == "flag":
        return v in c.flags
    if kind == "counter":
        return _in_range(c.counters.get(v, 0), req.get("min"), req.get("max"))
    if kind == "item":
        return c.items.get(v, 0) >= req.get("min", 1)
    if kind == "item_tag":
        have = sum(n for i, n in c.items.items() if i in ct.items and v in ct.items[i].tags)
        return have >= req.get("min", 1)
    if kind == "axis":
        return _in_range(c.axes.get(v, 0), req.get("min"), req.get("max"))
    if kind == "species":
        return c.species_id in _as_list(v)
    if kind == "species_category":
        return sp.category in _as_list(v)
    if kind == "species_tag":
        return any(t in sp.tags for t in _as_list(v))
    if kind == "location_tag":
        return c.location in _as_list(v)
    if kind == "any":
        return any(requirement_met(r, c, ct) for r in v)
    if kind == "all":
        return all(requirement_met(r, c, ct) for r in v)
    if kind == "not":
        return not requirement_met(v, c, ct)
    raise ValueError(f"Unknown requirement kind: {kind}")


def _as_list(v):
    return v if isinstance(v, list) else [v]


def requirements_met(reqs: list[dict], c: Character, ct: Content) -> bool:
    return all(requirement_met(r, c, ct) for r in reqs)


def conditions_met(cond: ConditionsDef, c: Character, ct: Content) -> bool:
    stage = stage_of(c, ct)
    idx = stage_index(ct, stage.id)
    sp = species_of(c, ct)
    if cond.min_age is not None and c.age < cond.min_age:
        return False
    if cond.max_age is not None and c.age > cond.max_age:
        return False
    if cond.min_stage and idx < stage_index(ct, cond.min_stage):
        return False
    if cond.max_stage and idx > stage_index(ct, cond.max_stage):
        return False
    if cond.life_stages and stage.id not in cond.life_stages:
        return False
    if cond.location_tags and c.location not in cond.location_tags:
        return False
    if cond.species and sp.id not in cond.species:
        return False
    if cond.species_categories and sp.category not in cond.species_categories:
        return False
    if cond.species_tags and not all(t in sp.tags for t in cond.species_tags):
        return False
    if not requirements_met(cond.requires_all, c, ct):
        return False
    if cond.requires_any and not any(requirement_met(r, c, ct) for r in cond.requires_any):
        return False
    if any(requirement_met(r, c, ct) for r in cond.excludes):
        return False
    return True


def describe_requirement(req: dict, ct: Content) -> str:
    """Player-facing text for why a choice is locked."""
    kind = next(k for k in req if k in REQ_KINDS)
    v = req[kind]

    def bounds(lo, hi, label):
        if lo is not None and hi is not None:
            return f"{label} {lo:g} to {hi:g}"
        return f"{label} {lo:g}+" if lo is not None else f"{label} {hi:g} or less"

    if kind == "stat":
        return bounds(req.get("min"), req.get("max"), pretty(v))
    if kind == "skill":
        return bounds(req.get("min_level"), req.get("max_level"), pretty(v))
    if kind == "skill_count":
        return f"{v} skills at level {req.get('min_level', 1):g}+"
    if kind == "max_top_skill":
        return f"No skill above {v:g}"
    if kind == "trait":
        return f"Trait: {ct.traits[v].name}"
    if kind == "flag":
        return "A particular past deed"
    if kind == "counter":
        return "A certain life experience"
    if kind == "item":
        return f"Item: {ct.items[v].name}"
    if kind == "item_tag":
        return f"An item ({pretty(v).lower()})"
    if kind == "axis":
        return bounds(req.get("min"), req.get("max"), ct.axes[v].name)
    if kind == "species":
        return "Species: " + " or ".join(ct.species[s].name for s in _as_list(v))
    if kind == "species_category":
        return "Kind: " + " or ".join(_as_list(v))
    if kind == "species_tag":
        return "Ability: " + " or ".join(pretty(t) for t in _as_list(v))
    if kind == "location_tag":
        return "Location: " + " or ".join(pretty(t) for t in _as_list(v))
    if kind == "any":
        return " or ".join(describe_requirement(r, ct) for r in v)
    if kind == "all":
        return " and ".join(describe_requirement(r, ct) for r in v)
    if kind == "not":
        return f"Not ({describe_requirement(v, ct)})"
    return kind


def unmet_requirements_text(reqs: list[dict], c: Character, ct: Content) -> str | None:
    unmet = [describe_requirement(r, ct) for r in reqs if not requirement_met(r, c, ct)]
    return "Requires " + ", ".join(unmet) if unmet else None


# ---------------------------------------------------------------- effect engine

def clamp_stat(c: Character, ct: Content, value: int) -> int:
    return max(1, min(species_of(c, ct).stat_cap, value))


def queue_event(c: Character, event_id: str) -> None:
    if c.pending_event is None:
        c.pending_event = event_id
    elif event_id not in c.queued_events:
        c.queued_events.append(event_id)


def grant_trait(c: Character, ct: Content, trait_id: str, notes: list[str] | None = None) -> bool:
    trait = ct.traits[trait_id]
    if trait_id in c.traits or not requirements_met(trait.prerequisites, c, ct):
        return False
    c.traits.append(trait_id)
    if notes is not None:
        notes.append(f"gained {trait.name}")
    return True


def gain_skill(c: Character, ct: Content, skill: str, amount: float) -> float:
    """Change a skill. Gains are scaled by aptitude; losses are not. Returns the actual change."""
    if amount > 0:
        amount *= skill_aptitude(c, ct, skill)
    before = c.skills.get(skill, 0.0)
    after = round(max(0.0, before + amount), 2)
    c.skills[skill] = after
    return after - before


def _category_target(c: Character, ct: Content, category: str, rng: random.Random) -> str:
    """For 'skill_xp: {magic: 8}': the character's best skill in the category, else a seeded pick."""
    owned = [(c.skills[s], s) for s in ct.categories[category] if c.skills.get(s, 0) > 0]
    if owned:
        return max(owned)[1]
    return rng.choice(sorted(ct.categories[category]))


def apply_effect(effect: dict, c: Character, ct: Content, rng: random.Random, notes: list[str]) -> None:
    if "chance" in effect and rng.random() >= effect["chance"]:
        return
    kind = next(k for k in effect if k != "chance")
    v = effect[kind]

    if kind == "stat_delta":
        harm = 0
        for stat, delta in v.items():
            before = c.stats[stat]
            c.stats[stat] = clamp_stat(c, ct, before + delta)
            if c.stats[stat] != before:
                notes.append(f"{c.stats[stat] - before:+d} {stat}")
                harm += min(0, c.stats[stat] - before)
        if harm:                                                     # setbacks are stressful
            stress = gain_stress(c, -harm * 2)
            if stress:
                notes.append(f"+{stress:.0f} stress")
    elif kind == "skill_xp":
        for key, amount in v.items():
            skill = key if key in ct.skills else _category_target(c, ct, key, rng)
            change = gain_skill(c, ct, skill, amount)
            if change:
                notes.append(f"{change:+.1f} {pretty(skill)}")
    elif kind == "axis_delta":
        for axis, delta in v.items():
            c.axes[axis] = max(-AXIS_LIMIT, min(AXIS_LIMIT, c.axes.get(axis, 0) + delta))
    elif kind == "grant_trait":
        grant_trait(c, ct, v, notes)
    elif kind == "remove_trait":
        if v in c.traits:
            c.traits.remove(v)
            notes.append(f"lost {ct.traits[v].name}")
    elif kind == "flag":
        if v not in c.flags:
            c.flags.append(v)
    elif kind == "clear_flag":
        if v in c.flags:
            c.flags.remove(v)
    elif kind == "counter":
        for name, amount in v.items():
            c.counters[name] = c.counters.get(name, 0) + amount
    elif kind == "grant_item":
        item_id, count = (v["id"], v.get("count", 1)) if isinstance(v, dict) else (v, 1)
        c.items[item_id] = c.items.get(item_id, 0) + count
        notes.append(f"received {ct.items[item_id].name}")
    elif kind == "consume_item":
        count = v.get("count", 1)
        target = v.get("id") or next(
            (i for i, n in c.items.items() if n > 0 and i in ct.items and v["tag"] in ct.items[i].tags), None
        )
        if target and c.items.get(target, 0) > 0:
            c.items[target] = max(0, c.items[target] - count)
            if c.items[target] == 0:
                del c.items[target]
            notes.append(f"used {ct.items[target].name}")
    elif kind == "set_location":
        c.location = v
    elif kind == "health_delta":
        change = change_health(c, ct, v)
        if change:
            notes.append(f"{change:+d} health")
    elif kind == "stress_delta":
        before = c.stress
        if v > 0:
            gain_stress(c, v)
        else:
            c.stress = clamp_vital(c.stress + v)
        change = c.stress - before
        if change:
            notes.append(f"{change:+.0f} stress")
    elif kind == "happiness_delta":
        change = change_happiness(c, v)
        if change:
            notes.append(f"{change:+.0f} happiness")
    elif kind == "die":
        c.alive = False
        c.cause_of_death = v["cause"]
    elif kind == "trigger_event":
        queue_event(c, v)


def apply_effects(effects: list[dict], c: Character, ct: Content, rng: random.Random, notes: list[str]) -> None:
    for effect in effects:
        apply_effect(effect, c, ct, rng, notes)
        if not c.alive:
            break
