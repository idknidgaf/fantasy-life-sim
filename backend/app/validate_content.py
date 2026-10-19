"""Check every content file:  python -m app.validate_content"""
import sys

from .content import ContentError, load_content


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
    return 0


if __name__ == "__main__":
    sys.exit(main())
