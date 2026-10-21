"""Fantasy name generation. Pure and seedable: no database, no Character, no global state.

    given name = prefix + 0..N middle syllables + suffix          Ael + an + wen   -> Aelanwen
    surname    = prefix + suffix                                  Silver + song    -> Silversong

The rules live in data/names.yaml, one culture per species family, so a new culture or species needs no code.
A character's identity is its unique id (see npc.py), never its name: two characters may share a display name.
Uniqueness is opt-in. Pass `taken=` to `NameGenerator.generate` when a name must not repeat in some scope (a
village, a family, a campaign); the generator then redraws, and raises `NameExhaustedError` if the culture simply
cannot produce another unused name.

Reproducible: all randomness comes from the `random.Random` you pass in, so the same seed gives the same names.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Collection, Iterable

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .content import DATA_DIR, ContentError

NAMES_FILE = DATA_DIR / "names.yaml"

MAX_CONSONANT_RUN = 3        # "Thrx" is fine, "Ghrmbl" is not
MAX_VOWEL_RUN = 2
VOWELS = set("aeiouy")
ATTEMPTS = 200               # redraws before giving up on a unique name


class NameExhaustedError(RuntimeError):
    """A culture cannot produce another name that is not already taken."""


# ---------------------------------------------------------------- configuration

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _letters(values: Iterable[str], where: str) -> list[str]:
    bad = [v for v in values if not v or not v.isalpha()]
    if bad:
        raise ValueError(f"{where}: name parts must be non-empty letters only, got {bad}")
    return list(values)


class NameParts(_Strict):
    """The building blocks of one kind of name (given name or surname)."""
    prefixes: list[str] = Field(min_length=1)
    syllables: list[str] = []
    suffixes: list[str] = Field(min_length=1)
    middle: tuple[int, int] = (0, 0)                    # min and max middle syllables
    length: tuple[int, int] = (3, 14)                   # min and max letters

    @model_validator(mode="after")
    def _check(self):
        _letters(self.prefixes, "prefixes")
        _letters(self.syllables, "syllables")
        _letters(self.suffixes, "suffixes")
        lo, hi = self.middle
        if not 0 <= lo <= hi:
            raise ValueError("middle must be [min, max] with 0 <= min <= max")
        if hi > 0 and not self.syllables:
            raise ValueError("middle asks for syllables, but there are none")
        lo, hi = self.length
        if not 1 <= lo <= hi:
            raise ValueError("length must be [min, max] with 1 <= min <= max")
        shortest = min(map(len, self.prefixes)) + min(map(len, self.suffixes))
        if shortest > hi:
            raise ValueError(f"length max {hi} is below the shortest possible name ({shortest} letters)")
        return self


class SurnameParts(NameParts):
    chance: float = Field(default=0.0, ge=0, le=1)       # probability that a generated name has a surname


class CultureNames(_Strict):
    given: NameParts
    surname: SurnameParts | None = None


class NameConfig(_Strict):
    default_culture: str
    species_cultures: dict[str, str] = {}
    cultures: dict[str, CultureNames] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self):
        if self.default_culture not in self.cultures:
            raise ValueError(f"default_culture '{self.default_culture}' is not a defined culture")
        for species, culture in self.species_cultures.items():
            if culture not in self.cultures:
                raise ValueError(f"species_cultures.{species}: unknown culture '{culture}'")
        return self


def load_name_config(species_ids: Iterable[str] | None = None, path: Path = NAMES_FILE) -> NameConfig:
    """Load and validate names.yaml. With `species_ids`, species named in the file must really exist."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ContentError([f"{path.name}: could not be read: {e}"])
    try:
        cfg = NameConfig.model_validate(raw)
    except ValidationError as e:
        raise ContentError([f"{path.name}: {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()])
    if species_ids is not None:
        known = set(species_ids)
        unknown = sorted(set(cfg.species_cultures) - known)
        if unknown:
            raise ContentError([f"{path.name}: species_cultures names unknown species {unknown}"])
    return cfg


@lru_cache
def get_name_config() -> NameConfig:
    from .content import get_content
    return load_name_config(get_content().species)


# ---------------------------------------------------------------- generation

@dataclass(frozen=True)
class GeneratedName:
    given: str
    surname: str | None = None

    @property
    def full(self) -> str:
        return f"{self.given} {self.surname}" if self.surname else self.given

    def __str__(self) -> str:
        return self.full


def _join(parts: list[str]) -> str:
    """Glue the parts together and tidy the seams: three identical letters in a row collapse to two."""
    out = ""
    for part in parts:
        for ch in part.lower():
            if len(out) >= 2 and out[-1] == ch and out[-2] == ch:
                continue
            out += ch
    return out.capitalize()


def _readable(name: str) -> bool:
    consonants = vowels = 0
    for ch in name.lower():
        if ch in VOWELS:
            vowels, consonants = vowels + 1, 0
        else:
            consonants, vowels = consonants + 1, 0
        if consonants > MAX_CONSONANT_RUN or vowels > MAX_VOWEL_RUN:
            return False
    return True


def _build(parts: NameParts, rng: random.Random) -> str | None:
    """One attempt. None when the result breaks the length or readability rules (the caller redraws)."""
    pieces = [rng.choice(parts.prefixes)]
    lo, hi = parts.middle
    for _ in range(rng.randint(lo, hi) if hi else 0):
        pieces.append(rng.choice(parts.syllables))
    suffix = rng.choice(parts.suffixes)
    if suffix.lower() == pieces[0].lower():                # "Leafleaf"
        return None
    pieces.append(suffix)
    name = _join(pieces)
    if not parts.length[0] <= len(name) <= parts.length[1] or not _readable(name):
        return None
    return name


class NameGenerator:
    def __init__(self, config: NameConfig):
        self.config = config

    def culture_for(self, species_id: str | None) -> str:
        return self.config.species_cultures.get(species_id or "", self.config.default_culture)

    def _draw(self, parts: NameParts, rng: random.Random) -> str:
        for _ in range(ATTEMPTS):
            name = _build(parts, rng)
            if name:
                return name
        raise NameExhaustedError("could not build a readable name from these parts")     # pragma: no cover

    def generate(self, *, rng: random.Random, species: str | None = None, culture: str | None = None,
                 surname: str | None = None, with_surname: bool | None = None,
                 taken: Collection[str] | None = None) -> GeneratedName:
        """Make one name.

        culture / species  which rules to use (culture wins); an unknown species uses the default culture
        surname            use exactly this family name (for example a relative's) instead of drawing one
        with_surname       force a surname on or off; None uses the culture's own chance
        taken              full names that must not be repeated (compared ignoring case). Omit it and names may
                           repeat: that is allowed, identity is the character's id, not its name.
        """
        culture = culture or self.culture_for(species)
        if culture not in self.config.cultures:
            raise KeyError(f"unknown name culture '{culture}'")
        rules = self.config.cultures[culture]
        used = {t.casefold() for t in taken} if taken is not None else None

        for attempt in range(ATTEMPTS):
            given = self._draw(rules.given, rng)
            family = surname
            if family is None and rules.surname is not None:
                # Past the halfway mark a surname is added regardless of chance, to widen a crowded name space.
                want = with_surname if with_surname is not None else rng.random() < rules.surname.chance
                if want or (used is not None and with_surname is None and attempt >= ATTEMPTS // 2):
                    family = self._draw(rules.surname, rng)
            elif family is None and with_surname:
                raise ValueError(f"culture '{culture}' has no surnames")
            name = GeneratedName(given, family)
            if used is None or name.full.casefold() not in used:
                return name
        raise NameExhaustedError(f"no unused name left for culture '{culture}' ({len(used or ())} taken)")

    def generate_many(self, count: int, *, rng: random.Random, unique: bool = False, **kw) -> list[GeneratedName]:
        """`count` names. With unique=True no two (and none in `taken`) are the same."""
        taken = set(kw.pop("taken", None) or [])
        out: list[GeneratedName] = []
        for _ in range(count):
            name = self.generate(rng=rng, taken=taken if unique else None, **kw)
            out.append(name)
            taken.add(name.full)
        return out


@lru_cache
def get_name_generator() -> NameGenerator:
    return NameGenerator(get_name_config())
