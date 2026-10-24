"""Family queries and validation, built only on the existing parent links (relationships.py).

Queries (read-only, a handful of queries each however large the family: every step is one batched lookup):

  shared_parents(a, b)     the parents two characters have in common
  siblings_of(ref)         full siblings (both parents shared, or the only known parent shared and the other unknown on
                           both sides) and half-siblings (some, not all, parents shared)
  cousins_of(ref)          first cousins: children of a parent's siblings or half-siblings, with the grandparents shared
  relations_between(a, b)  every relation that holds: parent, child, sibling, half_sibling, cousin, ancestor, descendant
  family_of(ref)           all of it in one bundle (what the future Character tab needs)

Ancestors and descendants are relationships.ancestors_of / descendants_of. NOTHING here prohibits a relationship:
siblings and cousins are only identified, so a game rule can decide what to do with them (see inheritance.yaml
`parents.forbidden_relations`, empty by default).

Incomplete trees are normal: a missing parent, a deleted character or a one-parent child just means fewer results. Two
siblings who each have one unknown parent are reported as half-siblings only when a real parent is shared, never guessed.

`validate_family` checks a whole campaign's links (self-parentage, duplicates, more than two parents, circular ancestry,
links to characters that no longer exist) so a save/load cycle can be verified. It reports and changes nothing; the
write path (relationships.add_relationship) already refuses each of these.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

from sqlmodel import Session, col, select

from .models import CharacterRelationship
from .relationships import (
    MAX_PARENTS, RelationshipKind, ancestors_of, descendants_of, existing_characters,
)

PARENT = RelationshipKind.PARENT.value


# ---------------------------------------------------------------- batched parent lookups

def _parents_map(session: Session, campaign_id: int, children: Iterable[str]) -> dict[str, list[str]]:
    """child -> live parents, for many children in two queries. Order follows link order."""
    kids = list(dict.fromkeys(children))
    if not kids:
        return {}
    rows = list(session.exec(select(CharacterRelationship.target_id, CharacterRelationship.source_id).where(
        CharacterRelationship.campaign_id == campaign_id, CharacterRelationship.kind == PARENT,
        col(CharacterRelationship.target_id).in_(kids)).order_by(CharacterRelationship.id)))
    live = existing_characters(session, campaign_id, {p for _, p in rows})
    out: dict[str, list[str]] = {k: [] for k in kids}
    for child, parent in rows:
        if parent in live and parent not in out[child]:
            out[child].append(parent)
    return out


def _children_map(session: Session, campaign_id: int, parents: Iterable[str]) -> dict[str, list[str]]:
    """parent -> live children, for many parents in two queries."""
    ps = list(dict.fromkeys(parents))
    if not ps:
        return {}
    rows = list(session.exec(select(CharacterRelationship.source_id, CharacterRelationship.target_id).where(
        CharacterRelationship.campaign_id == campaign_id, CharacterRelationship.kind == PARENT,
        col(CharacterRelationship.source_id).in_(ps)).order_by(CharacterRelationship.id)))
    live = existing_characters(session, campaign_id, {c for _, c in rows})
    out: dict[str, list[str]] = {p: [] for p in ps}
    for parent, child in rows:
        if child in live and child not in out[parent]:
            out[parent].append(child)
    return out


# ---------------------------------------------------------------- siblings

@dataclass(frozen=True)
class Sibling:
    ref: str
    kind: Literal["full", "half"]
    shared_parents: tuple[str, ...]


def shared_parents(session: Session, campaign_id: int, a: str, b: str) -> list[str]:
    pm = _parents_map(session, campaign_id, [a, b])
    return [p for p in pm[a] if p in pm[b]] if a != b else []


def _sibling_list(session: Session, campaign_id: int, ref: str, parents: list[str]) -> list[Sibling]:
    if not parents:
        return []
    cm = _children_map(session, campaign_id, parents)
    candidates = list(dict.fromkeys(c for p in parents for c in cm[p] if c != ref))
    pm = _parents_map(session, campaign_id, candidates)
    out = []
    for c in candidates:
        shared = tuple(p for p in parents if p in pm[c])
        # Full: every known parent of both is shared. A one-parent child and a two-parent child sharing that parent
        # are half-siblings: the second parent of one has no counterpart on the other.
        full = len(shared) > 0 and set(pm[c]) == set(parents)
        out.append(Sibling(c, "full" if full else "half", shared))
    return out


def siblings_of(session: Session, campaign_id: int, ref: str) -> list[Sibling]:
    """Full and half siblings, in the order their parents' links were made. Never includes the character themselves."""
    return _sibling_list(session, campaign_id, ref, _parents_map(session, campaign_id, [ref])[ref])


def full_siblings_of(session: Session, campaign_id: int, ref: str) -> list[str]:
    return [s.ref for s in siblings_of(session, campaign_id, ref) if s.kind == "full"]


def half_siblings_of(session: Session, campaign_id: int, ref: str) -> list[str]:
    return [s.ref for s in siblings_of(session, campaign_id, ref) if s.kind == "half"]


# ---------------------------------------------------------------- cousins

@dataclass(frozen=True)
class Cousin:
    ref: str
    shared_grandparents: tuple[str, ...]


def cousins_of(session: Session, campaign_id: int, ref: str) -> list[Cousin]:
    """First cousins: the children of any sibling or half-sibling of one of the character's parents. Needs the
    grandparents to be known; without them there is nothing to go on and the result is empty."""
    parents = _parents_map(session, campaign_id, [ref])[ref]
    if not parents:
        return []
    gp_map = _parents_map(session, campaign_id, parents)
    grandparents = list(dict.fromkeys(g for p in parents for g in gp_map[p]))
    if not grandparents:
        return []
    cm = _children_map(session, campaign_id, grandparents)
    aunts_uncles = list(dict.fromkeys(c for g in grandparents for c in cm[g] if c not in parents))
    kids = _children_map(session, campaign_id, aunts_uncles)
    cousin_ids = [c for au in aunts_uncles for c in kids[au] if c != ref]
    cousin_ids = list(dict.fromkeys(cousin_ids))
    own_gp = _parents_map(session, campaign_id, aunts_uncles)               # which grandparents each aunt/uncle shares
    out = []
    for cousin in cousin_ids:
        shared: list[str] = []
        for au in aunts_uncles:
            if cousin in kids[au]:
                shared += [g for g in own_gp[au] if g in grandparents and g not in shared]
        out.append(Cousin(cousin, tuple(shared)))
    return out


# ---------------------------------------------------------------- relations between two characters

Relation = Literal["parent", "child", "sibling", "half_sibling", "cousin", "ancestor", "descendant"]


def relations_between(session: Session, campaign_id: int, a: str, b: str) -> list[Relation]:
    """How `b` is related to `a` ("b is a's <relation>"), every one that holds. Empty if unrelated (or a == b).
    `parent`/`child` are the direct case of `ancestor`/`descendant` and both are listed."""
    if a == b:
        return []
    out: list[Relation] = []
    pm = _parents_map(session, campaign_id, [a, b])
    if b in pm[a]:
        out.append("parent")
    if a in pm[b]:
        out.append("child")
    sib = next((s for s in siblings_of(session, campaign_id, a) if s.ref == b), None)
    if sib is not None:
        out.append("sibling" if sib.kind == "full" else "half_sibling")
    if any(c.ref == b for c in cousins_of(session, campaign_id, a)):
        out.append("cousin")
    if b in ancestors_of(session, campaign_id, a):
        out.append("ancestor")
    if b in descendants_of(session, campaign_id, a):
        out.append("descendant")
    return out


def pair_relations(session: Session, campaign_id: int, parents: list[str]) -> list[str]:
    """Which of the forbidden-able relations hold between two prospective parents (for inheritance.yaml's
    `forbidden_relations`): sibling, half_sibling, cousin."""
    if len(parents) != 2:
        return []
    kinds = relations_between(session, campaign_id, parents[0], parents[1])
    return [k for k in kinds if k in ("sibling", "half_sibling", "cousin")]


# ---------------------------------------------------------------- the whole picture

@dataclass
class Family:
    ref: str
    parents: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    siblings: list[Sibling] = field(default_factory=list)
    grandparents: list[str] = field(default_factory=list)
    cousins: list[Cousin] = field(default_factory=list)
    ancestors: list[str] = field(default_factory=list)
    descendants: list[str] = field(default_factory=list)


def family_of(session: Session, campaign_id: int, ref: str, *, max_depth: int = 8) -> Family:
    """Everything the family queries know about one character. Ancestors and descendants are bounded by `max_depth`
    generations."""
    parents = _parents_map(session, campaign_id, [ref])[ref]
    children = _children_map(session, campaign_id, [ref])[ref]
    gp_map = _parents_map(session, campaign_id, parents)
    return Family(
        ref=ref, parents=parents, children=children,
        siblings=_sibling_list(session, campaign_id, ref, parents),
        grandparents=list(dict.fromkeys(g for p in parents for g in gp_map[p])),
        cousins=cousins_of(session, campaign_id, ref),
        ancestors=ancestors_of(session, campaign_id, ref, max_depth=max_depth),
        descendants=descendants_of(session, campaign_id, ref, max_depth=max_depth))


# ---------------------------------------------------------------- validation

@dataclass(frozen=True)
class FamilyIssue:
    kind: Literal["self_parent", "duplicate_link", "too_many_parents", "circular_ancestry", "dangling_link"]
    refs: tuple[str, ...]
    detail: str


def validate_family(session: Session, campaign_id: int) -> list[FamilyIssue]:
    """Check every parent link of a campaign. Read-only. An empty list means the family is consistent. The write path
    refuses all of these; this exists to verify data that was edited by hand or came from an older version."""
    links = list(session.exec(select(CharacterRelationship).where(
        CharacterRelationship.campaign_id == campaign_id, CharacterRelationship.kind == PARENT)
        .order_by(CharacterRelationship.id)))
    issues: list[FamilyIssue] = []
    seen: set[tuple[str, str]] = set()
    parents: dict[str, list[str]] = {}
    for link in links:
        edge = (link.source_id, link.target_id)
        if link.source_id == link.target_id:
            issues.append(FamilyIssue("self_parent", (link.source_id,), f"{link.source_id} is recorded as their own parent"))
            continue
        if edge in seen:
            issues.append(FamilyIssue("duplicate_link", edge, f"{edge[0]} is linked as parent of {edge[1]} more than once"))
            continue
        seen.add(edge)
        parents.setdefault(link.target_id, []).append(link.source_id)
    for child, ps in sorted(parents.items()):
        if len(ps) > MAX_PARENTS:
            issues.append(FamilyIssue("too_many_parents", (child, *ps), f"{child} has {len(ps)} parents (at most {MAX_PARENTS})"))
    ends = {x for link in links for x in (link.source_id, link.target_id)}
    live = existing_characters(session, campaign_id, ends)
    issues += [FamilyIssue("dangling_link", (ref,), f"{ref} is in a parent link but no longer exists") for ref in sorted(ends - live)]
    issues += _cycles(parents)
    return issues


def _cycles(parents: dict[str, list[str]]) -> list[FamilyIssue]:
    """Each distinct circular ancestry once, found by an iterative depth-first walk up the parent links."""
    state: dict[str, int] = {}                     # 1 = on the current path, 2 = finished
    found: list[FamilyIssue] = []
    reported: set[frozenset] = set()
    for start in sorted(parents):
        if state.get(start):
            continue
        stack = [(start, iter(parents.get(start, ())))]
        path = [start]
        state[start] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                path.pop()
                continue
            if state.get(nxt) == 1:
                cycle = path[path.index(nxt):]
                key = frozenset(cycle)
                if key not in reported:
                    reported.add(key)
                    found.append(FamilyIssue("circular_ancestry", tuple(cycle), "circular ancestry: " + " -> ".join([*cycle, nxt])))
            elif not state.get(nxt):
                state[nxt] = 1
                path.append(nxt)
                stack.append((nxt, iter(parents.get(nxt, ()))))
    return found
