"""Inheritance rules: what a child starts with, decided from its parents. Pure: no database (see descendants.py).

Inheritance gives STARTING APTITUDE, never a copy. It works out the inputs (species, base stats, a small head start
in the specialised stats, inherited traits, a talent) and nothing else: the child's id, name, age and record come from
the ordinary NPC factory, and its Power from the one Power system. The numbers live in data/inheritance.yaml, and
docs/INHERITANCE.md explains each rule.

  species    same species gives the same species; one parent gives that parent's; two different species only if the
             pair is listed in `crossings`. Otherwise the parents are refused: nothing assumes species can mix.
  stats      (1 - h) * a normal newborn roll + h * the parents' aptitude, +/- variation. Aptitude is the parent's
             stat without their species bonus. Bounded, then the child's own species modifiers and cap apply.
  skills     a small head start in the specialised stats from the parents' average level, capped.
  traits     only traits that can be present at birth (or are listed), each parent passing one on with a chance set
             by rarity tier.
  talent     each parent's hidden talent may be passed on as dormant potential; otherwise a normal newborn roll.

Reproducible: each part draws from its own stream `random.Random(f"{seed}:inherit:<part>")`, so the same parents and
seed give the same child, and tuning one rule never shifts another's dice.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .character import Character
from .content import DATA_DIR, STATS, Content, ContentError, _stage_first_age, get_content
from .npc import DEFAULT_STAT_RANGE
from .power import PowerConfig, get_power_config
from .rules import requirements_met

INHERITANCE_FILE = DATA_DIR / "inheritance.yaml"
MAX_PARENTS = 2
TIERS = ("common", "uncommon", "rare", "legendary")


class InheritanceError(ValueError):
    """These parents cannot have this child."""


class IncompatibleSpeciesError(InheritanceError):
    pass


class ParentNotEligibleError(InheritanceError):
    pass


class NoParentsError(InheritanceError):
    pass


class InvalidChildAgeError(InheritanceError):
    pass


class DescendantConflictError(InheritanceError):
    pass


# ---------------------------------------------------------------- configuration

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _ordered(label: str, pair: tuple[int, int]) -> None:
    if pair[0] > pair[1]:
        raise ValueError(f"{label} must be [min, max]")


class ParentRules(_Strict):
    min_stage: str = "young_adult"
    forbid_direct_line: bool = True
    cannot_parent: list[str] = []


class Crossing(_Strict):
    parents: tuple[str, str]
    offspring: dict[str, float] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self):
        if self.parents[0] == self.parents[1]:
            raise ValueError("a crossing is between two different species; same-species parents need no entry")
        if any(w <= 0 for w in self.offspring.values()):
            raise ValueError("offspring weights must be above 0")
        return self


class StatRules(_Strict):
    heritability: float = Field(default=0.4, ge=0, le=1)
    variation: int = Field(default=5, ge=0)
    roll_range: tuple[int, int] = DEFAULT_STAT_RANGE
    start_bounds: tuple[int, int] = (10, 75)

    @model_validator(mode="after")
    def _check(self):
        _ordered("roll_range", self.roll_range)
        _ordered("start_bounds", self.start_bounds)
        if self.start_bounds[0] < 1:
            raise ValueError("start_bounds must start at 1 or more")
        return self


class SpecialisedRules(_Strict):
    fraction: float = Field(default=0.10, ge=0, le=1)
    min_parent_level: float = Field(default=10, ge=0)
    max_start: float = Field(default=10, ge=0)
    variation: float = Field(default=0.25, ge=0, le=1)


class TraitRules(_Strict):
    extra: list[str] = []
    exclude: list[str] = []
    chance: dict[str, float] = {"common": 0.30, "uncommon": 0.20, "rare": 0.10, "legendary": 0.02}
    overrides: dict[str, float] = {}
    max_inherited: int = Field(default=2, ge=0)

    @model_validator(mode="after")
    def _check(self):
        bad = [t for t in self.chance if t not in TIERS]
        if bad:
            raise ValueError(f"chance is by rarity tier ({', '.join(TIERS)}); unknown {bad}")
        if any(not 0 <= p <= 1 for p in [*self.chance.values(), *self.overrides.values()]):
            raise ValueError("trait chances must be between 0 and 1")
        return self


class TalentRules(_Strict):
    base_chance: float = Field(default=0.01, ge=0, le=1)
    inherit_chance: float = Field(default=0.10, ge=0, le=1)


class InheritanceRules(_Strict):
    parents: ParentRules = Field(default_factory=ParentRules)
    crossings: list[Crossing] = []
    stats: StatRules = Field(default_factory=StatRules)
    specialised: SpecialisedRules = Field(default_factory=SpecialisedRules)
    traits: TraitRules = Field(default_factory=TraitRules)
    talent: TalentRules = Field(default_factory=TalentRules)


def _cross_check(rules: InheritanceRules, ct: Content, cfg: PowerConfig, name: str) -> list[str]:
    errs: list[str] = []
    if rules.parents.min_stage not in ct.stage_ids:
        errs.append(f"{name}: parents.min_stage '{rules.parents.min_stage}' is not a life stage")
    errs += [f"{name}: parents.cannot_parent: unknown species '{s}'" for s in rules.parents.cannot_parent if s not in ct.species]
    seen: set[frozenset] = set()
    for i, c in enumerate(rules.crossings):
        errs += [f"{name}: crossings[{i}]: unknown species '{s}'" for s in (*c.parents, *c.offspring) if s not in ct.species]
        key = frozenset(c.parents)
        if key in seen:
            errs.append(f"{name}: crossings[{i}]: {sorted(key)} is listed twice")
        seen.add(key)
    named = [*rules.traits.extra, *rules.traits.exclude, *rules.traits.overrides]
    errs += [f"{name}: traits: unknown trait '{t}'" for t in named if t not in ct.traits]
    return errs


def load_inheritance_rules(ct: Content | None = None, cfg: PowerConfig | None = None,
                           path: Path = INHERITANCE_FILE) -> InheritanceRules:
    ct, cfg = ct or get_content(), cfg or get_power_config()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ContentError([f"{path.name}: could not be read: {e}"])
    try:
        rules = InheritanceRules.model_validate(raw)
    except ValidationError as e:
        raise ContentError([f"{path.name}: {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()])
    errs = _cross_check(rules, ct, cfg, path.name)
    if errs:
        raise ContentError(errs)
    return rules


@lru_cache
def get_inheritance_rules() -> InheritanceRules:
    return load_inheritance_rules()


# ---------------------------------------------------------------- parents

@dataclass(frozen=True)
class ParentSource:
    """A parent as the rules see them: a stable reference plus a Character (an NPC through npc.to_character, or the
    playable character), so one set of rules serves both."""
    ref: str
    character: Character


def adult_age(species_id: str, rules: InheritanceRules, ct: Content) -> int:
    stage = next(s for s in ct.stages if s.id == rules.parents.min_stage)
    return _stage_first_age(ct.species[species_id], stage)


def offspring_species(parent_species: Sequence[str], rules: InheritanceRules) -> dict[str, float]:
    """The species the child could be, with weights. Raises IncompatibleSpeciesError for a pair nobody listed."""
    unique = list(dict.fromkeys(parent_species))
    if len(unique) == 1:
        return {unique[0]: 1.0}
    pair = frozenset(unique)
    for crossing in rules.crossings:
        if frozenset(crossing.parents) == pair:
            return dict(crossing.offspring)
    raise IncompatibleSpeciesError(f"{' and '.join(sorted(pair))} cannot have children together: no crossing is listed")


def check_parents(parents: Sequence[ParentSource], rules: InheritanceRules, ct: Content) -> None:
    """Everything that can be checked without a database. Raises an InheritanceError."""
    if not parents:
        raise NoParentsError("a child needs at least one available parent")
    if len(parents) > MAX_PARENTS:
        raise ParentNotEligibleError(f"a child has at most {MAX_PARENTS} parents")
    if len({p.ref for p in parents}) != len(parents):
        raise ParentNotEligibleError("the same character was given as both parents")
    for p in parents:
        c = p.character
        if c.species_id in rules.parents.cannot_parent:
            raise ParentNotEligibleError(f"{c.name} is a {c.species_id}, and that species cannot be a parent")
        if c.age < adult_age(c.species_id, rules, ct):
            raise ParentNotEligibleError(f"{c.name} is too young to be a parent (needs to be {rules.parents.min_stage})")
    offspring_species([p.character.species_id for p in parents], rules)          # raises if incompatible


# ---------------------------------------------------------------- the plan

@dataclass
class InheritanceReport:
    """What was inherited and from whom, for logging, tests and later UI. Not stored on the character."""
    parents: list[str]
    species_options: dict[str, float]
    aptitude: dict[str, float | None]                       # parents' average aptitude per base stat (None: unknown)
    skill_parent_levels: dict[str, float] = field(default_factory=dict)
    trait_sources: dict[str, list[str]] = field(default_factory=dict)       # inherited trait -> parents that passed it
    talent_source: str | None = None                        # the parent whose talent was passed on, if any


@dataclass
class InheritancePlan:
    species_id: str
    stats: dict[str, int]
    combat: dict[str, float]                                # specialised stat id -> starting level
    traits: list[str]                                       # inherited only; the species adds its own
    talent: str | None                                      # an inherited hidden talent, else None
    talent_chance: float                                    # the newborn roll to make when nothing was inherited
    report: InheritanceReport


def _stream(seed: int, part: str) -> random.Random:
    return random.Random(f"{seed}:inherit:{part}")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _aptitudes(parents: Sequence[ParentSource], ct: Content) -> dict[str, float | None]:
    """Each stat without the parent's species bonus, averaged over the parents who have it."""
    out: dict[str, float | None] = {}
    for stat in STATS:
        values = [p.character.stats[stat] - ct.species[p.character.species_id].stat_modifiers.get(stat, 0)
                  for p in parents if stat in p.character.stats]
        out[stat] = sum(values) / len(values) if values else None
    return out


def _plan_stats(species_id: str, aptitude: dict[str, float | None], rules: StatRules, seed: int,
                ct: Content) -> dict[str, int]:
    sp, rng = ct.species[species_id], _stream(seed, "stats")
    h, (lo, hi) = rules.heritability, rules.start_bounds
    out = {}
    for stat in STATS:                                          # both dice are always drawn, so the stream is stable
        roll, noise = rng.randint(*rules.roll_range), rng.randint(-rules.variation, rules.variation)
        apt = aptitude[stat]
        base = roll if apt is None else (1 - h) * roll + h * apt
        neutral = _clamp(round(base + noise), lo, hi)
        out[stat] = int(_clamp(neutral + sp.stat_modifiers.get(stat, 0), 1, sp.stat_cap))
    return out


def _plan_skills(species_id: str, parents: Sequence[ParentSource], rules: SpecialisedRules, seed: int,
                 ct: Content, cfg: PowerConfig) -> tuple[dict[str, float], dict[str, float]]:
    sp, rng = ct.species[species_id], _stream(seed, "skills")
    start: dict[str, float] = {}
    parent_levels: dict[str, float] = {}
    for stat, d in cfg.specialised_stats.items():
        mean = sum(sum(p.character.skills.get(s, 0.0) for s in d.skills) for p in parents) / len(parents)
        jitter = rng.uniform(1 - rules.variation, 1 + rules.variation)
        parent_levels[stat] = round(mean, 1)
        can_train = sp.allowed_skills is None or d.skills[0] in sp.allowed_skills
        if mean < rules.min_parent_level or not can_train:
            continue
        level = round(min(rules.max_start, rules.fraction * mean * jitter), 1)
        if level > 0:
            start[stat] = level
    return start, parent_levels


def _inheritable_traits(rules: TraitRules, ct: Content) -> list[str]:
    return sorted(t for t, d in ct.traits.items()
                  if (d.birth is not None or t in rules.extra) and t not in rules.exclude)


def _plan_traits(species_id: str, parents: Sequence[ParentSource], rules: TraitRules, seed: int,
                 ct: Content) -> dict[str, list[str]]:
    rng = _stream(seed, "traits")
    own = set(ct.species[species_id].innate_traits)
    passed: dict[str, list[str]] = {}
    for trait in _inheritable_traits(rules, ct):
        if trait in own:
            continue
        chance = rules.overrides.get(trait, rules.chance.get(ct.traits[trait].tier, 0.0))
        for p in parents:
            if trait in p.character.traits and rng.random() < chance:
                passed.setdefault(trait, []).append(p.ref)
    if len(passed) > rules.max_inherited:
        keep = set(rng.sample(sorted(passed), rules.max_inherited))
        passed = {t: refs for t, refs in passed.items() if t in keep}
    return passed


def _plan_talent(child: Character, parents: Sequence[ParentSource], rules: TalentRules, seed: int,
                 ct: Content) -> tuple[str | None, str | None]:
    rng = _stream(seed, "talent")
    offered = []
    for p in parents:
        talent = p.character.hidden_talent
        if talent in ct.talents and rng.random() < rules.inherit_chance:
            offered.append((talent, p.ref))
    offered = [(t, ref) for t, ref in offered if requirements_met(ct.talents[t].eligible, child, ct)]
    if not offered:
        return None, None
    weights = [ct.talents[t].weight for t, _ in offered]
    return rng.choices(offered, weights)[0]


def plan_inheritance(parents: Sequence[ParentSource], seed: int, *, age: int = 0,
                     rules: InheritanceRules | None = None, ct: Content | None = None,
                     cfg: PowerConfig | None = None) -> InheritancePlan:
    """Decide what a child of these parents starts with. Validates the parents first. No randomness beyond `seed`."""
    rules, ct, cfg = rules or get_inheritance_rules(), ct or get_content(), cfg or get_power_config()
    check_parents(parents, rules, ct)
    options = offspring_species([p.character.species_id for p in parents], rules)
    species = _stream(seed, "species").choices(sorted(options), [options[s] for s in sorted(options)])[0]

    aptitude = _aptitudes(parents, ct)
    stats = _plan_stats(species, aptitude, rules.stats, seed, ct)
    combat, parent_levels = _plan_skills(species, parents, rules.specialised, seed, ct, cfg)
    trait_sources = _plan_traits(species, parents, rules.traits, seed, ct)
    traits = list(trait_sources)
    probe = Character(name="child", species_id=species, age=age, stats=stats, traits=traits)
    talent, talent_from = _plan_talent(probe, parents, rules.talent, seed, ct)

    return InheritancePlan(
        species_id=species, stats=stats, combat=combat, traits=traits, talent=talent,
        talent_chance=0.0 if talent else rules.talent.base_chance,
        report=InheritanceReport(parents=[p.ref for p in parents], species_options=options, aptitude=aptitude,
                                 skill_parent_levels=parent_levels, trait_sources=trait_sources,
                                 talent_source=talent_from))
