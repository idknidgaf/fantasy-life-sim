"""Write the machine-readable story schema:  python -m app.story.export_schema  (-> docs/story_event.schema.json)"""
import json
from pathlib import Path

from . import conditions, effects, triggers  # noqa: F401  (registers the built-in types)
from .schema import EventFile

OUT = Path(__file__).resolve().parents[3] / "docs" / "story_event.schema.json"


def build() -> dict:
    return {
        "event_file": EventFile.model_json_schema(),
        "condition_types": conditions.conditions.catalog(),
        "condition_composites": {"all": "list of conditions, all true", "any": "list of conditions, one true",
                                 "not": "a single condition, negated"},
        "effect_types": effects.effects.catalog(),
        "trigger_types": triggers.triggers.catalog(),
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
