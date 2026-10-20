"""A small generic registry: named, parameter-validated operations.

Conditions, effects and triggers are all registered the same way. Adding a new type is one
decorated function plus a parameter model; no event definition and no engine code changes.

    @conditions.register("has_gold", HasGoldParams)
    def _has_gold(p: HasGoldParams, ctx) -> bool: ...
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, ValidationError


class StrictParams(BaseModel):
    """Base for parameter models: unknown parameters are rejected, not ignored."""
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Spec:
    name: str
    params: type[BaseModel]
    handler: Callable[..., Any]
    doc: str


class Registry:
    def __init__(self, kind: str):
        self.kind = kind
        self._specs: dict[str, Spec] = {}

    def register(self, name: str, params: type[BaseModel] = StrictParams, *, doc: str = ""):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            if name in self._specs:
                raise ValueError(f"{self.kind} type already registered: {name}")
            self._specs[name] = Spec(name, params, fn, doc or (fn.__doc__ or "").strip())
            return fn
        return decorator

    def names(self) -> list[str]:
        return sorted(self._specs)

    def get(self, name: str) -> Spec:
        return self._specs[name]

    def __contains__(self, name: str) -> bool:
        return name in self._specs

    def parse(self, raw: Any) -> tuple[Spec, BaseModel]:
        """Validate one raw `{"type": ..., **params}` mapping. Raises ValueError with a clear message."""
        if not isinstance(raw, dict):
            raise ValueError(f"a {self.kind} must be an object, got {type(raw).__name__}")
        name = raw.get("type")
        if not isinstance(name, str):
            raise ValueError(f"a {self.kind} needs a string 'type' (known: {', '.join(self.names())})")
        if name not in self._specs:
            raise ValueError(f"unknown {self.kind} type '{name}' (known: {', '.join(self.names())})")
        spec = self._specs[name]
        try:
            params = spec.params.model_validate({k: v for k, v in raw.items() if k != "type"})
        except ValidationError as e:
            detail = "; ".join(f"{'.'.join(str(p) for p in err['loc']) or 'params'}: {err['msg']}" for err in e.errors())
            raise ValueError(f"invalid {self.kind} '{name}': {detail}") from None
        return spec, params

    def normalize(self, raw: Any) -> dict:
        spec, params = self.parse(raw)
        return {"type": spec.name, **params.model_dump(mode="json")}

    def catalog(self) -> dict[str, dict]:
        """Machine-readable description of every registered type (used by the schema export)."""
        return {n: {"doc": s.doc, "params": s.params.model_json_schema()} for n, s in sorted(self._specs.items())}
