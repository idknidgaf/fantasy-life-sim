"""Surname inheritance. Pure: no database. Separate from character ids (an id never contains a name) and from the
fantasy name generator (names.py draws names; this only decides WHICH surname a child should carry).

    1. A culture with no family names (a wolf, a slime) gives its children none, whatever the parents' names.
    2. `mode` (data/inheritance.yaml `naming.surname`, or per culture) picks among the available parents who have a
       surname: first_parent (the first listed, the default), random_parent (seeded), or none.
    3. A parent of another naming culture is skipped when `cross_culture: fallback`.
    4. With nothing to inherit, `fallback: generate` lets the name generator decide by its culture's own chance, and
       `fallback: none` goes without.

A missing parent, a parent with a single name, a blank surname and an unknown culture are all handled: they simply
offer nothing to inherit.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence

from .inheritance import InheritanceRules, ParentSource
from .names import NameGenerator


@dataclass(frozen=True)
class SurnameChoice:
    surname: str | None             # the family name to use exactly, or None
    with_surname: bool | None       # when surname is None: False = none at all, None = let the culture's chance decide
    source: str | None = None       # the parent ref it came from, if inherited


def derive_surname(full_name: str | None) -> str | None:
    """The family name of a stored full name, for characters saved without one: the last word of a multi-word name."""
    parts = (full_name or "").split()
    return parts[-1] if len(parts) >= 2 else None


def parent_surname(stored: str | None, full_name: str | None) -> str | None:
    """A character's family name: the stored one if any, else derived from the name. Blank counts as none."""
    return (stored or "").strip() or derive_surname(full_name)


def _has_surnames(names: NameGenerator, culture: str) -> bool:
    rules = names.config.cultures.get(culture)
    return rules is not None and rules.surname is not None and rules.surname.chance > 0


def choose_surname(parents: Sequence[ParentSource], child_species: str, seed: int, rules: InheritanceRules,
                   names: NameGenerator, culture: str | None = None) -> SurnameChoice:
    cfg = rules.naming.surname
    culture = culture or names.culture_for(child_species)
    if not _has_surnames(names, culture):
        return SurnameChoice(None, False)
    mode = cfg.cultures.get(culture, cfg.mode)
    candidates = [p for p in parents if p.surname]
    if cfg.cross_culture == "fallback":
        candidates = [p for p in candidates if names.culture_for(p.character.species_id) == culture]
    if mode != "none" and candidates:
        pick = (candidates[0] if mode == "first_parent"
                else random.Random(f"{seed}:inherit:surname").choice(candidates))
        return SurnameChoice(pick.surname, None, pick.ref)
    return SurnameChoice(None, None if cfg.fallback == "generate" else False)
