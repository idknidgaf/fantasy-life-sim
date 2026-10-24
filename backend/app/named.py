"""Named (authored) characters at runtime: creating them, keeping their state, ageing them, linking them.

Three things are kept apart.

  definition   data/named_characters.yaml, version controlled and read-only at runtime (`NamedCharacterDef`).
               Title, lore, description and affiliations are only ever read from here, never copied.
  instance     an `Npc` saved through npc_store, with a stable id (`named_<definition id>`, unique per campaign) and
               the character's RUNTIME state: current age, status, location, story flags, the definition version it
               was made from. Stored in the existing NpcRecord JSON, so there is no new table.
  links        the existing CharacterRelationship table, through relationships.py.

Instantiation is explicit and idempotent: nothing is created unless a story rule or a caller asks, and asking again
returns the same character. Starting attributes come from the definition: authored values are FIXED and never
rolled, and only what `generate` allows is generated, once, from a seed made of the campaign seed and the
definition id, then saved with the character, so tuning the generator later cannot change a character who already
exists. Power is never stored: it is calculated from the saved skills like anyone else's (npc.npc_power).

A new definition version never resets a live character (`sync_npc`): only the display name is brought up to date,
and flags and relationships the definition gained are added. Age, status, location, attributes and existing flags
and links are runtime state and are left alone.

Nothing here commits: like npc_store and relationships, the caller owns the transaction.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Mapping

from sqlmodel import Session

from .content import Content, get_content
from .models import Campaign
from .npc import (
    CharacterCategory, ID_PREFIX, LifecycleStatus, NamedCharacterDef, Npc, NpcSpec, _fill, get_named_characters,
    named_npc,
)
from .known import record_known
from .npc_store import list_npcs, load_npc, save_npc
from .power import PowerConfig, get_power_config
from .relationships import RelationshipKind, add_relationship, relationships_of
from .story.scalars import Scalar, is_number

Definitions = Mapping[str, NamedCharacterDef]


class NamedError(ValueError):
    """A request about a named character that cannot be carried out."""


class UnknownDefinitionError(NamedError):
    pass


class NotInstantiatedError(NamedError):
    pass


class ImmortalError(NamedError):
    pass


def definitions() -> Definitions:
    """The authored definitions. One accessor, so tests (and later tooling) can substitute their own."""
    return get_named_characters()


def runtime_id(definition_id: str) -> str:
    """The stable character id of a definition's instance."""
    return ID_PREFIX["named"] + definition_id


def _definition(defs: Definitions | None, definition_id: str) -> NamedCharacterDef:
    d = (defs if defs is not None else definitions()).get(definition_id)
    if d is None:
        raise UnknownDefinitionError(f"no named character '{definition_id}' is defined")
    return d


# ---------------------------------------------------------------- starting attributes

def named_seed(campaign_seed: int, definition_id: str) -> int:
    """The seed for what `generate` allows: fixed for one campaign and one character, different between campaigns."""
    return random.Random(f"{campaign_seed}:named:{definition_id}").getrandbits(63)


def build_npc(d: NamedCharacterDef, ct: Content | None = None, cfg: PowerConfig | None = None,
              campaign_seed: int = 0) -> Npc:
    """A new character from a definition. Pure: nothing is saved.

    Authored values are taken exactly as written. Only the parts `generate` allows are generated (and only the
    parts not authored): base stats not listed, specialised stats if none are listed, extra random traits, and a
    hidden talent if none is given. The generator is the existing one (npc._fill); this only decides which of its
    results are used."""
    ct, cfg = ct or get_content(), cfg or get_power_config()
    npc = named_npc(d, ct, cfg)
    g = d.generate
    if not g.any:
        return npc
    spec = NpcSpec(
        category=CharacterCategory.PERSISTENT, species=d.species, stats=dict(d.stats or {}), stat_range=g.stat_range,
        traits=list(d.traits), random_traits=g.random_traits, talent=d.talent, talent_chance=g.talent_chance,
        combat=None if g.combat else {}, combat_focus=g.combat_focus, combat_level=g.combat_level)
    scratch = Npc(id="npc_scratch", category=CharacterCategory.PERSISTENT, name=d.name, species_id=d.species,
                  age=d.age, seed=named_seed(campaign_seed, d.id), spec=spec)
    filled = _fill(scratch, ct, cfg)
    if g.stats and npc.stats is None:
        npc.stats = filled["stats"]                           # authored stats inside it are carried through as given
    if g.combat:
        npc.skills = filled["skills"]
    if g.random_traits:
        npc.traits = filled["traits"]
    if g.talent_chance and d.talent is None:
        npc.talent, npc.talent_state = filled["talent"], filled["talent_state"]
    return npc


# ---------------------------------------------------------------- creating and finding

@dataclass
class SyncReport:
    version_from: int | None
    version_to: int | None
    changed: list[str] = field(default_factory=list)       # what a newer definition updated
    orphan: bool = False                                   # the definition no longer exists

    @property
    def updated(self) -> bool:
        return bool(self.changed) or self.version_from != self.version_to


@dataclass
class Instance:
    npc: Npc
    created: bool                                          # False: this character already existed
    sync: SyncReport | None = None


def get_instance(session: Session, campaign_id: int, definition_id: str) -> Npc | None:
    return load_npc(session, campaign_id, runtime_id(definition_id))


def instantiate(session: Session, campaign_id: int, definition_id: str, *, defs: Definitions | None = None,
                ct: Content | None = None, cfg: PowerConfig | None = None) -> Instance:
    """Bring a named character into the campaign, once. Calling again (from another encounter, after a reload,
    on a retry) returns the character that already exists, with their runtime state, and creates nothing.
    A dead or retired character still exists: they are not created again."""
    d = _definition(defs, definition_id)
    existing = get_instance(session, campaign_id, definition_id)
    if existing is not None:
        report = sync_npc(session, campaign_id, existing, d, defs)
        record_known(session, campaign_id, existing.id, "story")
        return Instance(get_instance(session, campaign_id, definition_id), False, report)
    campaign = session.get(Campaign, campaign_id)
    npc = build_npc(d, ct, cfg, campaign.seed if campaign else 0)
    save_npc(session, campaign_id, npc)
    apply_relationships(session, campaign_id, definition_id, defs)
    record_known(session, campaign_id, npc.id, "story")            # introduced by the story: the player has met them
    return Instance(get_instance(session, campaign_id, definition_id), True)


def apply_relationships(session: Session, campaign_id: int, definition_id: str, defs: Definitions | None = None) -> int:
    """Create the authored links this character is part of whose other end exists. Each is created once, so this
    is safe to repeat; a link whose other character has not been introduced yet is made when they are.
    Returns how many links were NEWLY created."""
    defs = defs if defs is not None else definitions()
    made = 0
    for owner in defs.values():
        for r in owner.relationships:
            if definition_id not in (owner.id, r.other):
                continue
            if get_instance(session, campaign_id, owner.id) is None or get_instance(session, campaign_id, r.other) is None:
                continue
            a, b = runtime_id(owner.id), runtime_id(r.other)
            source, target = (a, b) if r.self_is == "source" else (b, a)
            if any({l.source_id, l.target_id} == {source, target}
                   for l in relationships_of(session, campaign_id, source, r.kind)):
                continue                                           # already there: leave it exactly as it is
            add_relationship(session, campaign_id, RelationshipKind(r.kind), source, target)
            made += 1
    return made


# ---------------------------------------------------------------- definition updates

def sync_npc(session: Session, campaign_id: int, npc: Npc, d: NamedCharacterDef | None,
             defs: Definitions | None = None) -> SyncReport:
    """Bring an existing character up to a newer definition WITHOUT resetting them.

    Updated: the display name, the version it was synced to, flags the definition gained (never an existing
    value), and links the definition gained. Never touched: age, status, location, species, base and specialised
    stats, traits, talent, existing flags and existing links. Those are runtime state, and a definition describes
    where a character STARTS."""
    if d is None:
        return SyncReport(npc.definition_version, npc.definition_version, orphan=True)
    if npc.definition_version == d.version:
        return SyncReport(d.version, d.version)
    report = SyncReport(npc.definition_version, d.version)
    if npc.name != d.name:
        npc.name = d.name
        report.changed.append("name")
    gained = {k: v for k, v in d.flags.items() if k not in npc.flags}
    if gained:
        npc.flags = {**npc.flags, **gained}
        report.changed.append("flags")
    npc.definition_version = d.version
    save_npc(session, campaign_id, npc)
    if apply_relationships(session, campaign_id, d.id, defs):
        report.changed.append("relationships")
    return report


# ---------------------------------------------------------------- lifecycle

@dataclass(frozen=True)
class AgeReport:
    npc_id: str
    age_before: int
    age_after: int
    died: bool


def max_age(d: NamedCharacterDef, ct: Content | None = None) -> float | None:
    """The age past which this character dies of old age, or None if they never do."""
    if d.lifecycle.ageing == "immortal":
        return None
    return (ct or get_content()).species[d.species].lifespan * d.lifecycle.lifespan_multiplier


def advance_years(npc: Npc, d: NamedCharacterDef, years: int, ct: Content | None = None) -> AgeReport:
    """Age a character by their lifecycle rule. Pure: it changes the NPC it is given and saves nothing.

    normal and longevity characters age, and die of old age once past their lifespan (times the multiplier);
    an immortal one does not age at all; the dead and the already-ageless are left alone."""
    if years < 0:
        raise NamedError("years cannot be negative")
    before = npc.age
    if years == 0 or not npc.alive or d.lifecycle.ageing == "immortal":
        return AgeReport(npc.id, before, before, False)
    npc.age += years
    limit = max_age(d, ct)
    died = limit is not None and npc.age > limit
    if died:
        npc.mark_deceased("old age")
    return AgeReport(npc.id, before, npc.age, died)


def advance_all(session: Session, campaign_id: int, years: int = 1, *, defs: Definitions | None = None,
                ct: Content | None = None) -> list[AgeReport]:
    """The world's clock moved on: age every named character of this campaign who has been introduced. Characters
    who were never instantiated do not exist yet, so they do not age. Returns who changed."""
    defs = defs if defs is not None else definitions()
    out = []
    for npc in list_npcs(session, campaign_id, category=CharacterCategory.NAMED):
        d = defs.get(npc.definition or "")
        if d is None or not npc.alive:
            continue
        report = advance_years(npc, d, years, ct)
        if report.age_after != report.age_before:
            save_npc(session, campaign_id, npc)
            out.append(report)
    return out


def set_status(session: Session, campaign_id: int, definition_id: str, status: LifecycleStatus | str,
               cause: str | None = None, *, defs: Definitions | None = None) -> Npc:
    """Change who is alive, retired or active, as a story rule decides. An immortal character cannot be killed, and
    the dead cannot be brought back or retired."""
    status = LifecycleStatus(status)
    npc = _instance(session, campaign_id, definition_id)
    d = (defs if defs is not None else definitions()).get(definition_id)
    if status == LifecycleStatus.DECEASED:
        if d is not None and d.lifecycle.ageing == "immortal":
            raise ImmortalError(f"{npc.name} is immortal and cannot die")
        if npc.status != LifecycleStatus.DECEASED:
            npc.mark_deceased(cause)
    else:
        try:
            npc.retire() if status == LifecycleStatus.RETIRED else npc.reactivate()
        except ValueError as e:
            raise NamedError(str(e)) from None
    save_npc(session, campaign_id, npc)
    return npc


# ---------------------------------------------------------------- story flags and links

def _instance(session: Session, campaign_id: int, definition_id: str) -> Npc:
    npc = get_instance(session, campaign_id, definition_id)
    if npc is None:
        raise NotInstantiatedError(f"'{definition_id}' has not been introduced in this game")
    return npc


def set_flag(session: Session, campaign_id: int, definition_id: str, flag: str, value: Scalar) -> Npc:
    npc = _instance(session, campaign_id, definition_id)
    npc.flags = {**npc.flags, flag: value}
    save_npc(session, campaign_id, npc)
    return npc


def increment_flag(session: Session, campaign_id: int, definition_id: str, flag: str, amount: float = 1) -> Npc:
    npc = _instance(session, campaign_id, definition_id)
    current = npc.flags.get(flag, 0)
    if not is_number(current):
        raise NamedError(f"cannot increment flag '{flag}' of {npc.name}: it holds the non-numeric value {current!r}")
    npc.flags = {**npc.flags, flag: current + amount}
    save_npc(session, campaign_id, npc)
    return npc


def link(session: Session, campaign_id: int, definition_id: str, kind: RelationshipKind | str, other_ref: str,
         *, self_is: str = "source") -> None:
    """Relate an introduced named character to another character (by stable id, e.g. the player's `pc_<n>`)."""
    npc = _instance(session, campaign_id, definition_id)
    source, target = (npc.id, other_ref) if self_is == "source" else (other_ref, npc.id)
    add_relationship(session, campaign_id, RelationshipKind(kind), source, target)
