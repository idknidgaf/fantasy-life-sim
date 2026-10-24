"""The three groups of the Character tab, derived from data that already exists (no stored copies of the groups).

  family          NPCs connected to the player through family links (parent, child, sibling, half-sibling, grandparent,
                  cousin, further ancestors and descendants: family.family_of), and known to the player. Independent of
                  any personal relationship.
  acquaintances   NPCs (persistent or named) the player has personally met and holds an established relationship with
                  (awareness.py). Only an explicit registration creates one; a family link, a friend link made by a
                  definition, or being mentioned does not.
  notable         NAMED NPCs the player knows of (known.py): introduced or revealed by a story, encountered, or linked to.
                  No personal relationship is needed, and one ending never removes it.

Awareness, family and personal relationship are three separate states. An NPC may be in several groups (a named NPC
who is also a friend is Notable AND an Acquaintance); within one group it appears once (sets of ids, and a SQL query over
one row per NPC). Read-only.
"""
from __future__ import annotations

from typing import Literal

from sqlmodel import Session

from . import family as fam
from .awareness import acquainted_ids  # noqa: F401  (the acquaintance source of truth, re-exported for the API)

Category = Literal["family", "acquaintances", "notable"]
CATEGORIES: tuple[Category, ...] = ("family", "acquaintances", "notable")

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
