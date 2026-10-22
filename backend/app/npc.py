"""NPCs: a shared identity with optional, lazily generated attribute sections. Pure: no database (see npc_store.py).

One model, three categories (`CharacterCategory`):

  generic     temporary NPCs for an encounter. Light by default: identity plus a seed. Their stats, traits and
              combat skills are generated only when something asks for them, and the seed makes that identical
              every time, so a generic NPC never needs to be saved.
  persistent  recurring NPCs, relatives, potential descendants. Fully generated up front, so the values stay
              fixed even if the generation rules are tuned later.
  named       authored lore characters (data/named_characters.yaml). Authored name and id, never randomly
              generated.

Reuse, not duplication. An NPC holds the SAME kinds of data a playable `Character` does (base stats, skills,
traits, hidden talent), and `to_character` turns one into a `Character` so the existing rules apply unchanged:

  * Power:   `npc_power` calls `power.calculate_power`. No Power value is stored and no formula is repeated.
  * The four specialised stats (Magic, Swordsmanship, Marksmanship, Martial Arts) are not separate fields: as
    everywhere else in the game they are skills, mapped to stats by power.yaml (`specialised_values`).
  * Traits come from rules.grant_trait, the hidden talent from engine.roll_hidden_talent.

Identity is `Npc.id`, never the name. Names may repeat; `taken_names` opts in to uniqueness.

Reproducible: every part draws from its own stream, `random.Random(f"{seed}:npc:<part>")` (the convention
engine.new_character uses), so changing one part never shifts another and generating sections in any order, or
later, gives the same result. The same seed gives the same NPC, id included, so use distinct seeds for distinct
NPCs; with no seed one is drawn and recorded on the NPC.

Out of scope here: inheritance, family trees, legacy transitions and named-NPC story hooks.
"""
from __future__ import annotations

import random
import re
import secrets
import uuid
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Collection, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .character import Character
from .content import (
    DATA_DIR, STATS, Content, ContentError, _stage_first_age, get_content,
)
from .engine import roll_hidden_talent
from .names import NameGenerator, get_name_generator
from .power import PowerBreakdown, PowerConfig, calculate_power, get_power_config
from .rules import grant_trait

NAMED_FILE = DATA_DIR / "named_characters.yaml"
ID_PREFIX = {"generic": "npc_", "persistent": "npc_", "named": "named_"}
SLUG = re.compile(r"^[a-z][a-z0-9_]*$")
DEFAULT_STAT_RANGE = (20, 60)       # the range engine.new_character rolls a newborn's base stats in
DEFAULT_COMBAT_LEVEL = (5.0, 25.0)


class CharacterCategory(StrEnum):
    GENERIC = "generic"
    PERSISTENT = "persistent"
    NAMED = "named"


class LifecycleStatus(StrEnum):
    ACTIVE = "active"
    DECEASED = "deceased"        # terminal
    RETIRED = "retired"          # left the story (moved away, dismissed); may return to ACTIVE


class DetailLevel(StrEnum):
    LIGHT = "light"              # identity only; the rest is generated on demand
    FULL = "full"                # every section generated now


CATEGORY_DETAIL = {
    CharacterCategory.GENERIC: DetailLevel.LIGHT,
    CharacterCategory.PERSISTENT: DetailLevel.FULL,
    CharacterCategory.NAMED: DetailLevel.FULL,
}


class NpcSpecError(ValueError):
    """A generation request that makes no sense (unknown species, trait, stat, ...)."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- what to generate

class NpcSpec(_Strict):
    """Everything configurable about generating an NPC. All fields are optional."""
    category: Literal[CharacterCategory.GENERIC, CharacterCategory.PERSISTENT] = CharacterCategory.GENERIC
    species: str | list[str] | None = None          # one species, a pool to choose from, or None for any
    age_range: tuple[int, int] | None = None        # inclusive; None means the adult stages of the species
    detail: DetailLevel | None = None               # None uses the category default
    culture: str | None = None                      # name culture; None follows the species
    # base stats (strength, dexterity, constitution, wisdom, charisma)
    stat_range: tuple[int, int] = DEFAULT_STAT_RANGE
    stat_bonus: dict[str, int] = {}                 # added after the roll, e.g. {strength: 10} for a veteran
    stats: dict[str, int] = {}                      # exact values that replace the roll
    # traits and talent
    traits: list[str] = []                          # always given
    random_traits: int = Field(default=0, ge=0)     # extra traits drawn from those that can be born with
    talent: str | None = None                       # a specific hidden talent
    talent_chance: float = Field(default=0.0, ge=0, le=1)
    # combat: specialised stats (swordsmanship, marksmanship, magic, martial_arts)
    combat: dict[str, float | tuple[float, float]] | None = None    # stat id -> level, or [min, max]
    combat_focus: int = Field(default=1, ge=0)      # with `combat` unset: how many to draw at random
    combat_level: tuple[float, float] = DEFAULT_COMBAT_LEVEL

    @model_validator(mode="after")
    def _ranges(self):
        for label, (lo, hi) in (("stat_range", self.stat_range), ("combat_level", self.combat_level)):
            if not 0 <= lo <= hi:
                raise ValueError(f"{label} must be [min, max] with 0 <= min <= max")
        if self.age_range is not None and not 0 <= self.age_range[0] <= self.age_range[1]:
            raise ValueError("age_range must be [min, max] with 0 <= min <= max")
        return self


# ---------------------------------------------------------------- the NPC

class Npc(_Strict):
    """Shared identity plus optional attribute sections. A section that is None has not been generated yet."""
    id: str
    category: CharacterCategory
    status: LifecycleStatus = LifecycleStatus.ACTIVE
    name: str = Field(min_length=1)
    species_id: str
    age: int = Field(ge=0)
    cause_of_death: str | None = None
    seed: int | None = None                         # lets missing sections be generated, identically, later
    spec: NpcSpec | None = None                     # what was asked for (None for authored characters)
    # optional sections
    stats: dict[str, int] | None = None
    skills: dict[str, float] | None = None
    traits: list[str] | None = None
    talent: str | None = None                       # hidden talent id
    talent_state: Literal["none", "dormant", "awakened"] = "none"
    # Runtime state of an authored (named) character. The definition is never copied here: `definition` only names
    # it, so title, lore and affiliations are always read from the content file (see named.py).
    definition: str | None = None                   # the authored definition's id
    definition_version: int | None = None           # the version this character was created or last synced from
    flags: dict[str, bool | int | float | str] = {}    # NPC-scoped story flags
    location: str | None = None

    @model_validator(mode="after")
    def _consistent(self):
        prefix = ID_PREFIX[self.category.value]
        if not self.id.startswith(prefix) or len(self.id) <= len(prefix):
            raise ValueError(f"a {self.category.value} character's id starts with '{prefix}'")
        if self.stats is not None and set(self.stats) != set(STATS):
            raise ValueError(f"stats must have exactly {sorted(STATS)}")
        return self

    @property
    def alive(self) -> bool:
        return self.status != LifecycleStatus.DECEASED

    @property
    def has_stats(self) -> bool:
        return self.stats is not None

    @property
    def has_combat(self) -> bool:
        return self.skills is not None

    def mark_deceased(self, cause: str | None = None) -> None:
        self.status = LifecycleStatus.DECEASED
        self.cause_of_death = cause

    def retire(self) -> None:
        self._require_living()
        self.status = LifecycleStatus.RETIRED

    def reactivate(self) -> None:
        self._require_living()
        self.status = LifecycleStatus.ACTIVE

    def _require_living(self) -> None:
        if self.status == LifecycleStatus.DECEASED:
            raise ValueError(f"{self.name} is dead; a deceased character cannot change status")


def new_id(category: CharacterCategory, rng: random.Random | None = None) -> str:
    """A unique id. Given an rng it is derived from it, so seeded generation reproduces the same id."""
    value = uuid.UUID(int=rng.getrandbits(128), version=4) if rng else uuid.uuid4()
    return f"{ID_PREFIX[category.value]}{value.hex}"


# ---------------------------------------------------------------- generation

def _stream(seed: int, part: str) -> random.Random:
    return random.Random(f"{seed}:npc:{part}")


def _species_pool(spec: NpcSpec, ct: Content) -> list[str]:
    pool = [spec.species] if isinstance(spec.species, str) else (spec.species or sorted(ct.species))
    unknown = [s for s in pool if s not in ct.species]
    if unknown or not pool:
        raise NpcSpecError(f"unknown species {unknown or pool!r}")
    return sorted(set(pool))


def default_age_range(species_id: str, ct: Content) -> tuple[int, int]:
    """Real years of the species' young-adult and adult stages (humans: 18 to 49)."""
    sp = ct.species[species_id]
    stages = {s.id: s for s in ct.stages}
    lo = _stage_first_age(sp, stages["young_adult"])
    hi = _stage_first_age(sp, stages["middle_age"]) - 1
    return lo, max(lo, hi)


def check_spec(spec: NpcSpec, ct: Content, cfg: PowerConfig, names: NameGenerator) -> None:
    """Raise NpcSpecError if the request names something that does not exist."""
    problems = [f"unknown species '{s}'" for s in ([spec.species] if isinstance(spec.species, str) else spec.species or [])
                if s not in ct.species]
    problems += [f"unknown stat '{k}'" for k in {*spec.stat_bonus, *spec.stats} if k not in STATS]
    problems += [f"unknown trait '{t}'" for t in spec.traits if t not in ct.traits]
    if spec.talent is not None and spec.talent not in ct.talents:
        problems.append(f"unknown talent '{spec.talent}'")
    problems += [f"unknown specialised stat '{k}' (use {sorted(cfg.specialised_stats)})"
                 for k in (spec.combat or {}) if k not in cfg.specialised_stats]
    if spec.culture is not None and spec.culture not in names.config.cultures:
        problems.append(f"unknown name culture '{spec.culture}'")
    if problems:
        raise NpcSpecError("; ".join(problems))


def _scalar(rng: random.Random, value: float | tuple[float, float]) -> float:
    lo, hi = (value, value) if isinstance(value, (int, float)) else value
    return round(rng.uniform(lo, hi), 1)


def _adapter(npc: Npc, ct: Content, *, stats=None, skills=None, traits=None) -> Character:
    """The NPC as a plain Character, using the given sections where the NPC has none stored."""
    return Character(
        name=npc.name, species_id=npc.species_id, age=npc.age, alive=npc.alive,
        stats=dict(npc.stats if npc.stats is not None else (stats or {})),
        skills=dict(npc.skills if npc.skills is not None else (skills or {})),
        traits=list(npc.traits if npc.traits is not None else (traits or [])),
        hidden_talent=npc.talent, talent_state=npc.talent_state,
        cause_of_death=npc.cause_of_death)


def _roll_stats(npc: Npc, ct: Content) -> dict[str, int]:
    spec, sp = npc.spec or NpcSpec(), ct.species[npc.species_id]
    rng = _stream(npc.seed, "stats")
    lo, hi = spec.stat_range
    out = {}
    for s in STATS:                                            # always draw all five, so the stream stays stable
        value = rng.randint(lo, hi) + sp.stat_modifiers.get(s, 0) + spec.stat_bonus.get(s, 0)
        out[s] = max(1, min(sp.stat_cap, spec.stats.get(s, value)))
    return out


def _roll_skills(npc: Npc, ct: Content, cfg: PowerConfig) -> dict[str, float]:
    spec, sp = npc.spec or NpcSpec(), ct.species[npc.species_id]
    rng = _stream(npc.seed, "combat")
    skills = dict(sp.innate_skills)
    if spec.combat is not None:                                # authored: used as given, species limits ignored
        chosen = {stat: _scalar(rng, level) for stat, level in spec.combat.items()}
    else:                                                      # drawn: only skills the species can train
        options = [s for s, d in cfg.specialised_stats.items()
                   if sp.allowed_skills is None or d.skills[0] in sp.allowed_skills]
        picked = rng.sample(options, min(spec.combat_focus, len(options)))
        chosen = {stat: _scalar(rng, spec.combat_level) for stat in picked}
    for stat, level in chosen.items():
        skill = cfg.specialised_stats[stat].skills[0]          # the stat's skill, e.g. marksmanship -> archery
        skills[skill] = skills.get(skill, 0.0) + level
    return skills


def _roll_traits(npc: Npc, ct: Content, stats: dict[str, int], skills: dict[str, float]) -> list[str]:
    spec, sp = npc.spec or NpcSpec(), ct.species[npc.species_id]
    traits = list(dict.fromkeys([*sp.innate_traits, *spec.traits]))     # chosen traits bypass prerequisites
    c = _adapter(npc, ct, stats=stats, skills=skills, traits=traits)
    if spec.random_traits:
        rng = _stream(npc.seed, "traits")
        pool = sorted(t for t, d in ct.traits.items() if d.birth is not None and t not in c.traits)
        rng.shuffle(pool)
        added = 0
        for trait_id in pool:
            if added >= spec.random_traits:
                break
            added += grant_trait(c, ct, trait_id)              # skipped when its prerequisites are not met
    return c.traits


def _roll_talent(npc: Npc, ct: Content, c: Character) -> str | None:
    spec = npc.spec or NpcSpec()
    if spec.talent is not None:
        return spec.talent
    return roll_hidden_talent(_stream(npc.seed, "talent"), c, ct, spec.talent_chance)


def _fill(npc: Npc, ct: Content, cfg: PowerConfig) -> dict:
    """Every section, from what is stored where there is something and from the seed where there is not.
    Nothing is written to the NPC. Without a seed (authored characters) missing sections stay empty."""
    if npc.seed is None:
        return {"stats": npc.stats or {}, "skills": npc.skills or {}, "traits": npc.traits or [],
                "talent": npc.talent, "talent_state": npc.talent_state}
    stats = npc.stats if npc.stats is not None else _roll_stats(npc, ct)
    skills = npc.skills if npc.skills is not None else _roll_skills(npc, ct, cfg)
    traits = npc.traits if npc.traits is not None else _roll_traits(npc, ct, stats, skills)
    talent, state = npc.talent, npc.talent_state
    if npc.traits is None:                                     # the talent is settled together with the traits
        talent = _roll_talent(npc, ct, _adapter(npc, ct, stats=stats, skills=skills, traits=traits))
        state = "dormant" if talent else "none"
    return {"stats": stats, "skills": skills, "traits": traits, "talent": talent, "talent_state": state}


def ensure_attributes(npc: Npc, *, stats: bool = False, combat: bool = False, traits: bool = False,
                      ct: Content | None = None, cfg: PowerConfig | None = None) -> Npc:
    """Generate and store the requested sections that are still missing. Existing values are never replaced.
    `combat` is the specialised stats (stored as skills), `traits` also settles the hidden talent."""
    if npc.seed is None:
        return npc
    ct, cfg = ct or get_content(), cfg or get_power_config()
    filled = _fill(npc, ct, cfg)
    if stats and npc.stats is None:
        npc.stats = filled["stats"]
    if combat and npc.skills is None:
        npc.skills = filled["skills"]
    if traits and npc.traits is None:
        npc.traits, npc.talent, npc.talent_state = filled["traits"], filled["talent"], filled["talent_state"]
    return npc


def ensure_all(npc: Npc, ct: Content | None = None, cfg: PowerConfig | None = None) -> Npc:
    return ensure_attributes(npc, stats=True, combat=True, traits=True, ct=ct, cfg=cfg)


class NpcFactory:
    """Generates NPCs. Holds the content and name rules so repeated calls are cheap."""

    def __init__(self, ct: Content | None = None, names: NameGenerator | None = None,
                 cfg: PowerConfig | None = None):
        self.ct = ct or get_content()
        self.names = names or get_name_generator()
        self.cfg = cfg or get_power_config()

    def generate(self, spec: NpcSpec | None = None, *, seed: int | None = None,
                 taken_names: Collection[str] | None = None) -> Npc:
        """One NPC. `seed` makes it reproducible (the same seed gives the same NPC, id included).
        `taken_names` opts in to name uniqueness: the full name will differ from every one listed."""
        spec = spec or NpcSpec()
        check_spec(spec, self.ct, self.cfg, self.names)
        seed = secrets.randbits(63) if seed is None else seed

        species = _stream(seed, "species").choice(_species_pool(spec, self.ct))
        lo, hi = spec.age_range or default_age_range(species, self.ct)
        name = self.names.generate(rng=_stream(seed, "name"), species=species, culture=spec.culture,
                                   taken=taken_names)
        npc = Npc(
            id=new_id(spec.category, _stream(seed, "id")), category=spec.category, name=name.full,
            species_id=species, age=_stream(seed, "age").randint(lo, hi), seed=seed, spec=spec)
        if (spec.detail or CATEGORY_DETAIL[spec.category]) == DetailLevel.FULL:
            ensure_all(npc, self.ct, self.cfg)
        return npc

    def generate_many(self, count: int, spec: NpcSpec | None = None, *, seed: int,
                      unique_names: bool = False, taken_names: Collection[str] | None = None) -> list[Npc]:
        """`count` NPCs with seeds derived from `seed`, so the whole batch is reproducible."""
        taken = set(taken_names or [])
        out = []
        for i in range(count):
            npc = self.generate(spec, seed=_derive(seed, i), taken_names=taken if unique_names else None)
            taken.add(npc.name)
            out.append(npc)
        return out

    def promote(self, npc: Npc) -> Npc:
        """A generic NPC who keeps coming back becomes persistent: same id and name, every section settled now."""
        if npc.category != CharacterCategory.GENERIC:
            raise ValueError(f"only a generic NPC can be promoted; {npc.name} is {npc.category.value}")
        promoted = npc.model_copy(deep=True, update={"category": CharacterCategory.PERSISTENT})
        if promoted.spec is not None:
            promoted.spec = promoted.spec.model_copy(update={"category": CharacterCategory.PERSISTENT})
        return ensure_all(promoted, self.ct, self.cfg)          # the id is unchanged: it is the same character


def _derive(seed: int, index: int) -> int:
    return random.Random(f"{seed}:npc:batch:{index}").getrandbits(63)


# ---------------------------------------------------------------- authored (named) characters

Scalar = bool | int | float | str
FLAG = re.compile(r"^[a-z][a-z0-9_.]*$")


class LifecycleRule(_Strict):
    """How a named character ages and whether they can die.

    normal      ages with the world and dies of old age once past the species' lifespan (the default)
    longevity   the same, but the lifespan is multiplied (`lifespan_multiplier` > 1)
    immortal    does not age and cannot die, by age or by story effect
    """
    ageing: Literal["normal", "longevity", "immortal"] = "normal"
    lifespan_multiplier: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def _shape(self):
        if self.ageing == "longevity" and self.lifespan_multiplier <= 1:
            raise ValueError("longevity needs a lifespan_multiplier above 1")
        if self.ageing != "longevity" and self.lifespan_multiplier != 1:
            raise ValueError("lifespan_multiplier only applies to ageing: longevity")
        return self


class GenerateRules(_Strict):
    """What the game MAY generate for a character, when they are first created. Nothing is generated by default:
    anything authored is fixed, and anything neither authored nor allowed here is simply left unset."""
    stats: bool = False                                 # fill in the base stats that were not authored
    stat_range: tuple[int, int] = DEFAULT_STAT_RANGE
    combat: bool = False                                # draw specialised stats (only if none are authored)
    combat_focus: int = Field(default=1, ge=1)
    combat_level: tuple[float, float] = DEFAULT_COMBAT_LEVEL
    random_traits: int = Field(default=0, ge=0)         # extra traits on top of the authored ones
    talent_chance: float = Field(default=0.0, ge=0, le=1)   # a hidden talent, only if none is authored

    @model_validator(mode="after")
    def _ranges(self):
        for label, (lo, hi) in (("stat_range", self.stat_range), ("combat_level", self.combat_level)):
            if not 0 <= lo <= hi:
                raise ValueError(f"{label} must be [min, max] with 0 <= min <= max")
        return self

    @property
    def any(self) -> bool:
        return bool(self.stats or self.combat or self.random_traits or self.talent_chance)


class RelationshipDef(_Strict):
    """An initial link to another named character. It is created once both exist."""
    kind: str                                           # see relationships.RelationshipKind
    other: str                                          # another definition's id
    self_is: Literal["source", "target"] = "source"     # for parent/mentor: source is the parent/mentor


class EncounterDef(_Strict):
    """When the story may bring this character into a game: a story-engine condition tree (see STORY_ENGINE.md),
    checked by the `spawn_npc` effect. Left out, the character can be introduced whenever a story says so."""
    conditions: dict | None = None


class NamedCharacterDef(_Strict):
    """A lore character written by hand. The name and id are authored; nothing authored is ever random.

    Authored values are FIXED. `generate` says what the game may fill in instead; everything else stays unset."""
    id: str
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1)
    title: str | None = None
    species: str
    age: int = Field(ge=0)                          # the STARTING age; the current age is runtime state
    description: str = ""
    lore: str = ""
    affiliations: list[str] = []
    location: str | None = None                     # where they start (runtime state afterwards)
    stats: dict[str, int] | None = None             # all five, or some if `generate.stats`, or leave out
    specialised_stats: dict[str, float] = {}        # swordsmanship / marksmanship / magic / martial_arts -> level
    traits: list[str] = []
    talent: str | None = None
    generate: GenerateRules = GenerateRules()
    lifecycle: LifecycleRule = LifecycleRule()
    status: LifecycleStatus = LifecycleStatus.ACTIVE
    relationships: list[RelationshipDef] = []
    flags: dict[str, Scalar] = {}                   # initial NPC-scoped story flags
    encounter: EncounterDef = EncounterDef()

    @model_validator(mode="after")
    def _shape(self):
        if not SLUG.match(self.id):
            raise ValueError(f"id '{self.id}' must be lowercase letters, digits and underscores, starting with a letter")
        if self.stats is not None:
            unknown = sorted(set(self.stats) - set(STATS))
            if unknown:
                raise ValueError(f"unknown base stat(s) {unknown} (use {sorted(STATS)})")
            if any(v < 1 for v in self.stats.values()):
                raise ValueError("a base stat must be at least 1")
            if set(self.stats) != set(STATS) and not self.generate.stats:
                raise ValueError(f"stats must list all of {sorted(STATS)}, or be left out, "
                                 "unless generate.stats is true to fill in the rest")
        if self.generate.combat and self.specialised_stats:
            raise ValueError("generate.combat only applies when specialised_stats is left out: authored values are fixed")
        if any(v < 0 for v in self.specialised_stats.values()):
            raise ValueError("specialised stat levels cannot be negative")
        for tag in self.affiliations:
            if not SLUG.match(tag):
                raise ValueError(f"affiliation '{tag}' must be lowercase letters, digits and underscores")
        for key in self.flags:
            if not FLAG.match(key):
                raise ValueError(f"flag '{key}' must be lowercase letters, digits, underscores and dots")
        if self.lifecycle.ageing == "immortal" and self.status == LifecycleStatus.DECEASED:
            raise ValueError("an immortal character cannot be deceased")
        return self


def load_named_characters(ct: Content | None = None, cfg: PowerConfig | None = None,
                          path: Path = NAMED_FILE) -> dict[str, NamedCharacterDef]:
    ct, cfg = ct or get_content(), cfg or get_power_config()
    try:
        raw = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    except (OSError, yaml.YAMLError) as e:
        raise ContentError([f"{path.name}: could not be read: {e}"])
    if not isinstance(raw, dict) or set(raw) - {"characters"}:
        raise ContentError([f"{path.name}: expected a single top-level key 'characters'"])
    errors: list[str] = []
    out: dict[str, NamedCharacterDef] = {}
    names: dict[str, str] = {}
    for i, entry in enumerate(raw.get("characters") or []):
        try:
            d = NamedCharacterDef.model_validate(entry)
        except ValidationError as e:
            errors += [f"{path.name}: characters[{i}].{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                       for err in e.errors()]
            continue
        where = f"{path.name}: '{d.id}'"
        if d.id in out:
            errors.append(f"{where}: duplicate id")
        if d.name.casefold() in names:
            errors.append(f"{where}: name '{d.name}' is already used by '{names[d.name.casefold()]}'")
        if d.species not in ct.species:
            errors.append(f"{where}: unknown species '{d.species}'")
        errors += [f"{where}: unknown trait '{t}'" for t in d.traits if t not in ct.traits]
        if d.talent is not None and d.talent not in ct.talents:
            errors.append(f"{where}: unknown talent '{d.talent}'")
        errors += [f"{where}: unknown specialised stat '{k}'" for k in d.specialised_stats if k not in cfg.specialised_stats]
        if d.species in ct.species:
            cap = ct.species[d.species].stat_cap
            errors += [f"{where}: {k} {v} is above the {d.species} stat cap of {cap}" for k, v in (d.stats or {}).items() if v > cap]
        out[d.id], names[d.name.casefold()] = d, d.id
    errors += _check_authored_links(out, path.name)
    if errors:
        raise ContentError(errors)
    return out


def _check_authored_links(defs: dict[str, NamedCharacterDef], file: str) -> list[str]:
    """The checks that need every definition at once: relationship targets, parent structure, condition syntax."""
    from .relationships import MAX_PARENTS, RelationshipKind          # lazily: relationships imports this module
    from .story import npc_hooks  # noqa: F401  (registers the named-character condition types used below)
    from .story.conditions import parse_condition

    errors: list[str] = []
    parents: dict[str, set[str]] = {}
    for d in defs.values():
        where = f"{file}: '{d.id}'"
        seen = set()
        for r in d.relationships:
            try:
                kind = RelationshipKind(r.kind)
            except ValueError:
                errors.append(f"{where}: unknown relationship kind '{r.kind}' (use {[k.value for k in RelationshipKind]})")
                continue
            if r.other == d.id:
                errors.append(f"{where}: cannot be related to themselves")
            elif r.other not in defs:
                errors.append(f"{where}: relationship with unknown character '{r.other}'")
            key = (r.kind, r.other, r.self_is)
            if key in seen:
                errors.append(f"{where}: duplicate relationship {r.kind} with '{r.other}'")
            seen.add(key)
            if kind == RelationshipKind.PARENT and r.other in defs and r.other != d.id:
                parent, child = (d.id, r.other) if r.self_is == "source" else (r.other, d.id)
                parents.setdefault(child, set()).add(parent)
        if d.encounter.conditions is not None:
            try:
                parse_condition(d.encounter.conditions)
            except ValueError as e:
                errors.append(f"{where}: encounter.conditions: {e}")
    for child, ps in parents.items():
        if len(ps) > MAX_PARENTS:
            errors.append(f"{file}: '{child}' would have {len(ps)} parents (at most {MAX_PARENTS})")

    def ancestor(a: str, b: str, depth: int = 0) -> bool:        # is `a` an ancestor of `b`?
        return depth < 64 and any(p == a or ancestor(a, p, depth + 1) for p in parents.get(b, ()))
    errors += [f"{file}: '{c}' is their own ancestor (circular parentage)" for c in parents if ancestor(c, c)]
    return errors


@lru_cache
def get_named_characters() -> dict[str, NamedCharacterDef]:
    return load_named_characters()


def named_npc(definition: NamedCharacterDef | str, ct: Content | None = None,
              cfg: PowerConfig | None = None) -> Npc:
    """A named character as an NPC: authored name and id, authored sections only (nothing is generated here; see
    named.build_npc for the generated parts). The runtime fields start from the definition."""
    ct, cfg = ct or get_content(), cfg or get_power_config()
    d = get_named_characters()[definition] if isinstance(definition, str) else definition
    skills = dict(ct.species[d.species].innate_skills)
    for stat, level in d.specialised_stats.items():
        skill = cfg.specialised_stats[stat].skills[0]
        skills[skill] = skills.get(skill, 0.0) + level
    complete = d.stats is not None and set(d.stats) == set(STATS)       # a partial set waits for generate.stats
    return Npc(
        id=ID_PREFIX["named"] + d.id, category=CharacterCategory.NAMED, status=d.status, name=d.name,
        species_id=d.species, age=d.age, stats=dict(d.stats) if complete else None,
        skills=skills if (d.specialised_stats or skills) else None,
        traits=list(dict.fromkeys([*ct.species[d.species].innate_traits, *d.traits])),
        talent=d.talent, talent_state="dormant" if d.talent else "none",
        definition=d.id, definition_version=d.version, flags=dict(d.flags), location=d.location)


# ---------------------------------------------------------------- Power and the rest of the game

def to_character(npc: Npc, ct: Content | None = None, cfg: PowerConfig | None = None) -> Character:
    """The NPC as a playable-style Character, so every existing rule works on it. Missing sections are filled
    from the seed for this call only; the NPC itself is not changed (use ensure_attributes to store them)."""
    ct, cfg = ct or get_content(), cfg or get_power_config()
    f = _fill(npc, ct, cfg)
    return Character(
        name=npc.name, species_id=npc.species_id, age=npc.age, alive=npc.alive, stats=dict(f["stats"]),
        skills=dict(f["skills"]), traits=list(f["traits"]), hidden_talent=f["talent"],
        talent_state=f["talent_state"], cause_of_death=npc.cause_of_death)


def npc_power(npc: Npc, ct: Content | None = None, cfg: PowerConfig | None = None) -> PowerBreakdown:
    """Power from the game's one Power calculation. Nothing is stored; combat skills are generated on demand."""
    ct, cfg = ct or get_content(), cfg or get_power_config()
    return calculate_power(to_character(npc, ct, cfg), ct, cfg)


def specialised_values(npc: Npc, ct: Content | None = None, cfg: PowerConfig | None = None) -> dict[str, float]:
    """Magic, Swordsmanship, Marksmanship and Martial Arts as stat id -> level (0 when untrained), read through
    power.yaml's definition of each stat (the sum of its skills), so a zero-weight stat is still listed."""
    ct, cfg = ct or get_content(), cfg or get_power_config()
    skills = to_character(npc, ct, cfg).skills
    return {stat: round(sum(skills.get(k, 0.0) for k in d.skills), 2) for stat, d in cfg.specialised_stats.items()}
