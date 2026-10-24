# Inheritance and descendants

Creates a child from one or two existing characters. Code: `backend/app/inheritance.py` (the rules, pure) and
`descendants.py` (creating and saving the child). Rules and numbers: `backend/app/data/inheritance.yaml`, validated by
`python -m app.validate_content`. Not part of this system: death handling, switching the playable character, choosing
an heir, authored named characters.

## What it does

Inheritance only decides a child's **starting aptitude**. It builds an `NpcSpec` (species, exact base stats, a small
head start in the specialised stats, inherited traits, a talent) and hands it to the ordinary `NpcFactory`, which makes
the child exactly like any other persistent NPC: its own unique id, a generated name, its own age. The child is saved
with `save_npc` and linked with `add_parent`, together or not at all. Generic NPC generation is untouched.

```python
from app.descendants import create_descendant, descendant_seed
result = create_descendant(session, campaign_id, [mum_id, dad_id], seed=descendant_seed(family_seed, 0))
result.npc          # the saved child        result.parents   # the parents actually used (stable ids)
result.skipped      # parents that were unavailable, with the reason
result.report       # what was inherited, and from whom
```

Parents are stable ids: an NPC's id, or `pc_<n>` for the playable character. Use a different seed for each child
(`descendant_seed` derives one per index). Nothing is committed.

## The rules (all in `inheritance.yaml`)

| | Rule |
|---|---|
| **Parents** | Each must have reached `min_stage` (default young adult, scaled to their species), and the same character cannot be both parents. Two parents cannot be in the same direct line (a parent and their own ancestor or descendant); switch off with `forbid_direct_line`. `cannot_parent` bars species. The child must be younger than both. |
| **Unavailable parents** | A parent who cannot be found in the campaign, or is only a temporary NPC, is skipped and reported; the child is made from whoever is left, and it is an error if nobody is. A parent who exists but is not allowed (above) is an error. Whether a parent is alive is not checked. |
| **Species** | Same species gives that species; one parent gives that parent's species. Two different species only if the pair is listed under `crossings` (order does not matter), which names the possible offspring with weights. Shipped: human with elf and human with dwarf, each giving either parent's species. Everything else is refused. |
| **Base stats** | `child = (1 - h) * normal newborn roll + h * parent aptitude, +/- variation`, with `h` = `heritability` (0.4). Parent aptitude is the parent's stat minus their species bonus, averaged over the parents who have it. The result is kept within `start_bounds` (10 to 75), then the child's own species modifiers apply and its species cap is enforced. So children lean toward their parents without copying them, and a strong parent does not produce a prodigy. |
| **Specialised stats** | Magic, Swordsmanship, Marksmanship, Martial Arts: a head start of `fraction` (10%) of the parents' average level, at most `max_start`, and only if that average reaches `min_parent_level`. Only skills the child's species can train. Power is still calculated by the Power system from these skills; nothing else is added. |
| **Traits** | Eligible if inheritable, and not in `exclude` (which always wins). Inheritable means: the trait's own `inheritance.inheritable` in `traits.yaml` if it sets one (`false` beats even `extra`); otherwise it can be present at birth (has a `birth:` rule) or is in `extra`. By default that is iron constitution, fleet footwork and keen sense; traits only gained through life (`acquire:` rules, achievements) are not inherited unless listed in `extra`. Chance per parent, rolled separately: `overrides` in `inheritance.yaml`, else the trait's own `inheritance.chance`, else the rarity tier (`chance`). At most `max_inherited`. A trait's `prerequisites` must hold for the child. A species' own traits are not counted. A trait's origin (`birth`, `acquired`, `both`, `granted`) is `inheritance.trait_origin`. |
| **Talents** | Each parent with a hidden talent passes the same talent on with `inherit_chance` (10%), only if the child is eligible for it. At most one, always dormant. With nothing inherited the child rolls like a newborn (`base_chance`, 1%). |
| **Surname** | Separate from ids and from the name generator (`surnames.py`, `naming.surname` in `inheritance.yaml`). A culture with no family names (wolf, slime) never gives one. Otherwise `mode` picks an available parent's surname: `first_parent` (default), `random_parent` (seeded) or `none`, overridable per culture (`cultures`). `cross_culture: fallback` skips a parent of another naming culture. With nothing to inherit, `fallback: generate` lets the culture's own chance decide and `fallback: none` goes without. A parent's surname is the stored `Npc.surname`, else the last word of a multi-word name; single names and blank surnames offer nothing. The given name is always generated. |
| **Parents' relation** | Sibling, half-sibling and cousin parents are identified (`Descendant.parent_relations`) but allowed. A game rule can forbid them: `parents.forbidden_relations: [sibling, half_sibling, cousin]` (empty by default). |
| **Everything else** | Independent. Items, titles, achievements, history, developed skills and personality are not inherited; an NPC does not even carry most of them. Anything like that needs its own explicit rule. |

## Inheritance is not development

A child's stats are newborn aptitude (`NpcSpec.birth_stats`). They are not the parents' developed stats, and the parents'
developed (non-specialised) skills never pass down. Awakened talents become dormant potential. A child created at an age
above 0 is developed ONCE from those birth stats by the NPC progression system (`progression.initial_development`, see
`NPC_PROGRESSION.md`) and recorded as developed for that age, so the yearly world tick never grows it a second time.

## Reproducible and retry-safe

Each part (species, stats, skills, traits, talent) draws from its own stream `Random(f"{seed}:inherit:<part>")`, so the
same parents and seed give the same child, and tuning one rule never changes another's dice. Because the child's id comes
from the seed, repeating a call returns the already-saved child (`created=False`); reusing a seed for different parents
is refused.

## Known limits

* The shipped rules allow only human with elf and human with dwarf. Children are always an existing species: there are no
  hybrid species.
* A child born into the family the player knows is recorded as known (`known.py`); a child of strangers is not.
* A newborn's small specialised head start does count toward its Power through the normal calculation; set
  `specialised.fraction: 0` to turn it off.
* Family queries and validation: `family.py` (see `NPC_API.md`).
