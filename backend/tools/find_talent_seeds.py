"""Find seeds whose newborn carries a given hidden talent, so you can test the awakening system.

    python -m tools.find_talent_seeds                 # every species x talent
    python -m tools.find_talent_seeds slime           # one species
    python -m tools.find_talent_seeds human master_of_none

Use the printed seed in the Seed box when starting a new life (pick the same species).
The talent stays hidden in the game until it awakens; this tool simply looks at the birth roll.
"""
import sys

from app.content import get_content
from app.engine import new_character


def find(species: str, talent: str | None, limit: int = 100_000) -> dict[str, int]:
    found: dict[str, int] = {}
    wanted = {talent} if talent else set(get_content().talents)
    for seed in range(limit):
        t = new_character("x", species, seed).hidden_talent
        if t in wanted and t not in found:
            found[t] = seed
            if len(found) == len(wanted):
                break
    return found


def main(argv: list[str]) -> None:
    ct = get_content()
    species = [argv[0]] if argv else list(ct.species)
    talent = argv[1] if len(argv) > 1 else None
    for sp in species:
        print(f"{ct.species[sp].name}:")
        for t, seed in find(sp, talent).items():
            print(f"  seed {seed:>6}  ->  {ct.talents[t].name}")


if __name__ == "__main__":
    main(sys.argv[1:])
