"""NPC progression: age-aware development, yearly growth and life-experience hooks. Pure: no database (see
npc_world.py for the yearly tick over saved NPCs).

Two separate things, so growth is never applied twice:

  birth        base stats rolled once (npc._roll_stats, or inherited from parents). Newborn-level, any species.
  development  growth from birth up to the NPC's age. `initial_development` does this ONCE when an NPC is generated
               at an age above 0, at a reduced chance (data/npc_growth.yaml `initial`). It records
               `progress.developed_age`.
  yearly       `catch_up` applies growth for each age above `progress.developed_age`, one year at a time, and moves
               `developed_age` forward. A year already developed is never developed again, so retries, reloads and
               a second call for the same year change nothing.

Every year draws from its own stream, random.Random(f"{seed}:npc:growth:{age}") (initial development uses
`:development:{age}`), and always draws the same number of dice, so the same saved state and seed replay identically.

Base stats and specialised stats stay distinct: base stats grow through `grow_base_stats`, specialised stats (skills
mapped by power.yaml) through `grow_specialised`. Power is calculated by the existing Power system from the result.

Life experiences are modifiers looked up by id in the config (`experiences`), so a school, a mentor or a profession
only has to be given an id and an entry in npc_growth.yaml: `add_experience(npc, id)`. `apply_life_event` grants a
one-off reward (stats, traits, experience) exactly once per event key.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .content import DATA_DIR, STATS, Content, ContentError, get_content
from .npc import Npc, NpcProgress, ensure_all, _adapter
from .power import PowerConfig, get_power_config
from .rules import grant_trait, requirements_met, stage_at

GROWTH_FILE = DATA_DIR / "npc_growth.yaml"
KEY = re.compile(r"^[a-z][a-z0-9_.:]*$")


class ProgressionError(ValueError):
    """A progression request that makes no sense (unknown experience, trait, stat...)."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- configuration

def _pair(label: str, pair, *, lo=0):
    if not lo <= pair[0] <= pair[1]:
        raise ValueError(f"{label} must be [min, max] with {lo} <= min <= max")


class StatModifier(_Strict):
    chance: float = Field(default=1.0, ge=0)
    amount: float = Field(default=1.0, ge=0)
    breakthrough: float = Field(default=1.0, ge=0)
    cap: int = 0


class GrowthModifier(StatModifier):
    stats: dict[str, StatModifier] = {}


class Experience(GrowthModifier):
    trains: list[str] = []                          # specialised stats this experience lets grow from zero


class Breakthrough(_Strict):
    chance: float = Field(ge=0, le=1)
    percent: tuple[float, float]
    max_amount: int = Field(ge=1)
    max_per_stat: int = Field(ge=0)

    @model_validator(mode="after")
    def _check(self):
        _pair("percent", self.percent)
        return self


class SpecialisedGrowth(_Strict):
    chance: float = Field(default=0.0, ge=0, le=1)
    gain: tuple[float, float] = (0.5, 1.5)

    @model_validator(mode="after")
    def _check(self):
        _pair("gain", self.gain)
        return self


class Decline(_Strict):
    chance: float = Field(ge=0, le=1)
    amount: tuple[int, int] = (1, 1)
    stats: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self):
        _pair("amount", self.amount, lo=1)
        return self


class Profile(_Strict):
    stages: list[str] = Field(min_length=1)
    stat_chance: float = Field(ge=0, le=1)
    small: tuple[int, int] = (1, 2)
    breakthrough: Breakthrough
    specialised: SpecialisedGrowth = Field(default_factory=SpecialisedGrowth)
    decline: Decline | None = None

    @model_validator(mode="after")
    def _check(self):
        _pair("small", self.small, lo=1)
        return self


class Caps(_Strict):
    default: int = Field(default=85, ge=1)
    taper_from: float = Field(default=0.7, ge=0, lt=1)
    stats: dict[str, int] = {}
    species: dict[str, int] = {}
    specialised: dict[str, float] = {"default": 60.0}


class Aging(_Strict):
    die_of_old_age: bool = True


class TraitAcquisition(_Strict):
    enabled: bool = True
    chance_scale: float = Field(default=0.5, ge=0, le=1)


class Initial(_Strict):
    chance_scale: float = Field(default=0.8, ge=0, le=1)


class GrowthConfig(_Strict):
    profiles: dict[str, Profile] = Field(min_length=1)
    caps: Caps = Field(default_factory=Caps)
    aging: Aging = Field(default_factory=Aging)
    trait_acquisition: TraitAcquisition = Field(default_factory=TraitAcquisition)
    initial: Initial = Field(default_factory=Initial)
    stat_rates: dict[str, StatModifier] = {}
    species: dict[str, GrowthModifier] = {}
    traits: dict[str, GrowthModifier] = {}
    talents: dict[str, GrowthModifier] = {}
    experiences: dict[str, Experience] = {}

    def profile_for(self, stage_id: str) -> Profile | None:
        return next((p for p in self.profiles.values() if stage_id in p.stages), None)


def _cross_check(cfg: GrowthConfig, ct: Content, pcfg: PowerConfig, name: str) -> list[str]:
    errs: list[str] = []
    seen: dict[str, str] = {}
    for pid, p in cfg.profiles.items():
        for s in p.stages:
            if s not in ct.stage_ids:
                errs.append(f"{name}: profiles.{pid}: '{s}' is not a life stage")
            elif s in seen:
                errs.append(f"{name}: stage '{s}' is in both '{seen[s]}' and '{pid}'")
            seen[s] = pid
        errs += [f"{name}: profiles.{pid}.decline: unknown stat '{s}'" for s in (p.decline.stats if p.decline else []) if s not in STATS]
    errs += [f"{name}: stage '{s}' has no growth profile" for s in ct.stage_ids if s not in seen]

    def stat_keys(label: str, mod: GrowthModifier):
        return [f"{name}: {label}: unknown stat '{s}'" for s in mod.stats if s not in STATS]

    errs += [f"{name}: caps.stats: unknown stat '{s}'" for s in cfg.caps.stats if s not in STATS]
    errs += [f"{name}: caps.species: unknown species '{s}'" for s in cfg.caps.species if s not in ct.species]
    errs += [f"{name}: caps.specialised: unknown specialised stat '{s}'"
             for s in cfg.caps.specialised if s != "default" and s not in pcfg.specialised_stats]
    errs += [f"{name}: stat_rates: unknown stat '{s}'" for s in cfg.stat_rates if s not in STATS]
    for label, table, known in (("species", cfg.species, ct.species), ("traits", cfg.traits, ct.traits),
                                ("talents", cfg.talents, ct.talents)):
        for key, mod in table.items():
            if key not in known:
                errs.append(f"{name}: {label}: unknown id '{key}'")
            errs += stat_keys(f"{label}.{key}", mod)
    for key, exp in cfg.experiences.items():
        if not KEY.match(key):
            errs.append(f"{name}: experiences: id '{key}' must be lowercase letters, digits, '_', '.' or ':'")
        errs += stat_keys(f"experiences.{key}", exp)
        errs += [f"{name}: experiences.{key}.trains: unknown specialised stat '{s}'"
                 for s in exp.trains if s not in pcfg.specialised_stats]
    return errs


def load_growth_config(ct: Content | None = None, pcfg: PowerConfig | None = None,
                       path: Path = GROWTH_FILE) -> GrowthConfig:
    ct, pcfg = ct or get_content(), pcfg or get_power_config()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ContentError([f"{path.name}: could not be read: {e}"])
    try:
        cfg = GrowthConfig.model_validate(raw)
    except ValidationError as e:
        raise ContentError([f"{path.name}: {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()])
    errs = _cross_check(cfg, ct, pcfg, path.name)
    if errs:
        raise ContentError(errs)
    return cfg


@lru_cache
def get_growth_config() -> GrowthConfig:
    return load_growth_config()


# ---------------------------------------------------------------- reports

@dataclass
class YearReport:
    age: int
    stat_changes: dict[str, int] = field(default_factory=dict)          # base stat -> net change
    skill_changes: dict[str, float] = field(default_factory=dict)       # skill -> gain
    breakthroughs: list[str] = field(default_factory=list)              # base stats that broke through
    traits_gained: list[str] = field(default_factory=list)
    died: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.stat_changes or self.skill_changes or self.traits_gained or self.died)


# ---------------------------------------------------------------- modifiers

@dataclass(frozen=True)
class _Mods:
    chance: float
    amount: float
    breakthrough: float
    cap: int


def _sources(npc: Npc, gcfg: GrowthConfig, *, with_life: bool) -> list[GrowthModifier]:
    """Species always; traits, talent and experiences only for an NPC that has them (not at initial development)."""
    out: list[GrowthModifier] = []
    if npc.species_id in gcfg.species:
        out.append(gcfg.species[npc.species_id])
    if with_life:
        out += [gcfg.traits[t] for t in (npc.traits or []) if t in gcfg.traits]
        if npc.talent and npc.talent in gcfg.talents:
            out.append(gcfg.talents[npc.talent])
        if npc.progress is not None:
            out += [gcfg.experiences[e] for e in npc.progress.experiences if e in gcfg.experiences]
    return out


def _mods(stat: str, sources: Iterable[GrowthModifier], gcfg: GrowthConfig) -> _Mods:
    chance = amount = brk = 1.0
    cap = 0
    parts: list[StatModifier] = []
    if stat in gcfg.stat_rates:
        parts.append(gcfg.stat_rates[stat])
    for s in sources:
        parts += [s, s.stats[stat]] if stat in s.stats else [s]
    for m in parts:
        chance, amount, brk, cap = chance * m.chance, amount * m.amount, brk * m.breakthrough, cap + m.cap
    return _Mods(chance, amount, brk, cap)


def stat_cap(npc: Npc, stat: str, gcfg: GrowthConfig, ct: Content, mods: _Mods | None = None) -> int:
    """Where this NPC's stat stops growing: the configured cap (default, per stat or per species) plus modifier bonuses,
    never above the species' hard stat_cap."""
    hard = ct.species[npc.species_id].stat_cap
    base = gcfg.caps.species.get(npc.species_id, gcfg.caps.stats.get(stat, gcfg.caps.default))
    if npc.species_id in gcfg.caps.species and stat in gcfg.caps.stats:
        base = max(base, gcfg.caps.stats[stat])
    return min(hard, base + (mods.cap if mods else 0))


def _taper(value: int, cap: int, gcfg: GrowthConfig) -> float:
    start = gcfg.caps.taper_from * cap
    if value >= cap:
        return 0.0
    if value <= start:
        return 1.0
    return (cap - value) / (cap - start)


def _specialised_cap(stat: str, gcfg: GrowthConfig) -> float:
    return gcfg.caps.specialised.get(stat, gcfg.caps.specialised.get("default", 60.0))


# ---------------------------------------------------------------- growth for one year

def grow_base_stats(stats: dict[str, int], breakthroughs: dict[str, int], npc: Npc, age: int, rng: random.Random,
                    ct: Content, gcfg: GrowthConfig, *, scale: float = 1.0, with_life: bool = True) -> tuple[dict[str, int], list[str]]:
    """One year of base-stat growth for `age`. Changes `stats` and `breakthroughs` in place; returns (net change, stats
    that broke through). Always draws the same dice whatever happens, so the stream stays stable."""
    sp = ct.species[npc.species_id]
    profile = gcfg.profile_for(stage_at(sp, age, ct).id)
    sources = _sources(npc, gcfg, with_life=with_life)
    changes: dict[str, int] = {}
    broke: list[str] = []
    for stat in STATS:
        hit, brk, size, drop, drop_size = (rng.random() for _ in range(5))
        if profile is None or stat not in stats:
            continue
        m = _mods(stat, sources, gcfg)
        cap = stat_cap(npc, stat, gcfg, ct, m)
        value, delta = stats[stat], 0
        chance = profile.stat_chance * m.chance * scale * _taper(value, cap, gcfg)
        if hit < chance:
            b = profile.breakthrough
            if brk < min(1.0, b.chance * m.breakthrough) and breakthroughs.get(stat, 0) < b.max_per_stat:
                pct = b.percent[0] + (b.percent[1] - b.percent[0]) * size
                delta = min(b.max_amount, max(1, round(value * pct * m.amount)))
                breakthroughs[stat] = breakthroughs.get(stat, 0) + 1
                broke.append(stat)
            else:
                lo, hi = profile.small
                delta = max(1, round((lo + (hi - lo) * size) * m.amount)) if m.amount > 0 else 0
            delta = max(0, min(delta, cap - value))
        if profile.decline and stat in profile.decline.stats and drop < profile.decline.chance:
            lo, hi = profile.decline.amount
            delta -= round(lo + (hi - lo) * drop_size)
        new = max(1, min(sp.stat_cap, value + delta))
        if new != value:
            stats[stat] = new
            changes[stat] = new - value
    return changes, broke


def grow_specialised(skills: dict[str, float], npc: Npc, age: int, rng: random.Random, ct: Content,
                     gcfg: GrowthConfig, pcfg: PowerConfig) -> dict[str, float]:
    """One year of growth in the specialised stats (stored as skills). Only stats the NPC already trains, or an
    experience trains, and only skills the species may train. Returns skill -> gain."""
    sp = ct.species[npc.species_id]
    profile = gcfg.profile_for(stage_at(sp, age, ct).id)
    trained_by = {s for e in (npc.progress.experiences if npc.progress else []) if e in gcfg.experiences
                  for s in gcfg.experiences[e].trains}
    gains: dict[str, float] = {}
    for stat, d in sorted(pcfg.specialised_stats.items()):
        hit, size = rng.random(), rng.random()
        if profile is None:
            continue
        skill = d.skills[0]
        if sp.allowed_skills is not None and skill not in sp.allowed_skills:
            continue
        level = sum(skills.get(k, 0.0) for k in d.skills)
        if level <= 0 and stat not in trained_by:
            continue
        m = _mods(stat, _sources(npc, gcfg, with_life=True), gcfg)         # only chance/amount of non-stat entries
        cap = _specialised_cap(stat, gcfg)
        if level >= cap or hit >= min(1.0, profile.specialised.chance * m.chance):
            continue
        lo, hi = profile.specialised.gain
        gain = round(min(cap - level, (lo + (hi - lo) * size) * m.amount), 1)
        if gain > 0:
            skills[skill] = round(skills.get(skill, 0.0) + gain, 2)
            gains[skill] = gain
    return gains


def _acquire_traits(npc: Npc, age: int, ct: Content, gcfg: GrowthConfig) -> list[str]:
    """traits.yaml `acquire` rules, once a year, through the normal grant_trait (prerequisites apply)."""
    cfg = gcfg.trait_acquisition
    rng = random.Random(f"{npc.seed}:npc:acquire:{age}")
    c = _adapter(npc, ct)
    gained = []
    for trait_id in sorted(ct.traits):
        trait = ct.traits[trait_id]
        if trait_id in c.traits:
            continue
        for rule in trait.acquire:
            roll = rng.random()
            if requirements_met(rule.requirements, c, ct) and roll < rule.chance * cfg.chance_scale:
                if grant_trait(c, ct, trait_id):
                    gained.append(trait_id)
                break
    npc.traits = c.traits
    return gained


def _stream(npc: Npc, kind: str, age: int) -> random.Random:
    return random.Random(f"{npc.seed}:npc:{kind}:{age}")


def _progress(npc: Npc) -> NpcProgress:
    if npc.progress is None:
        npc.progress = NpcProgress(developed_age=npc.age)
    return npc.progress


# ---------------------------------------------------------------- initial development and yearly catch-up

def initial_development(birth_stats: dict[str, int], npc: Npc, ct: Content | None = None,
                        gcfg: GrowthConfig | None = None) -> tuple[dict[str, int], dict[str, int]]:
    """Newborn base stats developed to `npc.age`, once. Uses species modifiers only (the NPC's traits and talent are
    not known yet), a reduced chance, and the same caps. Returns (stats, breakthroughs). Pure: touches nothing."""
    ct, gcfg = ct or get_content(), gcfg or get_growth_config()
    stats, breakthroughs = dict(birth_stats), {}
    for age in range(1, npc.age + 1):
        grow_base_stats(stats, breakthroughs, npc, age, random.Random(f"{npc.seed}:npc:development:{age}"), ct, gcfg,
                        scale=gcfg.initial.chance_scale, with_life=False)
    return stats, breakthroughs


def catch_up(npc: Npc, ct: Content | None = None, gcfg: GrowthConfig | None = None,
             pcfg: PowerConfig | None = None) -> list[YearReport]:
    """Apply growth for every age above `progress.developed_age` up to `npc.age`, once each. Safe to call again."""
    ct, gcfg, pcfg = ct or get_content(), gcfg or get_growth_config(), pcfg or get_power_config()
    if npc.seed is None or not npc.alive:
        return []
    ensure_all(npc, ct, pcfg)
    progress, reports = _progress(npc), []
    for age in range(progress.developed_age + 1, npc.age + 1):
        report = YearReport(age)
        if npc.stats is not None:
            report.stat_changes, report.breakthroughs = grow_base_stats(
                npc.stats, progress.breakthroughs, npc, age, _stream(npc, "growth", age), ct, gcfg)
        if npc.skills is not None:
            report.skill_changes = grow_specialised(npc.skills, npc, age, _stream(npc, "specialised", age), ct, gcfg, pcfg)
        if gcfg.trait_acquisition.enabled and npc.traits is not None:
            report.traits_gained = _acquire_traits(npc, age, ct, gcfg)
        progress.developed_age = age                       # recorded per year, so a failure part-way never repeats a year
        reports.append(report)
    return reports


# ---------------------------------------------------------------- life-experience hooks

def add_experience(npc: Npc, experience_id: str, gcfg: GrowthConfig | None = None) -> bool:
    """Give an NPC a life experience (a school, a mentor, a profession...) defined in npc_growth.yaml. Returns True if
    it was new; repeating it changes nothing."""
    gcfg = gcfg or get_growth_config()
    if experience_id not in gcfg.experiences:
        raise ProgressionError(f"unknown experience '{experience_id}'")
    progress = _progress(npc)
    if experience_id in progress.experiences:
        return False
    progress.experiences = [*progress.experiences, experience_id]
    return True


def remove_experience(npc: Npc, experience_id: str) -> bool:
    progress = _progress(npc)
    if experience_id not in progress.experiences:
        return False
    progress.experiences = [e for e in progress.experiences if e != experience_id]
    return True


@dataclass
class LifeEventResult:
    key: str
    applied: bool                                           # False: this event was already applied, nothing changed
    stat_changes: dict[str, int] = field(default_factory=dict)
    traits_gained: list[str] = field(default_factory=list)
    experience_added: str | None = None


def apply_life_event(npc: Npc, key: str, *, stats: dict[str, int] | None = None, traits: Iterable[str] = (),
                     experience: str | None = None, ct: Content | None = None,
                     gcfg: GrowthConfig | None = None) -> LifeEventResult:
    """A significant life event: a breakthrough (`stats` deltas, bounded by the species cap), new traits (through the
    normal grant_trait, so prerequisites apply) and/or an experience. Applied exactly once per `key`: a retry or a
    reloaded save finds the key in `progress.events` and changes nothing."""
    ct, gcfg = ct or get_content(), gcfg or get_growth_config()
    if not KEY.match(key):
        raise ProgressionError(f"event key '{key}' must be lowercase letters, digits, '_', '.' or ':'")
    bad = [s for s in (stats or {}) if s not in STATS] + [t for t in traits if t not in ct.traits]
    if bad:
        raise ProgressionError(f"unknown stat or trait {bad}")
    if experience is not None and experience not in gcfg.experiences:
        raise ProgressionError(f"unknown experience '{experience}'")
    progress = _progress(npc)
    if key in progress.events:
        return LifeEventResult(key, applied=False)
    ensure_all(npc, ct)
    result = LifeEventResult(key, applied=True)
    cap = ct.species[npc.species_id].stat_cap
    for stat, delta in (stats or {}).items():
        if npc.stats is not None:
            new = max(1, min(cap, npc.stats[stat] + delta))
            if new != npc.stats[stat]:
                result.stat_changes[stat] = new - npc.stats[stat]
                npc.stats[stat] = new
    if traits:
        c = _adapter(npc, ct)
        result.traits_gained = [t for t in traits if grant_trait(c, ct, t)]
        npc.traits = c.traits
    if experience is not None and add_experience(npc, experience, gcfg):
        result.experience_added = experience
    progress.events = [*progress.events, key]
    return result
