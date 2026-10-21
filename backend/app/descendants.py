"""Creating a child from one or two existing characters, and saving it.

The rules (what the child inherits) are in inheritance.py and data/inheritance.yaml. This module only does the
plumbing, all of it through the existing systems:

  * the child is an ordinary persistent NPC made by `NpcFactory`: its own unique id, a generated name, its own age;
  * it is saved with `npc_store.save_npc` and linked with `relationships.add_parent` as one all-or-nothing step;
  * Power is never touched: it is calculated from the child's own skills and traits, like anyone else's.

Parents are referenced by stable id (an NPC's id, or `pc_<n>` for the playable character). A parent who cannot be found,
or who is only a temporary NPC, is *unavailable*: it is skipped and reported, and the child is made from whoever is left
(an error if nobody is). A parent who is found but not allowed (too young, an incompatible species, the other parent's
own ancestor...) is an error, not a silent skip, because that is a mistake in the request. Whether a parent is alive is
not checked: when a child is born is the caller's concern, and this task does no death handling.

Retry-safe: the same parents and seed give the same child id. Repeating a call that already succeeded returns the saved
child (`created=False`); reusing a seed with different parents is refused.
"""
from __future__ import annotations

import random
import secrets
from dataclasses import dataclass, field
from typing import Sequence

from sqlmodel import Session, select

from . import engine as sim
from .content import Content, get_content
from .inheritance import (
    DescendantConflictError, InheritanceReport, InheritanceRules, InvalidChildAgeError, NoParentsError,
    ParentNotEligibleError, ParentSource, MAX_PARENTS, check_parents, get_inheritance_rules, plan_inheritance,
)
from .models import CharacterRecord, NpcRecord
from .npc import CharacterCategory, Npc, NpcFactory, NpcSpec, to_character
from .npc_store import load_npc, save_npc
from .power import PowerConfig, get_power_config
from .relationships import PLAYER_PREFIX, add_parent, ancestors_of, parents_of


@dataclass(frozen=True)
class SkippedParent:
    ref: str
    reason: str


@dataclass
class Descendant:
    npc: Npc
    parents: list[str]                                   # the parents actually used, as stable ids
    skipped: list[SkippedParent] = field(default_factory=list)
    report: InheritanceReport | None = None              # what was inherited from whom
    created: bool = True                                 # False when this call found the child already saved


def descendant_seed(seed: int, index: int) -> int:
    """A reproducible seed for the index-th child of one family, so siblings differ."""
    return random.Random(f"{seed}:descendant:{index}").getrandbits(63)


def _load_parent(session: Session, campaign_id: int, ref: str, ct: Content,
                 cfg: PowerConfig) -> ParentSource | str:
    """The parent as a ParentSource, or the reason it is unavailable."""
    if ref.startswith(PLAYER_PREFIX):
        number = ref[len(PLAYER_PREFIX):]
        rec = session.get(CharacterRecord, int(number)) if number.isdigit() else None
        if rec is None or rec.campaign_id != campaign_id:
            return "no such playable character in this campaign"
        return ParentSource(ref, sim.repair(rec.to_character()))
    row = session.exec(select(NpcRecord).where(NpcRecord.campaign_id == campaign_id, NpcRecord.npc_id == ref)).first()
    if row is None:
        return "no such character in this campaign"
    if row.category == CharacterCategory.GENERIC.value:
        return "a temporary NPC; promote it to persistent first"
    return ParentSource(ref, to_character(Npc.model_validate(row.data), ct, cfg))


def create_descendant(session: Session, campaign_id: int, parents: Sequence[str | None], *, seed: int | None = None,
                      age: int = 0, rules: InheritanceRules | None = None, factory: NpcFactory | None = None,
                      taken_names: Sequence[str] | None = None) -> Descendant:
    """Make a child of one or two parents, save it, and link it to them. Nothing is committed.

    `parents` are stable ids; None (or a missing second parent) just means that parent is unknown. `age` is the
    child's age, default a newborn; it must be below every parent's. `seed` makes the child reproducible (and is
    what makes a retry return the same child); with none, one is drawn and kept on the child.
    """
    ct, cfg = (factory.ct, factory.cfg) if factory else (get_content(), get_power_config())
    rules = rules or get_inheritance_rules()
    factory = factory or NpcFactory(ct, cfg=cfg)
    refs = [r for r in parents if r]
    if len(refs) > MAX_PARENTS:
        raise ParentNotEligibleError(f"a child has at most {MAX_PARENTS} parents")
    if len(set(refs)) != len(refs):
        raise ParentNotEligibleError("the same character was given as both parents")
    if age < 0:
        raise InvalidChildAgeError("a child's age cannot be negative")

    sources: list[ParentSource] = []
    skipped: list[SkippedParent] = []
    for ref in refs:
        loaded = _load_parent(session, campaign_id, ref, ct, cfg)
        (skipped.append(SkippedParent(ref, loaded)) if isinstance(loaded, str) else sources.append(loaded))
    if not sources:
        why = "; ".join(f"{s.ref}: {s.reason}" for s in skipped)
        raise NoParentsError("no parent is available" + (f" ({why})" if why else ""))

    check_parents(sources, rules, ct)                      # is each parent allowed, and can these two have a child?
    for p in sources:
        if age >= p.character.age:
            raise InvalidChildAgeError(f"the child (age {age}) must be younger than {p.character.name} (age {p.character.age})")
    if rules.parents.forbid_direct_line and len(sources) == 2:
        a, b = sources
        if a.ref in ancestors_of(session, campaign_id, b.ref) or b.ref in ancestors_of(session, campaign_id, a.ref):
            raise ParentNotEligibleError(f"{a.character.name} and {b.character.name} are in the same direct line")

    seed = secrets.randbits(63) if seed is None else seed
    plan = plan_inheritance(sources, seed, age=age, rules=rules, ct=ct, cfg=cfg)
    child = factory.generate(
        NpcSpec(category=CharacterCategory.PERSISTENT, species=plan.species_id, age_range=(age, age),
                stats=plan.stats, traits=plan.traits, talent=plan.talent, talent_chance=plan.talent_chance,
                combat=plan.combat),                      # explicit, so no random combat is added on top
        seed=seed, taken_names=taken_names)

    used = [p.ref for p in sources]
    existing = load_npc(session, campaign_id, child.id)
    if existing is not None:
        if set(parents_of(session, campaign_id, child.id)) != set(used):
            raise DescendantConflictError(f"seed {seed} already made a different child ({existing.name}); use another seed")
        return Descendant(existing, used, skipped, plan.report, created=False)

    with session.begin_nested():                          # the child and its links are saved together or not at all
        save_npc(session, campaign_id, child)
        add_parent(session, campaign_id, child.id, *used)
    return Descendant(child, used, skipped, plan.report)
