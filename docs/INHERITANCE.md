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
| **Traits** | Eligible if the trait can be present at birth (it has a `birth:` rule) or is in `extra`, and is not in `exclude`. By default that means iron constitution, fleet footwork and keen sense, and not achievement traits. Each parent holding an eligible trait passes it on with the chance for its rarity tier (`chance`), or its own entry in `overrides`, rolled separately per parent. At most `max_inherited`. A species' own traits are not counted. |
| **Talents** | Each parent with a hidden talent passes the same talent on with `inherit_chance` (10%), only if the child is eligible for it. At most one, always dormant. With nothing inherited the child rolls like a newborn (`base_chance`, 1%). |
| **Everything else** | Independent. Items, titles, achievements, history, developed skills and personality are not inherited; an NPC does not even carry most of them. Anything like that needs its own explicit rule. |

## Inheritance is not development

A child's stats are newborn aptitude. They are not the parents' developed stats, and the parents' developed
(non-specialised) skills never pass down. Awakened talents become dormant potential. Growth after birth is left to normal
gameplay.

## Reproducible and retry-safe

Each part (species, stats, skills, traits, talent) draws from its own stream `Random(f"{seed}:inherit:<part>")`, so the
same parents and seed give the same child, and tuning one rule never changes another's dice. Because the child's id comes
from the seed, repeating a call returns the already-saved child (`created=False`); reusing a seed for different parents
is refused.

## Known limits

* The shipped rules allow only human with elf and human with dwarf. Children are always an existing species: there are no
  hybrid species.
* NPCs do not age or grow yet, so a child created at an older age still has newborn-range stats.
* The child's name is generated for its species and does not take a parent's surname.
* No API endpoints or UI. Only a direct line is blocked between parents (siblings and cousins are not checked).
* A newborn's small specialised head start does count toward its Power through the normal calculation; set
  `specialised.fraction: 0` to turn it off.
