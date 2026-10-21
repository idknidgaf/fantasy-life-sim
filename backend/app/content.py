"""Game content: definitions, loading and validation.

All content lives in app/data as YAML (or JSON). It is parsed into strict models here, then
cross-checked: every skill, trait, item, event and species a file mentions must exist, and
requirements/effects must be well formed. A mistake raises ContentError listing EVERY problem.

Run `python -m app.validate_content` after editing any content file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from math import ceil
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DATA_DIR = Path(__file__).parent / "data"
STATS = ["constitution", "dexterity", "wisdom", "strength", "charisma"]
ACTIONS = {"play", "work", "rest", "train", "train_stat", "socialize"}
MONSTER_STAGE_NAMES = {
    "infancy": "Hatchling",
    "early_childhood": "Hatchling",
    "childhood": "Juvenile",
    "adolescence": "Juvenile",
    "young_adult": "Adult",
    "adulthood": "Adult",
    "middle_age": "Adult",
    "elder": "Ancient",
}
CATEGORIES = {"humanoid", "beast", "monstrous"}
HUMAN_YEARS = 80          # stage thresholds are expressed in human-equivalent years


class ContentError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(
            f"{len(errors)} content problem(s):\n" + "\n".join(f"  - {e}" for e in errors)
        )


# ---------------------------------------------------------------- definitions

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")      # a typo'd key is an error, not silently ignored


class AxisDef(Strict):
    id: str
    name: str
    group: Literal["personality", "alignment"]
    low: str
    high: str


class StageDef(Strict):
    id: str
    name: str
    monster_name: str | None = None     # display name for beast/monstrous species
    starts_at: float = Field(ge=0)
    description: str
    actions: list[str] = []
    action_count: int = Field(default=1, ge=0)   # actions allowed per year at this stage
    train_categories: list[str] = []
    growth_chance: float = Field(default=0, ge=0, le=1)
    aging_chance: float = Field(default=0, ge=0, le=1)
    event_chance: float = Field(default=0.3, ge=0, le=1)


class ItemDef(Strict):
    id: str
    name: str
    description: str = ""
    tags: list[str] = []


class SpeciesDef(Strict):
    id: str
    name: str
    category: Literal["humanoid", "beast", "monstrous"]
    description: str
    lifespan: int = Field(gt=0)
    base_mortality: float = Field(default=0.001, ge=0, le=1)
    stat_cap: int = Field(default=100, ge=1)
    event_rate: float = Field(default=1.0, gt=0)
    stat_modifiers: dict[str, int] = {}
    skill_aptitude: dict[str, float] = {}
    innate_skills: dict[str, float] = {}
    innate_traits: list[str] = []
    allowed_skills: list[str] | None = None
    tags: list[str] = []
    habitats: list[str] = Field(min_length=1)
    axis_bias: dict[str, int] = {}
    labels: dict[str, str] = {}
    work_stats: list[str] = ["strength", "dexterity", "charisma"]
    play_stats: list[str] = ["dexterity", "wisdom", "charisma"]


class TraitModifiers(Strict):
    stat: dict[str, int] = {}
    skill_aptitude: dict[str, float] = {}
    mortality: float = Field(default=1.0, gt=0)
    heal_bonus: float = Field(default=0.0, ge=0)   # extra max-health fraction healed each Age Up
    power: float = Field(default=0.0, ge=-1.0)     # percentage Power modifier as a decimal (0.1 = +10%); additive
    unlock_skills: list[str] = []


class BirthRule(Strict):
    chance: float = Field(gt=0, le=1)
    requirements: list[dict] = []


class AcquireRule(Strict):
    requirements: list[dict] = Field(min_length=1)
    chance: float = Field(gt=0, le=1)
    narrative: str


class TraitDef(Strict):
    id: str
    name: str
    description: str
    tier: Literal["common", "uncommon", "rare", "legendary"] = "common"
    modifiers: TraitModifiers = Field(default_factory=TraitModifiers)
    abilities: list[str] = []
    prerequisites: list[dict] = []
    birth: BirthRule | None = None
    acquire: list[AcquireRule] = []


class AwakenRule(Strict):
    min_stage: str | None = None
    requirements: list[dict] = Field(min_length=1)


class OnAwaken(Strict):
    narrative: str
    effects: list[dict] = []
    trigger_event: str | None = None


class TalentDef(Strict):
    id: str
    name: str
    description: str
    weight: float = Field(default=1, gt=0)
    eligible: list[dict] = []
    hints: list[str] = Field(min_length=1)
    awaken: AwakenRule
    on_awaken: OnAwaken


class ConditionsDef(Strict):
    min_age: int | None = None
    max_age: int | None = None
    min_stage: str | None = None
    max_stage: str | None = None
    life_stages: list[str] | None = None
    location_tags: list[str] | None = None           # character's location is ANY of these
    species: list[str] | None = None                 # species is ANY of these
    species_categories: list[str] | None = None      # category is ANY of these
    species_tags: list[str] | None = None            # species has ALL of these tags
    requires_all: list[dict] = []
    requires_any: list[dict] = []
    excludes: list[dict] = []


class CheckDef(Strict):
    stat: str | None = None
    skill: str | None = None
    difficulty: float


class OutcomeDef(Strict):
    result: str
    effects: list[dict] = []


class OpponentDef(Strict):
    """Who the player fights. The opponent has no stats of its own: it is described by the skills and traits it
    would have, and its Power comes from the SAME Power calculation as the player's (see battle.opponent_power)."""
    name: str = Field(min_length=1)
    skills: dict[str, float] = {}          # skill_id -> level; their Power contribution is set by power.yaml
    traits: list[str] = []                 # traits with a Power modifier count, like the player's
    cause: str | None = None               # cause of death if the player dies fighting it


class FleeDef(Strict):
    """How an encounter treats fleeing. Every fight can be fled from unless `allowed` is false."""
    allowed: bool = True
    modifier: float = Field(default=0.0, ge=-1, le=1)      # added to the flee probability (encounter context)
    text: str = Field(default="Flee", min_length=1)         # the button label
    success_text: str = "You slip away before it comes to blood."
    failure_text: str = "You try to get away, but you are caught and have to fight."


class CombatDef(Strict):
    """A choice resolved by the combat system instead of a dice check. `victory` and `defeat` hold the rewards
    and non-damage consequences; the damage itself (and death) always comes from the combat rules, never from
    these effects, so a defeat is never punished twice. Fleeing successfully applies neither."""
    opponent: OpponentDef
    victory: OutcomeDef
    defeat: OutcomeDef
    flee: FleeDef = Field(default_factory=FleeDef)


class ChoiceDef(Strict):
    id: str
    text: str
    requirements: list[dict] = []
    result: str | None = None
    effects: list[dict] = []
    check: CheckDef | None = None
    success: OutcomeDef | None = None
    failure: OutcomeDef | None = None
    combat: CombatDef | None = None


class EventDef(Strict):
    id: str
    title: str
    narrative: str
    weight: float = Field(default=1, ge=0)
    once: bool = False
    trigger_only: bool = False
    conditions: ConditionsDef = Field(default_factory=ConditionsDef)
    choices: list[ChoiceDef] = Field(min_length=1)


@dataclass
class Content:
    skills: dict[str, str]                      # skill_id -> category
    categories: dict[str, list[str]]            # category -> [skill_id]
    axes: dict[str, AxisDef]
    stages: list[StageDef]
    items: dict[str, ItemDef]
    species: dict[str, SpeciesDef]
    traits: dict[str, TraitDef]
    talents: dict[str, TalentDef]
    events: dict[str, EventDef]
    warnings: list[str] = field(default_factory=list)

    @property
    def stage_ids(self) -> list[str]:
        return [s.id for s in self.stages]


# ---------------------------------------------------------------- requirements & effects grammar

REQ_KINDS: dict[str, set[str]] = {
    "stat": {"min", "max"}, "skill": {"min_level", "max_level"}, "skill_count": {"min_level"},
    "max_top_skill": set(), "trait": set(), "flag": set(), "counter": {"min", "max"},
    "item": {"min"}, "item_tag": {"min"}, "axis": {"min", "max"},
    "species": set(), "species_category": set(), "species_tag": set(), "location_tag": set(),
    "any": set(), "all": set(), "not": set(),
}
NEEDS_BOUND = {
    "stat": ("min", "max"), "skill": ("min_level", "max_level"),
    "counter": ("min", "max"), "axis": ("min", "max"),
}
EFFECT_KINDS = {
    "stat_delta", "skill_xp", "axis_delta", "grant_trait", "remove_trait", "flag", "clear_flag",
    "counter", "grant_item", "consume_item", "set_location", "die", "trigger_event",
    "health_delta", "stress_delta", "happiness_delta",
}


@dataclass
class _Refs:
    """Everything the content refers to, collected so reachability can be checked at the end."""
    flags_set: set[str] = field(default_factory=set)
    flags_required: dict[str, str] = field(default_factory=dict)
    counters_set: set[str] = field(default_factory=set)
    counters_required: dict[str, str] = field(default_factory=dict)
    traits_granted: set[str] = field(default_factory=set)
    items_granted: set[str] = field(default_factory=set)
    locations_set: set[str] = field(default_factory=set)
    location_refs: list[tuple[str, str]] = field(default_factory=list)
    events_triggered: set[str] = field(default_factory=set)


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else [v]


def _check_req(req: Any, where: str, ct: Content, refs: _Refs, errs: list[str]) -> None:
    if not isinstance(req, dict):
        errs.append(f"{where}: a requirement must be a mapping, got {type(req).__name__}")
        return
    kinds = [k for k in req if k in REQ_KINDS]
    if len(kinds) != 1:
        errs.append(f"{where}: requirement needs exactly one of {sorted(REQ_KINDS)}; got keys {sorted(req)}")
        return
    kind = kinds[0]
    value = req[kind]
    extra = set(req) - {kind} - REQ_KINDS[kind]
    if extra:
        errs.append(f"{where}: '{kind}' requirement has unexpected key(s) {sorted(extra)}")
    for mod in REQ_KINDS[kind] & set(req):
        if not _num(req[mod]):
            errs.append(f"{where}: '{mod}' must be a number")
    if kind in NEEDS_BOUND and not (set(NEEDS_BOUND[kind]) & set(req)):
        errs.append(f"{where}: '{kind}' requirement needs {' or '.join(NEEDS_BOUND[kind])}")

    if kind == "stat":
        if value not in STATS:
            errs.append(f"{where}: unknown stat '{value}'")
    elif kind == "skill":
        if value not in ct.skills:
            errs.append(f"{where}: unknown skill '{value}'")
    elif kind == "skill_count":
        if not (isinstance(value, int) and not isinstance(value, bool) and value > 0):
            errs.append(f"{where}: skill_count must be a positive whole number")
    elif kind == "max_top_skill":
        if not _num(value):
            errs.append(f"{where}: max_top_skill must be a number")
    elif kind == "trait":
        if value not in ct.traits:
            errs.append(f"{where}: unknown trait '{value}'")
    elif kind == "flag":
        refs.flags_required.setdefault(str(value), where)
    elif kind == "counter":
        refs.counters_required.setdefault(str(value), where)
    elif kind == "item":
        if value not in ct.items:
            errs.append(f"{where}: unknown item '{value}'")
    elif kind == "item_tag":
        if not any(value in i.tags for i in ct.items.values()):
            errs.append(f"{where}: no item has the tag '{value}'")
    elif kind == "axis":
        if value not in ct.axes:
            errs.append(f"{where}: unknown axis '{value}'")
    elif kind == "species":
        for v in _as_list(value):
            if v not in ct.species:
                errs.append(f"{where}: unknown species '{v}'")
    elif kind == "species_category":
        for v in _as_list(value):
            if v not in CATEGORIES:
                errs.append(f"{where}: unknown species category '{v}'")
    elif kind == "species_tag":
        tags = {t for s in ct.species.values() for t in s.tags}
        for v in _as_list(value):
            if v not in tags:
                errs.append(f"{where}: no species has the tag '{v}'")
    elif kind == "location_tag":
        for v in _as_list(value):
            refs.location_refs.append((where, str(v)))
    elif kind in ("any", "all"):
        if not isinstance(value, list) or not value:
            errs.append(f"{where}: '{kind}' needs a non-empty list of requirements")
        else:
            for i, sub in enumerate(value):
                _check_req(sub, f"{where} > {kind}[{i}]", ct, refs, errs)
    elif kind == "not":
        _check_req(value, f"{where} > not", ct, refs, errs)


def _check_reqs(reqs: list, where: str, ct: Content, refs: _Refs, errs: list[str]) -> None:
    for i, r in enumerate(reqs):
        _check_req(r, f"{where}[{i}]", ct, refs, errs)


def _check_effect(eff: Any, where: str, ct: Content, refs: _Refs, errs: list[str]) -> None:
    if not isinstance(eff, dict):
        errs.append(f"{where}: an effect must be a mapping, got {type(eff).__name__}")
        return
    kinds = [k for k in eff if k in EFFECT_KINDS]
    if len(kinds) != 1:
        errs.append(f"{where}: effect needs exactly one of {sorted(EFFECT_KINDS)}; got keys {sorted(eff)}")
        return
    kind = kinds[0]
    value = eff[kind]
    extra = set(eff) - {kind, "chance"}
    if extra:
        errs.append(f"{where}: unexpected key(s) {sorted(extra)}")
    if "chance" in eff and not (_num(eff["chance"]) and 0 < eff["chance"] <= 1):
        errs.append(f"{where}: chance must be a number above 0 and at most 1")

    if kind in ("stat_delta", "axis_delta", "skill_xp", "counter"):
        if not isinstance(value, dict) or not value:
            errs.append(f"{where}: '{kind}' needs a mapping")
            return
        for key, amount in value.items():
            if not _num(amount):
                errs.append(f"{where}: '{kind}.{key}' must be a number")
            if kind == "stat_delta" and key not in STATS:
                errs.append(f"{where}: unknown stat '{key}'")
            if kind == "axis_delta" and key not in ct.axes:
                errs.append(f"{where}: unknown axis '{key}'")
            if kind == "skill_xp" and key not in ct.skills and key not in ct.categories:
                errs.append(f"{where}: '{key}' is neither a skill nor a skill category")
            if kind == "counter":
                if not isinstance(amount, int) or isinstance(amount, bool):
                    errs.append(f"{where}: counter amounts must be whole numbers")
                elif amount > 0:
                    refs.counters_set.add(str(key))
    elif kind in ("grant_trait", "remove_trait"):
        if value not in ct.traits:
            errs.append(f"{where}: unknown trait '{value}'")
        elif kind == "grant_trait":
            refs.traits_granted.add(value)
    elif kind in ("flag", "clear_flag"):
        if not isinstance(value, str) or not value:
            errs.append(f"{where}: '{kind}' needs a name")
        elif kind == "flag":
            refs.flags_set.add(value)
    elif kind == "grant_item":
        item_id = value.get("id") if isinstance(value, dict) else value
        if item_id not in ct.items:
            errs.append(f"{where}: unknown item '{item_id}'")
        else:
            refs.items_granted.add(item_id)
    elif kind == "consume_item":
        if not isinstance(value, dict) or len(set(value) & {"id", "tag"}) != 1 or set(value) - {"id", "tag", "count"}:
            errs.append(f"{where}: consume_item needs exactly one of 'id' or 'tag' (and optionally 'count')")
        elif "id" in value and value["id"] not in ct.items:
            errs.append(f"{where}: unknown item '{value['id']}'")
        elif "tag" in value and not any(value["tag"] in i.tags for i in ct.items.values()):
            errs.append(f"{where}: no item has the tag '{value['tag']}'")
    elif kind == "set_location":
        if not isinstance(value, str) or not value:
            errs.append(f"{where}: set_location needs a location tag")
        else:
            refs.locations_set.add(value)
    elif kind in ("health_delta", "stress_delta", "happiness_delta"):
        if not _num(value):
            errs.append(f"{where}: '{kind}' must be a number")
    elif kind == "die":
        if not isinstance(value, dict) or not isinstance(value.get("cause"), str):
            errs.append(f"{where}: die needs a mapping with a text 'cause'")
    elif kind == "trigger_event":
        if value not in ct.events:
            errs.append(f"{where}: unknown event '{value}'")
        else:
            refs.events_triggered.add(value)


def _check_effects(effs: list, where: str, ct: Content, refs: _Refs, errs: list[str]) -> None:
    for i, e in enumerate(effs):
        _check_effect(e, f"{where}[{i}]", ct, refs, errs)


def _check_combat(ch: ChoiceDef, where: str, ct: Content, refs: _Refs, errs: list[str]) -> None:
    """A combat choice is resolved by the combat system: it may not also carry a check or a plain result."""
    if ch.check or ch.success is not None or ch.failure is not None or ch.result is not None or ch.effects:
        errs.append(f"{where}: a combat choice uses combat.victory/defeat, not check, success/failure or result/effects")
    combat = ch.combat
    for skill in combat.opponent.skills:
        if skill not in ct.skills:
            errs.append(f"{where}: combat opponent has unknown skill '{skill}'")
    if any(level < 0 for level in combat.opponent.skills.values()):
        errs.append(f"{where}: combat opponent skill levels cannot be negative")
    for trait in combat.opponent.traits:
        if trait not in ct.traits:
            errs.append(f"{where}: combat opponent has unknown trait '{trait}'")
    _check_effects(combat.victory.effects, f"{where} combat.victory.effects", ct, refs, errs)
    _check_effects(combat.defeat.effects, f"{where} combat.defeat.effects", ct, refs, errs)
    for name, outcome in (("victory", combat.victory), ("defeat", combat.defeat)):
        for e in outcome.effects:
            if isinstance(e, dict) and ("die" in e or (_num(e.get("health_delta")) and e["health_delta"] < 0)):
                errs.append(f"{where}: combat.{name}.effects may not deal damage or kill: "
                            "defeat damage and death come from the combat rules, so nothing is applied twice")


# ---------------------------------------------------------------- cross-validation

def _stage_first_age(sp: SpeciesDef, stage: StageDef) -> int:
    return ceil(stage.starts_at * sp.lifespan / HUMAN_YEARS)


def _event_possible(ev: EventDef, ct: Content) -> bool:
    """Is there ANY species/stage combination that satisfies the event's static conditions?"""
    cond = ev.conditions
    ids = ct.stage_ids
    for sp in ct.species.values():
        if cond.species and sp.id not in cond.species:
            continue
        if cond.species_categories and sp.category not in cond.species_categories:
            continue
        if cond.species_tags and not all(t in sp.tags for t in cond.species_tags):
            continue
        if cond.location_tags and not (set(cond.location_tags) & set(sp.habitats) or
                                       set(cond.location_tags) & _ALL_SET_LOCATIONS.get(id(ct), set())):
            continue
        for i, st in enumerate(ct.stages):
            if cond.life_stages and st.id not in cond.life_stages:
                continue
            if cond.min_stage and i < ids.index(cond.min_stage):
                continue
            if cond.max_stage and i > ids.index(cond.max_stage):
                continue
            return True
    return False


_ALL_SET_LOCATIONS: dict[int, set[str]] = {}


def _cross_validate(ct: Content) -> tuple[list[str], list[str]]:
    errs: list[str] = []
    warns: list[str] = []
    refs = _Refs()
    stage_ids = ct.stage_ids

    # axes
    for needed in ("law_chaos", "good_evil"):
        if needed not in ct.axes:
            errs.append(f"axes: the alignment axis '{needed}' is required")

    # stages
    if not ct.stages:
        errs.append("stages: at least one stage is required")
    else:
        if ct.stages[0].starts_at != 0:
            errs.append("stages: the first stage must start at 0")
        for a, b in zip(ct.stages, ct.stages[1:]):
            if b.starts_at <= a.starts_at:
                errs.append(f"stages: '{b.id}' must start after '{a.id}'")
    for st in ct.stages:
        for a in st.actions:
            if a not in ACTIONS:
                errs.append(f"stage '{st.id}': unknown action '{a}' (use {sorted(ACTIONS)})")
        for c in st.train_categories:
            if c != "all" and c not in ct.categories:
                errs.append(f"stage '{st.id}': unknown skill category '{c}'")
        if "train" in st.actions and not st.train_categories:
            errs.append(f"stage '{st.id}': 'train' needs train_categories")

    # species
    aptitude_keys = set(ct.skills) | set(ct.categories) | {"all"}
    for sp in ct.species.values():
        w = f"species '{sp.id}'"
        for k in sp.stat_modifiers:
            if k not in STATS:
                errs.append(f"{w}: unknown stat '{k}' in stat_modifiers")
        for k in sp.skill_aptitude:
            if k not in aptitude_keys:
                errs.append(f"{w}: unknown skill/category '{k}' in skill_aptitude")
        for k in sp.innate_skills:
            if k not in ct.skills:
                errs.append(f"{w}: unknown skill '{k}' in innate_skills")
        for t in sp.innate_traits:
            if t not in ct.traits:
                errs.append(f"{w}: unknown trait '{t}' in innate_traits")
        for s in sp.allowed_skills or []:
            if s not in ct.skills:
                errs.append(f"{w}: unknown skill '{s}' in allowed_skills")
        for k in sp.axis_bias:
            if k not in ct.axes:
                errs.append(f"{w}: unknown axis '{k}' in axis_bias")
        for k in sp.labels:
            if k not in ACTIONS:
                errs.append(f"{w}: labels key '{k}' is not an action ({sorted(ACTIONS)})")
        for k in sp.work_stats + sp.play_stats:
            if k not in STATS:
                errs.append(f"{w}: unknown stat '{k}' in work_stats/play_stats")

    # traits
    for t in ct.traits.values():
        w = f"trait '{t.id}'"
        for k in t.modifiers.stat:
            if k not in STATS:
                errs.append(f"{w}: unknown stat '{k}' in modifiers.stat")
        for k in t.modifiers.skill_aptitude:
            if k not in aptitude_keys:
                errs.append(f"{w}: unknown skill/category '{k}' in modifiers.skill_aptitude")
        for s in t.modifiers.unlock_skills:
            if s not in ct.skills:
                errs.append(f"{w}: unknown skill '{s}' in modifiers.unlock_skills")
        _check_reqs(t.prerequisites, f"{w} prerequisites", ct, refs, errs)
        if t.birth:
            _check_reqs(t.birth.requirements, f"{w} birth.requirements", ct, refs, errs)
        for i, rule in enumerate(t.acquire):
            _check_reqs(rule.requirements, f"{w} acquire[{i}].requirements", ct, refs, errs)

    # talents
    for tl in ct.talents.values():
        w = f"talent '{tl.id}'"
        _check_reqs(tl.eligible, f"{w} eligible", ct, refs, errs)
        if tl.awaken.min_stage and tl.awaken.min_stage not in stage_ids:
            errs.append(f"{w}: unknown stage '{tl.awaken.min_stage}' in awaken.min_stage")
        _check_reqs(tl.awaken.requirements, f"{w} awaken.requirements", ct, refs, errs)
        _check_effects(tl.on_awaken.effects, f"{w} on_awaken.effects", ct, refs, errs)
        if tl.on_awaken.trigger_event:
            if tl.on_awaken.trigger_event not in ct.events:
                errs.append(f"{w}: on_awaken.trigger_event '{tl.on_awaken.trigger_event}' does not exist")
            else:
                refs.events_triggered.add(tl.on_awaken.trigger_event)

    # events
    for ev in ct.events.values():
        w = f"event '{ev.id}'"
        cond = ev.conditions
        for fname in ("min_stage", "max_stage"):
            v = getattr(cond, fname)
            if v and v not in stage_ids:
                errs.append(f"{w}: unknown stage '{v}' in conditions.{fname}")
        if cond.min_stage in stage_ids and cond.max_stage in stage_ids and \
                stage_ids.index(cond.min_stage) > stage_ids.index(cond.max_stage):
            errs.append(f"{w}: min_stage comes after max_stage")
        for v in cond.life_stages or []:
            if v not in stage_ids:
                errs.append(f"{w}: unknown stage '{v}' in conditions.life_stages")
        if cond.min_age is not None and cond.max_age is not None and cond.min_age > cond.max_age:
            errs.append(f"{w}: min_age is greater than max_age")
        for v in cond.species or []:
            if v not in ct.species:
                errs.append(f"{w}: unknown species '{v}' in conditions.species")
        for v in cond.species_categories or []:
            if v not in CATEGORIES:
                errs.append(f"{w}: unknown category '{v}' in conditions.species_categories")
        all_tags = {t for s in ct.species.values() for t in s.tags}
        for v in cond.species_tags or []:
            if v not in all_tags:
                errs.append(f"{w}: no species has the tag '{v}' (conditions.species_tags)")
        for v in cond.location_tags or []:
            refs.location_refs.append((f"{w} conditions.location_tags", v))
        _check_reqs(cond.requires_all, f"{w} requires_all", ct, refs, errs)
        _check_reqs(cond.requires_any, f"{w} requires_any", ct, refs, errs)
        _check_reqs(cond.excludes, f"{w} excludes", ct, refs, errs)
        if ev.weight == 0 and not ev.trigger_only:
            errs.append(f"{w}: weight 0 means it can never be picked (set trigger_only: true if intended)")

        if all(ch.requirements for ch in ev.choices):
            errs.append(f"{w}: every choice has requirements, so a character could get stuck; give at least one choice no requirements")
        seen: set[str] = set()
        for ch in ev.choices:
            cw = f"{w} choice '{ch.id}'"
            if ch.id in seen:
                errs.append(f"{cw}: duplicate choice id")
            seen.add(ch.id)
            _check_reqs(ch.requirements, f"{cw} requirements", ct, refs, errs)
            if ":" in ch.id:
                errs.append(f"{cw}: a choice id may not contain ':' (it is reserved for the generated Flee option)")
            if ch.combat:
                _check_combat(ch, cw, ct, refs, errs)
            elif ch.check:
                if (ch.check.stat is None) == (ch.check.skill is None):
                    errs.append(f"{cw}: check needs exactly one of 'stat' or 'skill'")
                if ch.check.stat is not None and ch.check.stat not in STATS:
                    errs.append(f"{cw}: unknown stat '{ch.check.stat}' in check")
                if ch.check.skill is not None and ch.check.skill not in ct.skills:
                    errs.append(f"{cw}: unknown skill '{ch.check.skill}' in check")
                if ch.success is None or ch.failure is None:
                    errs.append(f"{cw}: a choice with a check needs both 'success' and 'failure'")
                if ch.result is not None or ch.effects:
                    errs.append(f"{cw}: a choice with a check uses success/failure, not result/effects")
                for name, outcome in (("success", ch.success), ("failure", ch.failure)):
                    if outcome:
                        _check_effects(outcome.effects, f"{cw} {name}.effects", ct, refs, errs)
            else:
                if ch.success is not None or ch.failure is not None:
                    errs.append(f"{cw}: success/failure need a 'check'")
                if ch.result is None:
                    errs.append(f"{cw}: needs a 'result' (or a 'check' with success/failure)")
                _check_effects(ch.effects, f"{cw} effects", ct, refs, errs)

    # reachability
    locations = {h for s in ct.species.values() for h in s.habitats} | refs.locations_set
    _ALL_SET_LOCATIONS[id(ct)] = refs.locations_set
    for where, loc in refs.location_refs:
        if loc not in locations:
            errs.append(f"{where}: location '{loc}' is never a species habitat or a set_location target")
    for flag, where in sorted(refs.flags_required.items()):
        if flag not in refs.flags_set:
            errs.append(f"{where}: flag '{flag}' is required but no effect ever sets it")
    for counter, where in sorted(refs.counters_required.items()):
        if counter not in refs.counters_set:
            errs.append(f"{where}: counter '{counter}' is required but no effect ever raises it")

    # warnings (suspicious but not fatal)
    innate = {t for s in ct.species.values() for t in s.innate_traits}
    for t in ct.traits.values():
        if not (t.birth or t.acquire or t.id in innate or t.id in refs.traits_granted):
            warns.append(f"trait '{t.id}' can never be obtained (no birth rule, acquire rule, species or effect grants it)")
    for ev in ct.events.values():
        if ev.trigger_only and ev.id not in refs.events_triggered:
            warns.append(f"event '{ev.id}' is trigger_only but nothing triggers it")
        if not ev.trigger_only and ev.weight > 0 and not errs and not _event_possible(ev, ct):
            warns.append(f"event '{ev.id}' can never be seen: no species/stage combination satisfies its conditions")
    for item in ct.items.values():
        if item.id not in refs.items_granted:
            warns.append(f"item '{item.id}' is never granted by any effect")
    _ALL_SET_LOCATIONS.pop(id(ct), None)
    return errs, warns


# ---------------------------------------------------------------- loading

def _read_file(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)


def _models(path: Path, model: type[BaseModel], label: str, errs: list[str]) -> dict[str, Any]:
    try:
        data = _read_file(path)
    except FileNotFoundError:
        errs.append(f"{path.name}: file not found")
        return {}
    except (yaml.YAMLError, json.JSONDecodeError) as e:
        errs.append(f"{path.name}: could not be parsed: {e}")
        return {}
    if not isinstance(data, list):
        errs.append(f"{path.name}: expected a list of {label} definitions")
        return {}
    out: dict[str, Any] = {}
    for i, raw in enumerate(data):
        ident = raw.get("id", f"#{i}") if isinstance(raw, dict) else f"#{i}"
        try:
            obj = model.model_validate(raw)
        except ValidationError as e:
            for err in e.errors():
                loc = ".".join(str(p) for p in err["loc"]) or "(root)"
                errs.append(f"{path.name}: {label} '{ident}': {loc}: {err['msg']}")
            continue
        if obj.id in out:
            errs.append(f"{path.name}: duplicate {label} id '{obj.id}'")
        out[obj.id] = obj
    return out


def load_content(data_dir: Path = DATA_DIR) -> Content:
    errs: list[str] = []

    try:
        raw_skills = _read_file(data_dir / "skills.json")
    except (OSError, json.JSONDecodeError) as e:
        raw_skills = {}
        errs.append(f"skills.json: could not be read: {e}")
    categories: dict[str, list[str]] = {}
    skills: dict[str, str] = {}
    if not isinstance(raw_skills, dict):
        errs.append("skills.json: expected a mapping of category -> list of skills")
    else:
        for cat, lst in raw_skills.items():
            if not isinstance(lst, list) or not lst:
                errs.append(f"skills.json: category '{cat}' needs a non-empty list")
                continue
            categories[cat] = list(lst)
            for s in lst:
                if s in skills:
                    errs.append(f"skills.json: duplicate skill '{s}'")
                skills[s] = cat

    axes = _models(data_dir / "axes.yaml", AxisDef, "axis", errs)
    stages = list(_models(data_dir / "stages.yaml", StageDef, "stage", errs).values())
    items = _models(data_dir / "items.yaml", ItemDef, "item", errs)
    species = _models(data_dir / "species.yaml", SpeciesDef, "species", errs)
    traits = _models(data_dir / "traits.yaml", TraitDef, "trait", errs)
    talents = _models(data_dir / "talents.yaml", TalentDef, "talent", errs)

    events: dict[str, EventDef] = {}
    event_dir = data_dir / "events"
    files = sorted(p for p in event_dir.glob("*") if p.suffix in {".yaml", ".yml", ".json"}) if event_dir.exists() else []
    if not files:
        errs.append("events/: no event files found")
    for path in files:
        for eid, ev in _models(path, EventDef, "event", errs).items():
            if eid in events:
                errs.append(f"{path.name}: duplicate event id '{eid}' (also defined in another file)")
            events[eid] = ev

    if errs:                       # fix structural problems before cross-checking references
        raise ContentError(errs)

    ct = Content(skills, categories, axes, stages, items, species, traits, talents, events)
    errs, warns = _cross_validate(ct)
    if errs:
        raise ContentError(errs)
    ct.warnings = warns
    return ct


@lru_cache
def get_content() -> Content:
    return load_content()
