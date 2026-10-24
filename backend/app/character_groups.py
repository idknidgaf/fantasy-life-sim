"""The three groups of the Character tab, derived from data that already exists (no new tables, no stored copies).

  family          NPCs connected to the player through family links (parent, child, sibling, half-sibling, grandparent,
                  cousin, further ancestors and descendants: family.family_of), and known to the player.
  acquaintances   PERSISTENT NPCs with an established personal relationship to the player: a friend, partner, rival or
                  mentor/mentee link (relationships.py). Family links alone do not make an acquaintance.
  notable         NAMED NPCs the player knows of: introduced by a story, encountered, or linked to. No personal
                  relationship is needed: awareness is enough (known.py).

An NPC may be in several groups; within one group it appears once (these are sets of ids and the list is a SQL query over
one row per NPC). Read-only.
"""
from __future__ import annotations

from typing import Literal

from sqlalchemy import or_
from sqlmodel import Session, select

from . import family as fam
from .models import CharacterRelationship
from .relationships import RelationshipKind

Category = Literal["family", "acquaintances", "notable"]
CATEGORIES: tuple[Category, ...] = ("family", "acquaintances", "notable")

PERSONAL_KINDS = (RelationshipKind.FRIEND.value, RelationshipKind.PARTNER.value, RelationshipKind.RIVAL.value,
                  RelationshipKind.MENTOR.value)
NPC_PREFIXES = ("npc_", "named_")
FAMILY_DEPTH = 4                    # generations of ancestors and descendants considered family


def _npcs_only(refs) -> list[str]:
    return [r for r in dict.fromkeys(refs) if r.startswith(NPC_PREFIXES)]      # drops pc_<n>: earlier lives are not NPCs


def family_roles(session: Session, campaign_id: int, player: str) -> dict[str, str]:
    """NPC id -> their most specific family relation to the player ("parent", "child", "sibling", "half_sibling",
    "grandparent", "cousin", "ancestor" or "descendant"). The keys are the player's NPC family."""
    f = fam.family_of(session, campaign_id, player, max_depth=FAMILY_DEPTH)
    ordered = [("parent", f.parents), ("child", f.children),
               ("sibling", [s.ref for s in f.siblings if s.kind == "full"]),
               ("half_sibling", [s.ref for s in f.siblings if s.kind == "half"]),
               ("grandparent", f.grandparents), ("cousin", [c.ref for c in f.cousins]),
               ("ancestor", f.ancestors), ("descendant", f.descendants)]
    roles: dict[str, str] = {}
    for role, refs in ordered:
        for ref in _npcs_only(refs):
            roles.setdefault(ref, role)                # the first, most specific role wins
    return roles


def personal_link_ids(session: Session, campaign_id: int, player: str) -> list[str]:
    """Every character with a friend, partner, rival or mentor link to the player (either direction), once each."""
    rows = session.exec(select(CharacterRelationship.source_id, CharacterRelationship.target_id).where(
        CharacterRelationship.campaign_id == campaign_id, CharacterRelationship.kind.in_(PERSONAL_KINDS),
        or_(CharacterRelationship.source_id == player, CharacterRelationship.target_id == player))
        .order_by(CharacterRelationship.id))
    return _npcs_only(e for pair in rows for e in pair if e != player)
