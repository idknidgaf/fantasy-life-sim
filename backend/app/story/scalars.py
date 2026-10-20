"""Flag values are plain JSON scalars. Comparison is type-aware so True never equals 1."""
from __future__ import annotations

from typing import Union

Scalar = Union[bool, int, float, str]


def is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def same_value(a: object, b: object) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b
