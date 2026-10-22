"""Relationships between characters: parent and child, partners, and other social links.

Characters are referenced by stable id, never by name or row number: an NPC's `Npc.id`, or `pc_<n>` for the
campaign's playable character (`player_ref`). Only characters that are *kept* can be related: persistent and named
NPCs and the player. A temporary (generic) NPC has no place in a family tree, so it must be promoted first.

Rules this module enforces, because the database cannot:
  * a character is never related to itself;
  * both ends exist (and belong to this campaign);
  * a child has at most two parents, and may have fewer (one, or none known);
  * parentage is never circular: nobody is their own ancestor.
It does NOT calculate inheritance, pick heirs or move anyone between lives; those are later tasks.

Retry-safe: adding a link that already exists returns it instead of failing or duplicating, and the unique
constraint is the backstop if two writers race. Nothing here commits; the caller owns the transaction.

Missing targets are tolerated: if a character vanishes (deleted by hand, or by an older version) its links are
simply not reported, and `prune_dangling` removes them. Deleting a character through npc_store removes its links.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Iterable

from sqlalchemy import delete, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from .models import CharacterRecord, CharacterRelationship, NpcRecord
from .npc import CharacterCategory, LifecycleStatus, Npc

PLAYER_PREFIX = "pc_"
MAX_PARENTS = 2
MAX_DEPTH = 64                          # a safety net for the family walks; real trees are far shallower


class RelationshipKind(StrEnum):
    PARENT = "parent"                   # source is the parent of target
    MENTOR = "mentor"                   # source mentors target
    PARTNER = "partner"                 # symmetric. No gameplay uses it yet; the model is ready for it.
    FRIEND = "friend"                   # symmetric
    RIVAL = "rival"                     # symmetric


DIRECTED = {RelationshipKind.PARENT, RelationshipKind.MENTOR}
SYMMETRIC = {RelationshipKind.PARTNER, RelationshipKind.FRIEND, RelationshipKind.RIVAL}
Kind = RelationshipKind | str


class RelationshipError(ValueError):
    """The relationship is not allowed."""


class SelfRelationshipError(RelationshipError):
    pass


class UnknownCharacterError(RelationshipError):
    pass


class TemporaryCharacterError(RelationshipError):
    pass


class TooManyParentsError(RelationshipError):
    pass


class CircularParentageError(RelationshipError):
    pass


def player_ref(record: CharacterRecord) -> str:
    """The stable reference to the campaign's playable character."""
    if record.id is None:
        raise ValueError("save the playable character before referring to it")
    return f"{PLAYER_PREFIX}{record.id}"


def _kind(kind: Kind) -> RelationshipKind:
    try:
        return RelationshipKind(kind)
    except ValueError:
        raise RelationshipError(f"unknown relationship kind '{kind}' (use {[k.value for k in RelationshipKind]})")


# ---------------------------------------------------------------- do the characters exist?

def _npc_rows(session: Session, campaign_id: int, ids: Iterable[str]) -> dict[str, NpcRecord]:
    ids = [i for i in set(ids) if not i.startswith(PLAYER_PREFIX)]
    if not ids:
        return {}
    rows = session.exec(select(NpcRecord).where(NpcRecord.campaign_id == campaign_id, col(NpcRecord.npc_id).in_(ids)))
    return {r.npc_id: r for r in rows}


def _player_ids(session: Session, campaign_id: int, refs: Iterable[str]) -> set[str]:
    numbers = {int(r[len(PLAYER_PREFIX):]) for r in refs if r.startswith(PLAYER_PREFIX) and r[len(PLAYER_PREFIX):].isdigit()}
    if not numbers:
        return set()
    found = session.exec(select(CharacterRecord.id).where(CharacterRecord.campaign_id == campaign_id,
                                                          col(CharacterRecord.id).in_(numbers)))
    return {f"{PLAYER_PREFIX}{n}" for n in found}


def existing_characters(session: Session, campaign_id: int, ids: Iterable[str]) -> set[str]:
    """Which of these ids are kept characters of this campaign. Temporary NPCs do not count."""
    ids = list(ids)
    kept = {i for i, r in _npc_rows(session, campaign_id, ids).items() if r.category != CharacterCategory.GENERIC.value}
    return kept | _player_ids(session, campaign_id, ids)


def _require_kept(session: Session, campaign_id: int, ref: str) -> None:
    if ref in existing_characters(session, campaign_id, [ref]):
        return
    row = _npc_rows(session, campaign_id, [ref]).get(ref)
    if row is not None:
        raise TemporaryCharacterError(f"{row.name} is a temporary NPC; promote it to persistent before relating it")
    raise UnknownCharacterError(f"no character '{ref}' in campaign {campaign_id}")


# ---------------------------------------------------------------- reading links

def _edge(session: Session, campaign_id: int, kind: str, source: str, target: str) -> CharacterRelationship | None:
    return session.exec(select(CharacterRelationship).where(
        CharacterRelationship.campaign_id == campaign_id, CharacterRelationship.kind == kind,
        CharacterRelationship.source_id == source, CharacterRelationship.target_id == target)).first()


def relationships_of(session: Session, campaign_id: int, ref: str,
                     kind: Kind | None = None) -> list[CharacterRelationship]:
    """Every link this character is part of, in either direction (links to missing characters included)."""
    query = select(CharacterRelationship).where(
        CharacterRelationship.campaign_id == campaign_id,
        or_(CharacterRelationship.source_id == ref, CharacterRelationship.target_id == ref)
    ).order_by(CharacterRelationship.id)
    if kind is not None:
        query = query.where(CharacterRelationship.kind == _kind(kind).value)
    return list(session.exec(query))


def _neighbours(session: Session, campaign_id: int, refs: Iterable[str], kind: RelationshipKind,
                *, towards_target: bool) -> list[str]:
    """One query: the ids on the far side of `kind` links from all of `refs`. Order follows insertion."""
    refs = list(refs)
    if not refs:
        return []
    near, far = ((CharacterRelationship.source_id, CharacterRelationship.target_id) if towards_target
                 else (CharacterRelationship.target_id, CharacterRelationship.source_id))
    return list(session.exec(select(far).where(
        CharacterRelationship.campaign_id == campaign_id, CharacterRelationship.kind == kind.value,
        col(near).in_(refs)).order_by(CharacterRelationship.id)))


def parents_of(session: Session, campaign_id: int, child: str) -> list[str]:
    """The child's parents: none, one or two, oldest link first. Parents that no longer exist are left out."""
    found = _neighbours(session, campaign_id, [child], RelationshipKind.PARENT, towards_target=False)
    live = existing_characters(session, campaign_id, found)
    return [p for p in found if p in live]


def children_of(session: Session, campaign_id: int, parent: str) -> list[str]:
    found = _neighbours(session, campaign_id, [parent], RelationshipKind.PARENT, towards_target=True)
    live = existing_characters(session, campaign_id, found)
    return [c for c in found if c in live]


def related_to(session: Session, campaign_id: int, ref: str, kind: Kind) -> list[str]:
    """Characters linked to `ref` by `kind`, whichever side of the link they are on (partners, friends, mentees...)."""
    kind = _kind(kind)
    if kind in SYMMETRIC:
        found = (_neighbours(session, campaign_id, [ref], kind, towards_target=True)
                 + _neighbours(session, campaign_id, [ref], kind, towards_target=False))
    else:
        found = _neighbours(session, campaign_id, [ref], kind, towards_target=True)
    live = existing_characters(session, campaign_id, found)
    return [f for f in dict.fromkeys(found) if f in live]


def _walk(session: Session, campaign_id: int, start: str, *, down: bool, max_depth: int = MAX_DEPTH) -> list[str]:
    """Breadth-first over parent links: one query per generation, nearest generation first. A visited set means
    even a corrupt, circular tree cannot loop forever."""
    seen, order, frontier = {start}, [], [start]
    for _ in range(max_depth):
        found = _neighbours(session, campaign_id, frontier, RelationshipKind.PARENT, towards_target=down)
        frontier = [f for f in dict.fromkeys(found) if f not in seen]
        if not frontier:
            break
        seen.update(frontier)
        order.extend(frontier)
    live = existing_characters(session, campaign_id, order)
    return [o for o in order if o in live]


def ancestors_of(session: Session, campaign_id: int, ref: str, *, max_depth: int = MAX_DEPTH) -> list[str]:
    return _walk(session, campaign_id, ref, down=False, max_depth=max_depth)


def descendants_of(session: Session, campaign_id: int, ref: str, *, max_depth: int = MAX_DEPTH) -> list[str]:
    """Children, then grandchildren, and so on. Ids only; they may be NPCs or the playable character."""
    return _walk(session, campaign_id, ref, down=True, max_depth=max_depth)


def eligible_descendants(session: Session, campaign_id: int, ref: str, *, max_depth: int = MAX_DEPTH) -> list[Npc]:
    """The descendants who could still take part in the story: NPCs whose status is active (not dead, not retired),
    nearest generation first. This only filters; choosing among them is a later feature."""
    ids = descendants_of(session, campaign_id, ref, max_depth=max_depth)
    rows = _npc_rows(session, campaign_id, ids)
    return [Npc.model_validate(rows[i].data) for i in ids
            if i in rows and rows[i].status == LifecycleStatus.ACTIVE.value]


# ---------------------------------------------------------------- writing links

def add_relationship(session: Session, campaign_id: int, kind: Kind, source: str, target: str) -> CharacterRelationship:
    """Create a link, or return the one that already exists (so a retry is harmless). For `parent`, source is the
    parent and target the child. Raises a RelationshipError subclass when the link is not allowed."""
    kind = _kind(kind)
    if source == target:
        raise SelfRelationshipError(f"'{source}' cannot be related to itself")
    if kind in SYMMETRIC:
        source, target = sorted((source, target))
    _require_kept(session, campaign_id, source)
    _require_kept(session, campaign_id, target)

    existing = _edge(session, campaign_id, kind.value, source, target)
    if existing:
        return existing
    if kind == RelationshipKind.PARENT:
        _check_parentage(session, campaign_id, parent=source, child=target)

    link = CharacterRelationship(campaign_id=campaign_id, kind=kind.value, source_id=source, target_id=target)
    try:
        with session.begin_nested():                    # a savepoint: a lost race must not undo the caller's work
            session.add(link)
            session.flush()
    except IntegrityError:
        return _edge(session, campaign_id, kind.value, source, target) or _raise_lost(link)
    return link


def _raise_lost(link: CharacterRelationship):                                   # pragma: no cover
    raise RelationshipError(f"could not save the {link.kind} link between {link.source_id} and {link.target_id}")


def _check_parentage(session: Session, campaign_id: int, *, parent: str, child: str) -> None:
    current = _neighbours(session, campaign_id, [child], RelationshipKind.PARENT, towards_target=False)
    if len(set(current)) >= MAX_PARENTS:
        raise TooManyParentsError(f"'{child}' already has {MAX_PARENTS} parents")
    if child in ancestors_of(session, campaign_id, parent):
        raise CircularParentageError(f"'{child}' is an ancestor of '{parent}', so they cannot also be its child")


def add_parent(session: Session, campaign_id: int, child: str, *parents: str) -> list[CharacterRelationship]:
    """Give a child one or two parents in one go. Fewer than two is fine. All-or-nothing: if any parent is
    refused, none are added (the savepoint is rolled back, the rest of the caller's work is kept)."""
    if len(set(parents)) > MAX_PARENTS:
        raise TooManyParentsError(f"a child has at most {MAX_PARENTS} parents")
    if len(set(parents)) != len(parents):
        raise RelationshipError("the same parent was listed twice")
    with session.begin_nested():
        return [add_relationship(session, campaign_id, RelationshipKind.PARENT, p, child) for p in parents]


def remove_relationship(session: Session, campaign_id: int, kind: Kind, source: str, target: str) -> bool:
    kind = _kind(kind)
    if kind in SYMMETRIC:
        source, target = sorted((source, target))
    link = _edge(session, campaign_id, kind.value, source, target)
    if link is None:
        return False
    session.delete(link)
    session.flush()
    return True


def remove_all_for(session: Session, campaign_id: int, ref: str) -> int:
    """Remove every link a character is part of (used when the character itself is deleted)."""
    result = session.exec(delete(CharacterRelationship).where(
        CharacterRelationship.campaign_id == campaign_id,
        or_(CharacterRelationship.source_id == ref, CharacterRelationship.target_id == ref)))
    session.flush()
    return result.rowcount or 0


def rekey_character(session: Session, campaign_id: int, old: str, new: str) -> int:
    """Point every link of `old` at `new`, for a character whose id changes (an NPC who becomes the playable
    character). Nothing is lost and nothing is added: the same links, the same kinds, the same other ends.
    Symmetric links are stored with their two ids in sorted order, so those are put back in order."""
    if old == new:
        return 0
    links = relationships_of(session, campaign_id, old)
    for link in links:
        if link.source_id == old:
            link.source_id = new
        if link.target_id == old:
            link.target_id = new
        if RelationshipKind(link.kind) in SYMMETRIC and link.source_id > link.target_id:
            link.source_id, link.target_id = link.target_id, link.source_id
        session.add(link)
    session.flush()
    return len(links)


def prune_dangling(session: Session, campaign_id: int) -> int:
    """Remove links whose characters no longer exist. Returns how many were removed."""
    links = list(session.exec(select(CharacterRelationship).where(CharacterRelationship.campaign_id == campaign_id)))
    live = existing_characters(session, campaign_id, {x for link in links for x in (link.source_id, link.target_id)})
    dead = [link for link in links if link.source_id not in live or link.target_id not in live]
    for link in dead:
        session.delete(link)
    session.flush()
    return len(dead)
