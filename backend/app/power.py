"""Power: a derived rating of how strong a character is. Pure calculation, no database and no UI.

    Final Power = (Stats Power + Equipment Power + Skill Power) x (1 + Total Modifier)

Power is never stored: it is recomputed from the character's current state every time it is asked for,
so it cannot drift out of date. The result is a structured breakdown (every line, its source, every
intermediate value and the formula as text) that the frontend renders without doing any arithmetic.

Stats Power comes only from SPECIALISED stats (swordsmanship, marksmanship, magic, martial arts, ...), each
read from the character's existing skill levels. The five base stats (strength, dexterity, constitution, wisdom,
charisma) are for event checks and never contribute to Power.

Extending: each part of the formula comes from a *provider*, a function
    (character, content, config) -> list[PowerLine].
Replace or add one in `PowerProviders` to bring in equipment, skill bonuses or new modifier sources; the
formula, de-duplication and breakdown stay the same. See docs/POWER.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .character import Character
from .content import DATA_DIR, STATS, Content, ContentError, get_content
from .rules import pretty

POWER_FILE = DATA_DIR / "power.yaml"
DECIMALS = 2          # components and the final Power are rounded to this many places
MOD_DECIMALS = 4      # percentage modifiers are decimals (0.1 = +10%), rounded to this many places


# ---------------------------------------------------------------- configuration

class SpecialisedStatDef(BaseModel):
    """A combat proficiency that adds to Power: its value is the sum of the listed skills' levels."""
    model_config = ConfigDict(extra="forbid")
    name: str
    purpose: str = ""
    weight: float = Field(default=1.0, ge=0)        # Power per point; 1.0 is 1:1
    skills: list[str] = Field(min_length=1)


class PowerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")      # a typo'd key (or the old `stat_weights`) is an error
    specialised_stats: dict[str, SpecialisedStatDef] = Field(default_factory=dict)
    skill_power: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _no_skill_counted_twice(self):
        owner: dict[str, str] = {}
        for stat_id, d in self.specialised_stats.items():
            for skill in d.skills:
                if owner.get(skill) == stat_id:
                    raise ValueError(f"skill '{skill}' is listed twice in '{stat_id}': it would be counted twice")
                if skill in owner:
                    raise ValueError(f"skill '{skill}' feeds both '{owner[skill]}' and '{stat_id}': "
                                     "it would be counted twice")
                owner[skill] = stat_id
        for skill in self.skill_power:
            if skill in owner:
                raise ValueError(f"skill '{skill}' feeds the specialised stat '{owner[skill]}', so it cannot "
                                 "also be in skill_power (it would be counted twice)")
        return self


def load_power_config(content: Content | None = None, path: Path = POWER_FILE) -> PowerConfig:
    """Load and validate power.yaml. A missing file means defaults (no stat contributes, nothing else)."""
    errors: list[str] = []
    raw: object = {}
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as e:
            raise ContentError([f"{path.name}: could not be read: {e}"])
    try:
        cfg = PowerConfig.model_validate(raw)
    except ValidationError as e:
        raise ContentError([f"{path.name}: {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()])
    for stat_id, d in cfg.specialised_stats.items():
        if stat_id in STATS:
            errors.append(f"{path.name}: specialised_stats.{stat_id}: '{stat_id}' is a base stat; "
                          "base stats are for checks and do not add Power")
        if content is not None:
            for skill in d.skills:
                if skill not in content.skills:
                    errors.append(f"{path.name}: specialised_stats.{stat_id}: unknown skill '{skill}'")
    if content is not None:
        for skill in cfg.skill_power:
            if skill not in content.skills:
                errors.append(f"{path.name}: skill_power: unknown skill '{skill}'")
    if errors:
        raise ContentError(errors)
    return cfg


@lru_cache
def get_power_config() -> PowerConfig:
    return load_power_config(get_content())


# ---------------------------------------------------------------- the breakdown (also the API response)

class PowerLine(BaseModel):
    """One contribution to a component or to the modifier total, with where it came from."""
    source_type: str                     # "stat" | "skill" | "equipment" | "trait" | ...
    source_id: str
    label: str
    value: float                         # raw Power for a component line; a decimal (0.1 = +10%) for a modifier
    detail: str = ""                     # human-readable derivation, e.g. "46 x 0.75"
    inputs: dict[str, float] = Field(default_factory=dict)      # the numbers behind it
    sources: list[str] = Field(default_factory=list)            # finer attribution, e.g. "Iron Constitution +6"


class PowerComponent(BaseModel):
    key: str
    label: str
    total: float
    implemented: bool                    # False: this part of the system does not exist yet, so it is 0
    note: str = ""
    lines: list[PowerLine] = Field(default_factory=list)


class PowerModifiers(BaseModel):
    total: float                         # sum of the lines, as a decimal
    multiplier: float                    # 1 + total, never below 0
    percent_text: str                    # e.g. "+30%"
    note: str = ""
    lines: list[PowerLine] = Field(default_factory=list)


class PowerFormula(BaseModel):
    general: str                         # the formula with names
    substituted: str                     # the same with this character's numbers
    result: str                          # the arithmetic to the final value


class PowerBreakdown(BaseModel):
    total: float                         # Final Power
    subtotal: float                      # Stats + Equipment + Skill, before modifiers
    stats: PowerComponent
    equipment: PowerComponent
    skills: PowerComponent
    modifiers: PowerModifiers
    formula: PowerFormula
    notes: list[str] = Field(default_factory=list)      # e.g. duplicate contributions that were ignored


# ---------------------------------------------------------------- providers

Provider = Callable[[Character, Content, PowerConfig], list[PowerLine]]


def stat_lines(c: Character, ct: Content, cfg: PowerConfig) -> list[PowerLine]:
    """One line per specialised stat: weight x value, where the value is the sum of its skills' levels.
    Base stats are deliberately not read here, so they can never add Power."""
    lines = []
    for stat_id, d in cfg.specialised_stats.items():
        if not d.weight:
            continue
        parts = [(skill, c.skills.get(skill, 0.0)) for skill in dict.fromkeys(d.skills)]
        value = round(sum(level for _, level in parts), DECIMALS)
        lines.append(PowerLine(
            source_type="specialised_stat", source_id=stat_id, label=d.name,
            value=round(d.weight * value, DECIMALS), detail=f"{fmt(value)} × {fmt(d.weight)}",
            inputs={"value": value, "weight": d.weight},
            sources=[f"{pretty(skill)} {fmt(level)}" for skill, level in parts if level > 0]))
    return lines


def equipment_lines(c: Character, ct: Content, cfg: PowerConfig) -> list[PowerLine]:
    """No equipment system exists yet (items are inventory counts, nothing is 'equipped'), so: nothing."""
    return []


def skill_lines(c: Character, ct: Content, cfg: PowerConfig) -> list[PowerLine]:
    """Raw Power for each unlocked skill (level > 0) that has an entry in power.yaml `skill_power`."""
    lines = []
    for skill in sorted(cfg.skill_power):
        level = c.skills.get(skill, 0)
        if level > 0 and cfg.skill_power[skill]:
            lines.append(PowerLine(source_type="skill", source_id=skill, label=pretty(skill),
                                   value=round(cfg.skill_power[skill], DECIMALS), detail=f"unlocked, level {level:g}",
                                   inputs={"level": level}))
    return lines


def modifier_lines(c: Character, ct: Content, cfg: PowerConfig) -> list[PowerLine]:
    """Percentage modifiers, as decimals. Today: a trait's explicit `modifiers.power`. A trait's stat
    bonuses are NOT here (they are already inside Stats Power), so nothing is counted twice."""
    lines = []
    for trait_id in dict.fromkeys(c.traits):                 # each trait once
        trait = ct.traits.get(trait_id)
        if trait and trait.modifiers.power:
            lines.append(PowerLine(source_type="trait", source_id=trait_id, label=trait.name,
                                   value=round(trait.modifiers.power, MOD_DECIMALS),
                                   detail=f"{trait.modifiers.power:+.0%} Power"))
    return lines


@dataclass
class PowerProviders:
    stats: Provider = stat_lines
    equipment: Provider = equipment_lines
    skills: Provider = skill_lines
    modifiers: Provider = modifier_lines


# ---------------------------------------------------------------- calculation

def fmt(x: float) -> str:
    """Compact number: 156.0 -> '156', 1.3 -> '1.3', 33.333 -> '33.33'."""
    return f"{x:.{DECIMALS}f}".rstrip("0").rstrip(".") if x else "0"


def _dedupe(lines: list[PowerLine], where: str, notes: list[str]) -> list[PowerLine]:
    """A source may contribute to a part of the formula once; repeats are dropped and reported."""
    seen: dict[tuple[str, str], PowerLine] = {}
    for line in lines:
        key = (line.source_type, line.source_id)
        if key in seen:
            notes.append(f"Ignored a duplicate {where} contribution from {line.label} ({line.source_type}).")
            continue
        seen[key] = line
    return list(seen.values())


def calculate_power(c: Character, ct: Content | None = None, cfg: PowerConfig | None = None,
                    providers: PowerProviders | None = None) -> PowerBreakdown:
    """Derive Power from the character's current state. Nothing is read from or written to storage."""
    ct = ct or get_content()
    cfg = cfg if cfg is not None else get_power_config()
    providers = providers or PowerProviders()
    notes: list[str] = []

    def component(key: str, label: str, provider: Provider, implemented: bool, note: str) -> PowerComponent:
        lines = _dedupe(provider(c, ct, cfg), label, notes)
        return PowerComponent(key=key, label=label, total=round(sum(l.value for l in lines), DECIMALS),
                              implemented=implemented, note=note, lines=lines)

    stats = component("stats", "Stats Power", providers.stats, True,
                      "Specialised stats only (combat proficiencies), each counted at its weight. "
                      "Base stats (Strength, Dexterity, Constitution, Wisdom, Charisma) are for event checks "
                      "and do not add Power.")
    equipment_impl = providers.equipment is not equipment_lines
    equipment = component("equipment", "Equipment Power", providers.equipment, equipment_impl,
                          "" if equipment_impl else "There is no equipment system yet, so this is 0.")
    skills_impl = providers.skills is not skill_lines or bool(cfg.skill_power)
    skills = component("skills", "Skill Power", providers.skills, skills_impl,
                       "" if skills_impl else "No skill grants Power yet, so this is 0.")

    mod_lines = _dedupe(providers.modifiers(c, ct, cfg), "modifier", notes)
    mod_total = round(sum(l.value for l in mod_lines), MOD_DECIMALS)          # additive: +10% and +20% = +30%
    multiplier = round(max(0.0, 1 + mod_total), MOD_DECIMALS)
    modifiers = PowerModifiers(
        total=mod_total, multiplier=multiplier, percent_text=f"{mod_total:+.0%}" if mod_total else "+0%",
        note="" if mod_lines else "No modifiers apply, so the multiplier is 1.", lines=mod_lines)
    if 1 + mod_total < 0:
        notes.append("The modifiers total below -100%; the multiplier is held at 0 (Power cannot be negative).")

    subtotal = round(stats.total + equipment.total + skills.total, DECIMALS)
    total = round(subtotal * multiplier, DECIMALS)
    formula = PowerFormula(
        general="Final Power = (Stats Power + Equipment Power + Skill Power) × (1 + Total Modifier)",
        substituted=f"Final Power = ({fmt(stats.total)} + {fmt(equipment.total)} + {fmt(skills.total)}) × (1 + {fmt(mod_total)})",
        result=f"= {fmt(subtotal)} × {fmt(multiplier)} = {fmt(total)}")
    return PowerBreakdown(total=total, subtotal=subtotal, stats=stats, equipment=equipment, skills=skills,
                          modifiers=modifiers, formula=formula, notes=notes)
