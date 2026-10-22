"""Check every content file:  python -m app.validate_content"""
import sys

from .content import ContentError, load_content
from .combat import load_combat_config
from .inheritance import load_inheritance_rules
from .names import load_name_config
from .npc import load_named_characters
from .power import load_power_config
from .story.errors import StoryContentError
from .story.library import load_library


def main() -> int:
    try:
        ct = load_content()
    except ContentError as e:
        print("CONTENT INVALID\n" + str(e))
        return 1
    print(
        f"OK: {len(ct.species)} species, {len(ct.traits)} traits, {len(ct.talents)} talents, "
        f"{len(ct.events)} events, {len(ct.items)} items, {len(ct.skills)} skills, "
        f"{len(ct.stages)} life stages"
    )
    for w in ct.warnings:
        print(f"  warning: {w}")
    try:
        power = load_power_config(ct)
    except ContentError as e:
        print("POWER CONFIG INVALID\n" + str(e))
        return 1
    print(f"OK: power.yaml ({len(power.specialised_stats)} specialised stats, {len(power.skill_power)} skill bonuses)")
    try:
        cfg = load_combat_config()
    except ContentError as e:
        print("COMBAT CONFIG INVALID\n" + str(e))
        return 1
    print(f"OK: combat.yaml (power floor {cfg.power_floor:g}, win chance {cfg.win_chance_floor:.0%}-{1 - cfg.win_chance_floor:.0%})")
    try:
        names = load_name_config(ct.species)
    except ContentError as e:
        print("NAMES CONFIG INVALID\n" + str(e))
        return 1
    print(f"OK: names.yaml ({len(names.cultures)} cultures)")
    try:
        named = load_named_characters(ct, power)
    except ContentError as e:
        print("NAMED CHARACTERS INVALID\n" + str(e))
        return 1
    print(f"OK: named_characters.yaml ({len(named)} named characters)")
    try:
        rules = load_inheritance_rules(ct, power)
    except ContentError as e:
        print("INHERITANCE RULES INVALID\n" + str(e))
        return 1
    print(f"OK: inheritance.yaml ({len(rules.crossings)} species crossings)")
    try:
        story = load_library(content=ct, named=named)
    except StoryContentError as e:
        print("STORY CONTENT INVALID\n" + str(e))
        return 1
    print(f"OK: {len(story)} story engine events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
